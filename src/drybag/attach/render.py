"""The overlay's executable parts, rendered into a local plugin named by the binding id.

**What runs is this directory, in place.** `claude plugin install --scope local` keeps a cache
copy, but a session runs the plugin from its marketplace's own directory, `<data>/delivery/<id>/`:
an edit there reaches the next session with no reinstall, and with the directory gone the plugin
does not load and nothing says so. So `attach` re-renders here on every run, and the directory is
always derived from the id, never read from a record a checkout keeps.

**The names are the defence.** Claude Code records a local-scope marketplace machine-wide, and a
clone that commits an `enabledPlugins` entry naming a plugin this machine knows runs its hooks. So
the marketplace is `drybag-delivery-<id>` and the plugin `overlay-<id>`, each carrying the whole
binding id, which never leaves this machine (`drybag.binding`): a clone cannot name what it never
saw. No other name of a delivered part is predictable, and none of these is ever printed.

**What is rendered.** The union of the `claude/hooks.json` of each plate of the overlay
(`config.plates`: `common/`, the project's tags in list order, `projects/<project>/`), each event's
entry groups in plate order, as the overlay wrote them; and the skills and agents of the same
directories, each copied whole from the plate that wins it (`attach.items`), so no file of a
losing plate's skill reaches the render. `<project>` is the project this machine's binding record
names, which the caller passes. Every source is read before anything is written (`read_parts`),
so a file that cannot be read stops the run while the render is as it was.

**The overlay's path-scoped rules** (`attach.rules`) add one entry: on `PostToolUse`, matching
`Read|Edit|Write`, `hook PostToolUse` run through whatever launched the drybag that renders
(`scoped_command`), by absolute paths, since the render is no part of the core plugin and has no
`${CLAUDE_PLUGIN_ROOT}` naming it. Run from the core plugin, or a checkout of it, that is the
plugin's own hook wrapper, `hooks/run-hook.sh` beside the running module's `src/`, which runs the
interpreter with `-I` and answers any fault with exit 0, as for the core plugin's own entries; run
from an installed package, which has no plugin root, it is the running interpreter with
`-I -m drybag`. Only what the running process knows about itself is asked. The entry is rendered
only when the overlay holds a rule with `paths:`. No entry is added to the core plugin's
`hooks/hooks.json`.

**The digest is a convenience, not a gate.** It is sha256 over every rendered file's path relative
to the render directory and its bytes, which the ledger keeps beside those paths so a later run can
say what changed and take back what the overlay no longer holds. The overlay is the owner's, and
its hooks run scripts from the overlay checkout in place, so a digest of this text would not even
cover what runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import drybag
from drybag import fsops
from drybag.attach.items import AGENT, SKILL, fold
from drybag.attach.permissions import HOOKS_FILE, claude_sources, hook_groups
from drybag.attach.rules import scoped_exist
from drybag.binding import delivery_directory, short, unwritten
from drybag.config.plates import Plate
from drybag.errors import Failure
from drybag.printed import quoted

MARKETPLACE_PREFIX = "drybag-delivery-"
PLUGIN_PREFIX = "overlay-"
# The plugin's directory inside the marketplace, as the marketplace's `source` names it.
PLUGIN_DIRECTORY = "plugin"
MARKETPLACE_FILE = ".claude-plugin/marketplace.json"
PLUGIN_FILE = f"{PLUGIN_DIRECTORY}/.claude-plugin/plugin.json"
# The plugin's hook file, by its path inside the plugin directory.
HOOK_FILE = "hooks/hooks.json"
# The directories of the two kinds of item copied whole from the plate that wins each, beside the
# hook file.
COPIED = (SKILL.directory, AGENT.directory)
# The entry that delivers path-scoped rules: the event, the tools whose input names a file, and the
# core plugin's hook wrapper, by its path in the plugin root, beside `src/`.
SCOPED_EVENT = "PostToolUse"
SCOPED_MATCHER = "Read|Edit|Write"
WRAPPER = "hooks/run-hook.sh"
# The core plugin's own hook timeout, in seconds (`hooks/hooks.json`).
SCOPED_TIMEOUT = 10
_DESCRIPTION = "The hooks, skills and agents of your drybag overlay, rendered for this checkout."

# The two `claude plugin` calls that make the render a plugin of this checkout, run from the main
# checkout, whose `settings.local.json` Claude Code reads from every worktree: `SLOT` is filled with
# the render directory and with `overlay-<id>@drybag-delivery-<id>`.
SLOT = "<slot>"
PLUGIN_MARKETPLACE_ADD_LOCAL = ("claude", "plugin", "marketplace", "add", SLOT, "--scope", "local")
PLUGIN_INSTALL_LOCAL = ("claude", "plugin", "install", SLOT, "--scope", "local")
# The two that `detach` takes it back with, from the same checkout: `SLOT` is filled with
# `overlay-<id>@drybag-delivery-<id>` and with `drybag-delivery-<id>`. The marketplace is
# removed from every scope, since Claude Code records it machine-wide and no other scope names it.
PLUGIN_UNINSTALL_LOCAL = ("claude", "plugin", "uninstall", SLOT, "--scope", "local")
PLUGIN_MARKETPLACE_REMOVE = ("claude", "plugin", "marketplace", "remove", SLOT)


class Part(NamedTuple):
    """One file the plugin carries: its text, and the permission bits it is written with, `None`
    for a file drybag generates, which keeps the mode it has or is made with the default."""

    text: str
    mode: int | None = None


@dataclass(frozen=True)
class Rendered:
    """What the attach ledger keeps of a render: its digest, never the id and never a path on this
    machine, since the directory is always `delivery_directory(id)`."""

    digest: str


@dataclass(frozen=True)
class RenderRecord:
    """One render: the id it is named by and the digest over its files."""

    id: str
    digest: str

    @property
    def rendered(self) -> Rendered:
        return Rendered(self.digest)


def marketplace_name(id: str) -> str:
    return f"{MARKETPLACE_PREFIX}{id}"


def plugin_name(id: str) -> str:
    return f"{PLUGIN_PREFIX}{id}"


def filled(table: tuple[str, ...], value: str) -> list[str]:
    """`table`'s argv with its `SLOT` replaced by `value`."""
    return [value if word == SLOT else word for word in table]


def _unreadable(sentence: str) -> Failure:
    return Failure(f"{sentence}, so nothing was written")


def _copied(item: Path) -> dict[str, Part]:
    """Every regular file of `item`, an entry of a plate's `skills/` or `agents/` that is a
    directory or a file, by its path relative to the directory holding it, its text with its own
    line endings and its permission bits, so a skill's script stays executable; dot-named files
    and directories below it are left out. The overlay is the owner's, so a path in it prints,
    through `quoted` since a project's directory carries `project.name`."""

    def refuse(error: OSError) -> None:
        raise error

    found: dict[str, Part] = {}
    with fsops.reading(quoted(str(item)), _unreadable):
        walked: Iterable[tuple[str, list[str], list[str]]] = (
            os.walk(item, onerror=refuse)
            if fsops.is_dir(item)
            else [(str(item.parent), [], [item.name])]
        )
        for directory, names, files in walked:
            # Pruned in place, which is how `os.walk` is told not to enter a directory.
            names[:] = sorted(name for name in names if not name.startswith("."))
            for name in sorted(files):
                path = Path(directory) / name
                if name.startswith(".") or not fsops.is_file(path):
                    continue
                with fsops.reading(quoted(str(path)), _unreadable):
                    text = fsops.read_regular_text(path, newline="")
                    # The permission bits alone: a set-id or sticky bit is not the overlay's to
                    # hand to a file under this machine's data directory.
                    mode = stat.S_IMODE(path.stat().st_mode) & 0o777
                found[path.relative_to(item.parent).as_posix()] = Part(text, mode)
    return found


def _quoted(path: str) -> str:
    """`path` in double quotes, as the core plugin's entries quote theirs, with the four characters
    a shell still reads inside them escaped, so it arrives as one word, unexpanded."""
    for char in '\\"$`':
        path = path.replace(char, f"\\{char}")
    return f'"{path}"'


def scoped_command() -> str | None:
    """`hook PostToolUse` run through whatever launched this drybag: the plugin root's hook
    wrapper where the running module's `src/` has one beside it, as `scripts/drybag` puts that
    `src/` on `sys.path`; else the running interpreter, isolated; `None` where the process cannot
    name its interpreter."""
    wrapper = Path(drybag.__file__).resolve().parents[2] / WRAPPER
    if fsops.is_file(wrapper):
        return f"{_quoted(str(wrapper))} hook {SCOPED_EVENT}"
    if sys.executable:
        return f"{_quoted(sys.executable)} -I -m drybag hook {SCOPED_EVENT}"
    return None


def launcher(command: str) -> str | None:
    """The executable `command` runs, where it is one `scoped_command` wrote: its first word, in
    the double quotes `_quoted` puts around it, unescaped; `None` for any other command."""
    if not command.startswith('"'):
        return None
    word: list[str] = []
    escaped = False
    for char in command[1:]:
        if escaped:
            word.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return "".join(word)
        else:
            word.append(char)
    return None


def recorded_command(directory: Path) -> str | None:
    """The path-scoped rules' command in the render at `directory`: the last `SCOPED_EVENT` group
    matching `SCOPED_MATCHER` whose one entry runs `hook SCOPED_EVENT`, as `scoped_group` writes
    it; `None` where the render holds none, or its hook file cannot be read. The render is this
    machine's own file, written only by `write`."""
    try:
        document = json.loads(fsops.read_regular_text(directory / PLUGIN_DIRECTORY / HOOK_FILE))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    hooks = document.get("hooks") if isinstance(document, dict) else None
    groups = hooks.get(SCOPED_EVENT) if isinstance(hooks, dict) else None
    found = None
    for group in groups if isinstance(groups, list) else ():
        entries = group.get("hooks") if isinstance(group, dict) else None
        if group.get("matcher") != SCOPED_MATCHER or not isinstance(entries, list):
            continue
        if len(entries) == 1 and isinstance(entries[0], dict):
            command = entries[0].get("command")
            if isinstance(command, str) and command.endswith(f" hook {SCOPED_EVENT}"):
                found = command
    return found


def scoped_group(command: str) -> dict[str, object]:
    """The `PostToolUse` entry group that runs `command` on every file a session touches."""
    return {
        "matcher": SCOPED_MATCHER,
        "hooks": [{"type": "command", "timeout": SCOPED_TIMEOUT, "command": command}],
    }


def read_parts(held: Sequence[Plate], *, command: str | None = None) -> dict[str, Part]:
    """What the plates `held` give the plugin, by path relative to the plugin directory, read and
    nothing written: the hook file's union, the path-scoped rules' entry where there is one, and the
    copied directories. `command` is the path-scoped rules' command, this process's own
    (`scoped_command`) where it is `None`: `doctor` passes the one the render holds, so a report
    run through another launcher than `attach` was reads no drift for that alone."""
    hooks: dict[str, list[object]] = {}
    for source, owner in claude_sources(held, HOOKS_FILE):
        for event, groups in hook_groups(source, share=owner).items():
            hooks.setdefault(event, []).extend(groups)
    # The rules are read whether or not there is a command, so a rule that cannot be read stops
    # every run alike.
    if scoped_exist(held) and (command := command or scoped_command()) is not None:
        hooks.setdefault(SCOPED_EVENT, []).append(scoped_group(command))
    parts = {HOOK_FILE: Part(json.dumps({"hooks": hooks}, indent=2) + "\n")}
    for kind in (SKILL, AGENT):
        for item in fold(held, kind, _unreadable).winners.values():
            for relative, part in _copied(item).items():
                parts[f"{kind.directory}/{relative}"] = part
    return parts


def _digest(files: Mapping[str, Part]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        content = files[relative].text.encode("utf-8")
        digest.update(f"{relative}\0{len(content)}\0".encode())
        digest.update(content)
    return digest.hexdigest()


def _files(id: str, parts: Mapping[str, Part]) -> dict[str, Part]:
    """Every file of the render named `id`, by its path relative to the render directory: the
    marketplace, the plugin's manifest and `parts`."""
    marketplace = {
        "name": marketplace_name(id),
        "owner": {"name": "drybag"},
        "plugins": [
            {
                "name": plugin_name(id),
                "source": f"./{PLUGIN_DIRECTORY}",
                "description": _DESCRIPTION,
            }
        ],
    }
    plugin = {"name": plugin_name(id), "description": _DESCRIPTION}
    return {
        MARKETPLACE_FILE: Part(json.dumps(marketplace, indent=2) + "\n"),
        PLUGIN_FILE: Part(json.dumps(plugin, indent=2) + "\n"),
        **{f"{PLUGIN_DIRECTORY}/{relative}": part for relative, part in parts.items()},
    }


def digest(id: str, parts: Mapping[str, Part]) -> str:
    """The digest `write` would record for `parts` rendered under `id`, with nothing written."""
    return _digest(_files(id, parts))


def write(id: str, parts: Mapping[str, Part]) -> RenderRecord:
    """Render the marketplace, the plugin's manifest and `parts` whole into a new directory beside
    `<data>/delivery/<id>/`, then swap it into place: the directory holds what the overlay holds
    now, whatever an earlier render or a ledger recorded. Each file goes through the walk that
    refuses a symlink; the directory before is removed whole (`remove_tree`)."""
    files = _files(id, parts)
    directory = delivery_directory(id)
    try:
        # The plain-path form for the data directory itself, as `binding.record` writes it.
        directory.parent.mkdir(parents=True, exist_ok=True)
        fresh = Path(tempfile.mkdtemp(prefix=f".{short(id)}-", dir=directory.parent))
        for relative, part in files.items():
            fsops.write_within(fresh, relative, part.text, mode=part.mode)
        before = Path(tempfile.mkdtemp(prefix=f".{short(id)}-", dir=directory.parent))
        if fsops.exists(directory) or fsops.is_symlink(directory):
            os.replace(directory, before / "render")
        os.replace(fresh, directory)
        remove_tree(before)
    except OSError as exc:
        raise unwritten(directory.parent, id, exc) from None
    return RenderRecord(id, _digest(files))


def remove_tree(directory: Path) -> None:
    """Remove `directory` whole, a link in its place by unlinking it, or raise the `OSError`.

    A removal driven by a listing, allowed for the reason the hook sink's is
    (CONTRIBUTING.md#enumerated-writes): the directory is `delivery_directory(id)`, derived from
    the id and never read from the ledger a clone can commit, or a directory `write` made beside it,
    and only `attach` writes inside either. Each entry goes through the contained walk, which
    follows no link: a link inside is unlinked."""

    def refuse(error: OSError) -> None:
        raise error

    if fsops.is_symlink(directory):
        fsops.remove_within(directory.parent, directory.name)
        return
    for current, names, files in os.walk(directory, topdown=False, onerror=refuse):
        here = Path(current).relative_to(directory)
        for name in files:
            fsops.remove_within(directory, (here / name).as_posix())
        for name in names:
            relative = (here / name).as_posix()
            if fsops.is_symlink(directory / relative):
                fsops.remove_within(directory, relative)
            else:
                fsops.rmdir_within(directory, relative)
    fsops.rmdir_within(directory.parent, directory.name)
