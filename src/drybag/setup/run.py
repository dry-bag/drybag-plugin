"""`setup(preset, ...)`: machine setup, once — after drybag is installed, before any attach.

Five things, each reported and each independently skippable, in this order: write the machine
file (the personal defaults the preset names, for whichever of them nothing has recorded yet);
merge the preset's deny rules and the personal values into the machine-scope settings file;
only for the plugins the caller named, register the marketplace the preset declares and install
from it, per configured harness; report whether `drybag` itself resolves on `PATH`; and, only
when the caller named an answer, create, render or record the private overlay.

**A preset default never overwrites a value already recorded.** The first draft of this module
rebuilt `[personal]` from the preset's own defaults on every run, which meant a second
`setup --preset recommended` reset a personal value back to the preset's after the owner set it by
hand — exactly the value `setup.machine`'s own docstring promises survives a rewrite that "only
set one of them". `_new_personal_values` computes only the keys the machine file does not already
carry, and that is what `write_machine` receives — an empty dict on every run after the first,
once every key has a recorded value. The settings file's `pluginConfigs` follows the same rule
from its own side: a value it already holds is kept, whoever wrote it.

**A plugin is installed only when it is named.** A third-party plugin runs in every session of its
harness, its hooks included, and the preset pins none, so `setup --plugin NAME`
installs one and the README recommends them.

**Marketplaces are registered before anything is installed from them, and per-plugin sources are
never guessed.** The first draft attempted `claude plugin install superpowers@obra` on a fresh
machine and always failed there — `obra` and `upstash` are GitHub accounts, not marketplace names,
and every measured successful install in this tree's own spike record
(`docs/plans/2026-09-05-agent-harness-p0-spikes.md`) is preceded by a `marketplace add`. Checked
before writing this fix (`gh api repos/...`, `claude plugin marketplace list` on the machine this
was written on): `superpowers` and `context7` both ship in Anthropic's own official marketplace,
`anthropics/claude-plugins-official`, which a Claude Code install already carries — the
`marketplace add` this module still issues is idempotent defence in depth, not a first registration,
and its own real output confirms that (`✔ Marketplace 'claude-plugins-official' already on disk`).
No non-interactive, non-guessed source could be established for Codex, so nothing is attempted there
for these two plugins; the recommended preset's own `[plugins.claude]` table and the README carry
the reasoning and the recommendation respectively.

**Two harnesses, two verbs, and no unmeasured flags.** Claude Code installs a plugin with `claude
plugin install <name>@<marketplace>`, bare — the spike record's own transcript reports
`(scope: user)` as the *default* a bare install already gets, not something a flag adds, and `-y`
appears in that record only on `plugin uninstall`, never `install`. Codex adds one with
`codex plugin add <name>@<marketplace>` when a marketplace is declared for it — measured, not
assumed, in that same spike record. A harness or a plugin with no declared marketplace gets a note,
never a guessed argv.

**`pluginConfigs` and not a flat top-level key.** Claude Code's own settings reference files a
plugin's non-sensitive `userConfig` answers under `pluginConfigs[<plugin-id>].options`, keyed by
`<plugin-name>@<marketplace-name>` and not by the plugin name alone — confirmed against
`code.claude.com/docs/en/settings-reference` before this was written, because a flatter shape
would round-trip through nothing Claude Code itself reads. `PLUGIN_ID` matches this repository's
own shipped manifest and marketplace names (`tests/test_manifests.py`), which is the pair this
plugin is actually installed under everywhere the preset's own `setup` runs.

**The overlay is validated before anything is written**, in `_requested_overlay`, asked in
`_before_the_first_write` — above the machine file, the settings merge and the plugin installs,
and for `create:` above `gh repo create` itself:

* **a path holding something must be an overlay**, a tree whose identity record names layout 1
  (`overlay.api.overlay_fault`). A path holding nothing (`fsops.vacant`: not there, or an empty
  directory that is no link) is rendered into, so a mistyped path is a new overlay and not a
  refusal, and the run that records a root other than the one the machine file held says so
  (`REPLACED_ROOT`).
* **a `create:` destination must be one `gh` can clone into**: vacant, or the overlay an
  earlier run made. Its place is `home/<name>`, knowable from the arguments
  (`overlay.api.target_root`), so a file there, or a directory holding anything but an overlay, is
  refused before `gh repo create` makes a repository on the owner's account that nothing would
  then record. Naming `create:` is the request, and no flag stands in front of it: a flag a
  model-written command line types as easily as the command consents to nothing.

Where the overlay lives is the owner's choice, and `setup` refuses no place for it: `doctor`'s
`overlay-requires` row warns when another repository's work tree holds it.

**The settings file is written through the home's own symlinks**, so a home managed by stow,
chezmoi or a synced directory gets the file its links lead to: nothing a repository writes is on
that path.
"""

from __future__ import annotations

import datetime
import os
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from drybag import REPOSITORY_URL, __version__, fsops
from drybag.config.machine import in_owner_home
from drybag.config.overlay import overlay_root as recorded_root
from drybag.errors import Failure, Refusal
from drybag.fsops import utf_8_name
from drybag.harnesses import BYTE_ORDER_MARK, HARNESSES, lenient_settings
from drybag.jsonobject import json_object, json_text
from drybag.overlay.api import (
    finish,
    generate,
    overlay_fault,
    render,
    require_overlay,
    target_root,
)
from drybag.presets import defaults_of, load_preset
from drybag.printed import answered, quoted
from drybag.runner import Runner
from drybag.setup.machine import SETUP_SANDBOXED, USER_SETTINGS, read_personal, write_machine
from drybag.trust.api import sandbox_refused

# `<plugin-name>@<marketplace-name>`, matching `.claude-plugin/plugin.json`'s `name` and
# `.claude-plugin/marketplace.json`'s `name` (`tests/test_manifests.py` holds both). Claude
# Code keys `pluginConfigs` by this pair, not by the plugin name alone.
PLUGIN_ID = "drybag@drybag"
# The release tag scheme (`vX.Y.Z`); `uv tool install` has no `--from`, so the positional git URL
# form pinned to a release tag is the install form (`git+https://…@<tag>`; principle 9: the CLI
# installs from a git tag). An f-string with a doubled brace, because the concatenation it replaces
# read as somebody having forgotten one: `{version}` is meant to survive into the template and be
# filled by the caller, and `{{version}}` says so.
INSTALL_COMMAND = f"uv tool install git+{REPOSITORY_URL}@v{{version}}"
# One verb pair per harness, fixed here rather than in the preset: which CLI verb installs a
# plugin is a property of the harness, never of any one plugin, and the two differ (measured;
# the module docstring's "Two harnesses, two verbs" paragraph).
_MARKETPLACE_ADD = {
    "claude": lambda source: ["claude", "plugin", "marketplace", "add", source],
    "codex": lambda source: ["codex", "plugin", "marketplace", "add", source],
}
_PLUGIN_INSTALL = {
    "claude": lambda full: ["claude", "plugin", "install", full],
    "codex": lambda full: ["codex", "plugin", "add", full],
}
# What `--overlay <path>` and `--overlay create:` are each about to do, for the refusal the
# overlay probe raises. One sentence each, so the two commands that ask "is this an overlay"
# differ in what they were doing and not in what the answer means.
_RECORDING = (
    "--overlay must name a real overlay's root, or a path that names nothing, where one is "
    "rendered, because this path becomes the machine's trust anchor: every `drybag attach` on "
    "this machine reads rules and notes out of it"
)
# What `create:` says of a destination it cannot clone into, `{destination}` through
# `printed.quoted`: asked before `gh` is, so nothing exists on GitHub when either is said.
DESTINATION_NOT_A_DIRECTORY = (
    "{destination} is there and is not a directory, so `create:` has nowhere to clone the overlay "
    "and nothing was created on GitHub; move it, or name another repository"
)
DESTINATION_NOT_AN_OVERLAY = (
    "{destination} is a directory that is not an overlay, so `create:` would clone the new "
    "repository over what is there, and nothing was created on GitHub; move it, or name another "
    "repository"
)
# What either form says of a directory there that this user cannot search, so what it holds cannot
# be told, or that is no overlay and that this user cannot both list and write: nothing can be
# rendered or cloned into either. `{destination}` through `printed.quoted`.
UNUSABLE_DIRECTORY = (
    "{destination} is a directory this user cannot list, write and search, so no overlay can be "
    "rendered or cloned into it, and nothing was created on GitHub; give this user read, write "
    "and search on it, or name another place"
)
# What `setup` notes when it records an overlay root other than the one the machine file held,
# `{previous}` through `printed.quoted`: a mistyped `--overlay` path is rendered, not refused.
REPLACED_ROOT = (
    "the machine configuration recorded the overlay at {previous}, and records this one now; "
    "`drybag setup --overlay {previous}` records that one again"
)
# What `--overlay ~/…` says where `HOME` names no home, `{overlay}` through `printed.quoted`.
NO_HOME_FOR_OVERLAY = (
    "--overlay {overlay} begins with `~`, and HOME names no home directory to read it against; "
    "name the overlay by its whole path"
)
# What `--overlay <path>` says of a path that is not there and cannot be rendered, `{path}` and
# `{parent}` through `printed.quoted`.
NO_PLACE_TO_RENDER = (
    "{path} is not there, so --overlay would render an overlay at it, and {parent} is not a "
    "directory this user can create it in; create that directory, or name an existing overlay"
)


@dataclass(frozen=True)
class SetupReport:
    """What one `setup` run did. Every field here is this run's own computation, printable."""

    machine_written: bool
    plugins_installed: tuple[str, ...]
    deny_written: bool
    cli_on_path: bool
    overlay: Path | None
    notes: tuple[str, ...]


def _personal_defaults(preset: dict[str, Any]) -> dict[str, Any]:
    return dict(defaults_of(preset).get("personal", {}))


def _new_personal_values(preset: dict[str, Any], machine: Path) -> dict[str, Any]:
    """The preset's personal defaults, minus every key the machine file already carries.

    A key present in the file — set by an earlier `setup`, or by the owner's own hand — is the
    owner's, and a preset default may not win over it on a later run. An absent key gets the
    preset's default, which is what makes a *first* run write all three.
    """
    existing = read_personal(machine)
    return {k: v for k, v in _personal_defaults(preset).items() if k not in existing}


def _marketplace(preset: dict[str, Any], agent: str) -> tuple[str, str] | None:
    """`(source, marketplace name)` for `agent`, or `None` when the preset declares none.

    `None` is not a fault: nothing is vendored for a harness on a guess, and the caller reports
    it as a note rather than attempting an argv nobody measured.
    """
    table = preset.get("plugins", {}).get(agent)
    if not isinstance(table, dict):
        return None
    source, marketplace = table.get("source"), table.get("marketplace")
    if not isinstance(source, str) or not isinstance(marketplace, str):
        return None
    return source, marketplace


def _install_plugins(
    preset: dict[str, Any],
    agents: Sequence[str],
    selectors: Sequence[str],
    *,
    home: Path,
    runner: Runner,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Install each plugin in `selectors`, the ones the caller named, per harness in `agents`.

    Nothing named is nothing run: no marketplace is registered and no note is written."""
    if not selectors:
        return (), ()
    installed: list[str] = []
    notes: list[str] = []
    for agent in agents:
        market = _marketplace(preset, agent)
        if market is None:
            notes.append(
                f"{agent}: no verified marketplace for {', '.join(selectors)}; "
                f"install manually if this harness supports it (see README)"
            )
            continue
        source, marketplace = market
        add_argv_of, install_argv_of = _MARKETPLACE_ADD.get(agent), _PLUGIN_INSTALL.get(agent)
        if add_argv_of is None or install_argv_of is None:
            notes.append(f"{agent}: no known plugin command for this harness")
            continue
        # Idempotent by construction: an already-registered source is a note from `claude`/
        # `codex` themselves ("already on disk"), read here through the same non-zero-is-a-note
        # path as everything else — a missing binary is `Completed(127, ...)` from `Runner`
        # itself (its own docstring: "a missing binary is a finding, never a traceback").
        added = runner.launch(add_argv_of(source), home)
        if added.code != 0:
            detail = answered(added)
            notes.append(f"{agent}: could not register marketplace {source} ({detail})")
            continue
        for selector in selectors:
            argv = install_argv_of(f"{selector}@{marketplace}")
            done = runner.launch(argv, home)
            if done.code == 0:
                if selector not in installed:
                    installed.append(selector)
            else:
                detail = answered(done)
                notes.append(f"{agent}: `{' '.join(argv)}` did not succeed ({detail})")
    return tuple(installed), tuple(notes)


def _read_document(path: Path) -> tuple[dict[str, Any], str]:
    """The user settings file at `path` as one JSON object, and its text as it is on disk.

    Read as Claude Code reads it (`harnesses.lenient_settings`), past a leading byte-order mark,
    so an owner's file the harness reads is merged into rather than refused."""
    with fsops.reading(path, Failure):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, ""
    if not lenient_settings(text).strip():
        return {}, text
    # The file is the owner's and this run rewrites it, so valid JSON past the parser's reach is
    # refused as a `Failure` naming it rather than read, and rather than ending setup in an
    # internal error.
    return json_object(lenient_settings(text), str(path), error=Failure), text


def _write_user_settings(
    home: Path,
    deny_rules: Sequence[str],
    personal: Mapping[str, Any],
) -> bool:
    """Merge the preset's deny rules and the personal values into `<home>/<USER_SETTINGS>`.

    Merge, never replace: the owner's own file predates drybag on most machines, and nothing
    this run did not add may be recorded as if it had. Deny only — this never reads or writes
    `permissions.allow` — and `pluginConfigs` is filled key by key inside its own `options`, only
    where it holds no value: Claude Code writes there the answers a person gives in its
    plugin-config screen, so a value there is the user's and survives every run.
    """
    path = home / USER_SETTINGS
    document, text = _read_document(path)

    permissions = document.get("permissions")
    permissions = dict(permissions) if isinstance(permissions, dict) else {}
    raw_deny = permissions.get("deny")
    existing_deny = (
        [r for r in raw_deny if isinstance(r, str)] if isinstance(raw_deny, list) else []
    )
    permissions["deny"] = existing_deny + [r for r in deny_rules if r not in existing_deny]
    document["permissions"] = permissions

    plugin_configs = document.get("pluginConfigs")
    plugin_configs = dict(plugin_configs) if isinstance(plugin_configs, dict) else {}
    entry = plugin_configs.get(PLUGIN_ID)
    entry = dict(entry) if isinstance(entry, dict) else {}
    options = entry.get("options")
    options = dict(options) if isinstance(options, dict) else {}
    for key, value in personal.items():
        options.setdefault(key, value)
    entry["options"] = options
    plugin_configs[PLUGIN_ID] = entry
    document["pluginConfigs"] = plugin_configs

    # Written back through `json_text`, which answers a document the parser read and the
    # indenting encoder cannot follow (Python 3.12) as the reader answers one nested too deep.
    # A byte-order mark the file opened with is kept, so a run that changes nothing writes nothing.
    mark = BYTE_ORDER_MARK if text.startswith(BYTE_ORDER_MARK) else ""
    new_text = mark + json_text(document, str(path), error=Failure, indent=2, sort_keys=True) + "\n"
    if new_text == text:
        return False
    try:
        fsops.write_atomically(fsops.resolved(path), new_text)
    except OSError as exc:
        if sandbox_refused(exc):
            raise Refusal(SETUP_SANDBOXED) from exc
        raise Refusal(f"{path} cannot be written ({fsops.said(exc)})") from exc
    return True


def _not_writable(directory: Path, said: str) -> Refusal:
    """The refusal of `directory`, which `os.access` says this user may not write: `SETUP_SANDBOXED`
    where its mode lets its owner, this user, write and search it, and `said` otherwise.

    An agent's sandbox answers `os.access` as it answers the write (measured with `sandbox-exec`
    on macOS 27 on 2026-10-16: `os.access(W_OK)` was false for a directory the profile denied and
    whose mode allowed the write), so a directory its mode does not refuse is refused by something
    else -- the sandbox, or a read-only mount, whose answer is the same for a person: run it from
    their own terminal. A mode that refuses the user is not the sandbox, and keeps `said`.
    """
    try:
        found = os.stat(directory)
    except OSError:
        return Refusal(said)
    writable = stat.S_IWUSR | stat.S_IXUSR
    owned = found.st_uid == os.getuid() and found.st_mode & writable == writable
    return Refusal(SETUP_SANDBOXED if owned else said)


def _check_home_usable(home: Path, *, created: Path | None = None) -> None:
    """Refuse a `--home` this command cannot write into — above the first write.

    `home.mkdir(parents=True)` under a read-only parent raised `PermissionError` into the frame,
    `drybag: internal error`, exit 2; and a home that was there with mode 555 had the machine
    file written into the machine configuration before `_write_user_settings` refused. The
    question is `fsops.creatable`'s, and it is asked of each place a write under `home` lands —
    never of `home` for its own sake: a home with a writable `.claude` already in it takes no
    write at all.

    - The settings file, `<home>/.claude/settings.json` where its links lead: `creatable` walks
      to its directory when that is there, and to the nearest directory above it while it is not
      — which is also where the `mkdir` starts.
    - `created`, the `<home>/<name>` a `create:` overlay is cloned into, while it is not there yet.
      Asked here because the clone is what would find out otherwise, after `gh repo create` has
      made a repository on somebody's account. What is there already is `_requested_overlay`'s.

    The refusal names the directory `creatable` stopped at. `os.access` answers for the real uid
    and says yes to root whatever the mode, so the `mkdir` in `setup` keeps a refusal of its own
    under this one. A directory above `home` that cannot be searched leaves the question open,
    and the `fsops` predicates raise for that rather than guess: the floor of
    `_before_the_first_write` names the path they were asked about, as it does for every step.
    """
    landing = [fsops.resolved(home / USER_SETTINGS)]
    if created is not None and not fsops.exists(created):
        landing.append(created)
    for path in landing:
        directory = fsops.creation_directory(path)
        # A regular file where a directory has to be (`home` itself, or one above it) is the
        # `mkdir`'s to name, as "File exists" or "Not a directory": "is not writable" said of it
        # would be a cause it does not have.
        if fsops.is_dir(directory) and not fsops.creatable(path):
            raise _not_writable(
                directory, f"--home {home} cannot be used: {directory} is not writable"
            )
    # The settings write opens the file's directory `O_RDONLY`, and a directory at mode 300 takes
    # a new name and refuses that open. `os.access` says yes to root, for whom the write's own
    # refusal stays the floor.
    directory = landing[0].parent
    if fsops.is_dir(directory) and not os.access(directory, os.R_OK):
        raise Refusal(
            f"--home {home} cannot be used: {directory} is not readable, and the settings write "
            "opens it to read"
        )


@dataclass(frozen=True)
class _Overlay:
    """What `--overlay` asked for, once everything knowable before the first write is known.

    `kind` is which of the three; `root` is where the overlay is or will be, resolved, and what is
    recorded. A `create` alone names the `(owner, name)` GitHub generates, and `destination`,
    `<home>/<name>` as `overlay.api.target_root` names it, which is judged and cloned into.
    """

    kind: Literal["record", "render", "create"]
    root: Path
    create: tuple[str, str] = ("", "")
    destination: Path = Path()


def _recordable(root: Path) -> None:
    """Refuse an overlay root the machine file cannot hold: it is UTF-8 TOML, and a path the
    disk holds in other bytes — a directory named in latin-1, on Linux — reaches Python with
    surrogate escapes and raised `UnicodeEncodeError` at the last write, after the machine
    file's other tables and the settings file were written. Asked with the tree checks, above
    every write."""
    if not utf_8_name(str(root)):
        raise Refusal(
            f"{quoted(str(root))} is not UTF-8 text, so the machine configuration, a UTF-8 file, "
            f"cannot record it as the overlay root; keep the overlay under a path that is"
        )


def _requested_overlay(overlay: str | None, *, home: Path) -> _Overlay | None:
    """What `--overlay` names, and every refusal its arguments and its tree's identity can raise.

    The first step of `_before_the_first_write`. The first draft called the equivalent of this from
    the bottom
    of `setup`, so `--overlay /typo` had already written the machine file, merged the settings
    file and installed two plugins before it refused, and `--overlay create:` had already
    created a private repository on GitHub.

    `home`, never `Path.cwd()`, is what a *created* overlay is created in: this is a machine
    command, run from wherever the owner happened to be sitting, and a created overlay must not
    depend on that. Measured while that was written: `root=Path.cwd()` created a real
    `drybag-private/` inside this very checkout the first time a test exercised the branch.
    """
    if overlay is None:
        # Nothing was asked for, so nothing is touched: `--overlay` is the only way to reach the
        # overlay at all, and a default here would turn an omitted flag into a repository created
        # on somebody's account.
        return None
    if overlay.startswith("create:"):
        spec = overlay[len("create:") :]
        owner, sep, name = spec.partition("/")
        if not sep or not owner or not name:
            raise Refusal(
                f"--overlay create:<owner>/<name> needs both a GitHub owner and a repository "
                f"name; got {overlay!r}"
            )
        # Refuses a name that is not one path segment, and answers where the tree would land —
        # both without creating anything, which is the whole point of asking here.
        destination, account = target_root(home, owner, name)
        # Judged before any `gh` call, which the clone would otherwise follow with a repository
        # nothing records.
        refused = (DESTINATION_NOT_A_DIRECTORY, DESTINATION_NOT_AN_OVERLAY)
        _judged(destination, overlay_fault, refused)
        root = fsops.resolved(destination)
        _recordable(root)
        return _Overlay("create", root, (account, name), destination)
    # `~` as the machine file's `[overlay] root` reads it: never `/` or the working directory.
    named = in_owner_home(overlay)
    if named is None:
        raise Refusal(NO_HOME_FOR_OVERLAY.format(overlay=quoted(overlay)))
    candidate = fsops.resolved(named)
    _recordable(candidate)
    not_recorded = f"{{fault}}; {_RECORDING}"
    if _judged(candidate, overlay_fault, (not_recorded, not_recorded)):
        return _Overlay("record", candidate)
    # Nothing there: the directory the template's files are made in has to be one this user can
    # write, asked of a file in it here rather than met by the render after the first write.
    parent = candidate.parent
    if not fsops.is_dir(parent) or not fsops.creatable(candidate / "file"):
        raise Refusal(
            NO_PLACE_TO_RENDER.format(path=quoted(str(candidate)), parent=quoted(str(parent)))
        )
    return _Overlay("render", candidate)


def _judged(place: Path, fault_of: Callable[[Path], str | None], refused: tuple[str, str]) -> bool:
    """Whether `place` holds an overlay (`False`: nothing is there), refusing anything else: one
    rule for both forms of `--overlay`. Nothing there, or an empty directory that is no link
    (`fsops.vacant`), takes a render or a clone. A directory this user cannot search, or one holding
    no overlay that it cannot both list and write, is `UNUSABLE_DIRECTORY`; the rest is refused in
    the caller's words for what is not a directory and for a directory holding something else
    (`refused`, given `{destination}` and `{fault}`). `os.access` says yes to root, so the render
    and the clone keep refusals of their own. `fault_of` is `overlay.api.overlay_fault`, handed in
    by the one function here that imports it."""
    shown = quoted(str(place))
    directory = fsops.is_dir(place)
    if directory and not os.access(place, os.X_OK):
        raise Refusal(UNUSABLE_DIRECTORY.format(destination=shown))
    fault = fault_of(place)
    if fault is None:
        return True
    if directory and not os.access(place, os.R_OK | os.W_OK):
        raise Refusal(UNUSABLE_DIRECTORY.format(destination=shown))
    if fsops.vacant(place):
        return False
    said = refused[1] if directory else refused[0]
    raise Refusal(said.format(destination=shown, fault=fault))


def _apply_overlay(planned: _Overlay, *, runner: Runner) -> tuple[Path, str]:
    """Create the overlay if this run has to, record what there is, and say what happened.

    Everything here that can refuse is a **floor** under `_before_the_first_write` rather than a
    second copy of it, in the sense the `attach` area settled the same shape: the checks above
    the first write are what a person acts on, and these are what catches a tree that changed in
    between — or, for the create branch, one that did not exist to be checked at all. What this
    function returns is written into the machine file, and every later `attach` on this machine
    derives `permitted_roots` from that record, so the last thing to touch the tree before it
    becomes the trust anchor asks again.

    The created branch's refusals are `generate`'s, after `gh repo create`, and each says the
    repository may exist and how to record it: the report is discarded on a refusal, so nothing
    else would ever tell the owner.
    """
    match planned.kind:
        case "record":
            require_overlay(planned.root, because=_RECORDING)
            # Armed here too: the hook lives in the clone's own `.git`, so an overlay a second
            # machine records from a clone has no commit-time scan until something installs it.
            recorded = f"recorded the existing overlay at {quoted(str(planned.root))}"
            return planned.root, f"{recorded}; {finish(planned.root, runner=runner)}"
        case "render":
            try:
                rendered = render(planned.root, runner=runner)
            except OSError as exc:
                # The floor under `_requested_overlay`'s question: a directory that changed in
                # between, or root, for whom `os.access` says yes whatever the mode.
                raise Refusal(
                    f"{quoted(str(planned.root))} could not be rendered ({fsops.said(exc)}), and "
                    "nothing was recorded in the machine configuration"
                ) from exc
            root = fsops.resolved(rendered.root)
            notes = (*rendered.notes, finish(root, runner=runner))
            return root, f"rendered the overlay at {quoted(str(root))}: {'; '.join(notes)}"
        case "create":
            owner, name = planned.create
            created = generate(owner, name, root=planned.destination.parent, runner=runner)
            # The one value finished and recorded, as the phase planned it: what the clone's path
            # resolves to now, so a link that arrived there since is followed and not recorded.
            root = fsops.resolved(created.root)
            shown = quoted(str(root))
            # No template: `generate` found the overlay a first run made and asked `gh` nothing.
            said = (
                f"found the overlay already at {shown}, so nothing was generated"
                if created.template is None
                else f"created the overlay at {shown}, generated from {created.template}"
            )
            return root, f"{said}; {finish(root, runner=runner)}"


def _before_the_first_write(
    preset: str, *, home: Path, overlay: str | None
) -> tuple[dict[str, Any], _Overlay | None]:
    """Every question `setup` asks before its first write, in one order, under one floor.

    Nothing is written, and no program is run, until every step has answered; each refuses with
    its own words, and the first refusal ends the run. The steps, in order:

    1. **What `--overlay` names** (`_requested_overlay`): a spec without both halves, a name that
       is not one path segment, a root the machine file cannot hold; for a path holding something,
       a tree that is not an overlay; for one holding nothing, a directory this user cannot render
       it in; and for `create:`, a destination `gh` could not clone into.
    2. **The preset** (`load_preset`): a mistyped `--preset`. It used to come one line after the
       home tree had been created for it.
    3. **Where a write under `--home` lands** (`_check_home_usable`): the settings file's
       directory, the `mkdir` of a home not there yet, and a `create:` destination not there yet.

    **The floor.** `fsops`' predicates raise rather than guess when a path cannot be asked
    about, and a step that does not word that itself let the `OSError` reach `cli.run` as
    `drybag: internal error`: a `create:` destination under a home nobody may search, or an
    `--overlay` directory below one. Whatever a step raises that way is a refusal here, naming
    the path that was being asked about — the `OSError`'s own file name, the path the predicate
    was handed — and the fault in `fsops.said`'s words. A step whose own words name the cause
    better keeps them.
    """
    try:
        planned = _requested_overlay(overlay, home=home)
        # Where this run clones a `create:` overlay, or `None` when it creates none.
        created = planned.root if planned is not None and planned.kind == "create" else None
        data = load_preset(preset, key="--preset")
        _check_home_usable(home, created=created)
    except OSError as exc:
        asked = (
            quoted(str(exc.filename)) if exc.filename is not None else "a path this run was given"
        )
        raise Refusal(
            f"{asked} cannot be checked ({fsops.said(exc)}), so nothing was written"
        ) from exc
    return data, planned


def setup(
    preset: str,
    *,
    home: Path,
    machine: Path,
    runner: Runner,
    overlay: str | None,
    plugins: Sequence[str] = (),
) -> SetupReport:
    """Configure this machine from `preset`, installing the `plugins` named and no other.

    **Everything structural is asked before the first write**, in `_before_the_first_write`:
    `--overlay` is parsed and probed, the preset is loaded and the places a write under `--home`
    lands are asked, while nothing is on disk and no repository exists on anyone's GitHub
    account. What is left after that is the
    work, and the one refusal that follows a write is the post-condition on a tree this run
    created.
    """
    data, planned_overlay = _before_the_first_write(preset, home=home, overlay=overlay)

    # `home` is the root every write in this function lands under, the settings file and a
    # created overlay included. `home` is not a root this process was handed already
    # existing, the way a project root or the overlay itself is. Created directly for the same
    # reason `setup.machine`'s module docstring gives for `fsops.write_atomically` on the
    # machine file: there is nothing for a contained walk to be relative to until this
    # directory exists.
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # The floor under `_check_home_usable`: root, for whom `os.access` says yes whatever the
        # mode, and a directory that changed between that question and this call.
        raise Refusal(f"--home {home} could not be created ({fsops.said(exc)})") from exc
    personal = _new_personal_values(data, machine)
    machine_table = {"version": __version__, "installed": datetime.date.today().isoformat()}
    write_machine(machine, personal=personal, overlay_root=None, machine=machine_table)

    deny_rules = [r for r in data.get("deny", {}).get("global", []) if isinstance(r, str)]
    # **`pluginConfigs` is filled from the machine file where it holds no value, and never
    # overwritten.** It is where Claude Code reads a plugin's `userConfig` answers, and where its
    # plugin-config screen writes the ones a person gives, so a value already there is the
    # user's. The cost is stated where a reader will meet it: a personal value changed in the
    # machine file after the first run does not reach `pluginConfigs`; changing it there, or
    # removing it so the next `setup` fills it again, does.
    deny_written = _write_user_settings(home, deny_rules, read_personal(machine))

    installed, install_notes = _install_plugins(
        data,
        tuple(harness.name for harness in HARNESSES),
        tuple(dict.fromkeys(plugins)),
        home=home,
        runner=runner,
    )
    notes = list(install_notes)

    cli_on_path = shutil.which("drybag") is not None
    if not cli_on_path:
        notes.append(
            f"`drybag` is not on PATH; install it with "
            f"`{INSTALL_COMMAND.format(version=__version__)}`"
        )

    overlay_root: Path | None = None
    if planned_overlay is not None:
        previous = recorded_root(machine)
        overlay_root, note = _apply_overlay(planned_overlay, runner=runner)
        notes.append(note)
        if previous is not None and previous != overlay_root:
            notes.append(REPLACED_ROOT.format(previous=quoted(str(previous))))
        # An overlay this run made is one this machine syncs; recording an existing one is the
        # owner's to switch on (`config.owner.Sync`).
        switch = {"enabled": True} if planned_overlay.kind != "record" else {}
        write_machine(machine, personal={}, overlay_root=overlay_root, machine={}, sync=switch)

    return SetupReport(
        machine_written=True,
        plugins_installed=installed,
        deny_written=deny_written,
        cli_on_path=cli_on_path,
        overlay=overlay_root,
        notes=tuple(notes),
    )
