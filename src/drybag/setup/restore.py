"""`setup --restore`: bring a machine back from the private overlay its machine file records.

Machine setup is `setup.run.setup`; this module is what `--restore` does around it. It checks the
tools (`missing_tools`), installs the plugins the overlay's machine settings enable
(`listed_plugins`, `install_listed`), and attaches each checkout the overlay's
`machine/projects.toml` lists (`listed_projects`, `restore`). The overlay is the owner's, so the
names, directories and plugin sources it holds are the owner's input; what a *checkout* holds is a
repository's, and `attach` judges it.

**A checkout cannot choose the store it is attached under.** A listed checkout's tracked
`drybag.toml` can claim another of the owner's projects. The store `restore` hands `attach`
comes from the table key in `<overlay>/machine/projects.toml`, which no repository writes, and
`attach` refuses a store that is not the checkout's own project's, so such a checkout is refused
rather than given another project's notes, rules and delivery. The adversary is a clone that names
itself after a project it does not belong to; the anchor is the key, which only the owner's file
holds, checked against the project the checkout's own file names by `attach.binding.read_binding`.

One project failing never ends the others: each outcome is its own, and a re-run completes what a
failure left.
"""

from __future__ import annotations

import shutil
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from drybag import fsops
from drybag.attach.api import attach
from drybag.command import command_owner
from drybag.config.loader import read_document
from drybag.config.reading import UNPARSEABLE, toml_position
from drybag.errors import Failure, Refusal
from drybag.harnesses import HARNESSES, lenient_settings
from drybag.jsonobject import json_object
from drybag.memory.api import project_notes, promote_share
from drybag.printed import answered, clipped, quoted
from drybag.runner import Runner
from drybag.setup.run import _MARKETPLACE_ADD, _PLUGIN_INSTALL

# The owner's list of the checkouts of the projects this overlay serves, one `[projects.<name>]`
# table each, under the overlay root. `docs/cli.md` documents it as a product input.
PROJECTS_FILE = Path("machine") / "projects.toml"
_LABEL = PROJECTS_FILE.as_posix()


class Listed(NamedTuple):
    """One table of the projects file: the overlay project it names and where its checkout is."""

    name: str
    dir: Path


def _table(name: str) -> str:
    return f"[projects.{clipped(name)}] of {_LABEL}"


def listed_projects(overlay: Path, *, home: Path) -> tuple[Listed, ...]:
    """The `[projects.<name>]` tables of `<overlay>/machine/projects.toml` in file order.

    `dir` is required and is absolute or led by `~/`, which is read under `home`; every other key
    is ignored, because an overlay's own scripts may read them. `()` when the file is not there.
    A file that cannot be read, is not TOML, whose `projects` is not a table or holds a table
    whose `dir` is missing, not a string or relative is a `Refusal` naming it.
    """
    path = overlay / PROJECTS_FILE
    with fsops.reading(_LABEL, Refusal):
        try:
            text = fsops.read_regular_text(path)
        except FileNotFoundError:
            return ()
    try:
        document = tomllib.loads(text)
    except UNPARSEABLE as exc:
        raise Refusal(f"{_LABEL} is not valid TOML {toml_position(exc)}") from None
    projects = document.get("projects", {})
    if not isinstance(projects, dict):
        raise Refusal(f"`projects` in {_LABEL} is not a table")
    found: list[Listed] = []
    for name, table in projects.items():
        if not isinstance(table, dict):
            raise Refusal(f"{_table(name)} is not a table")
        value = table.get("dir")
        if not isinstance(value, str) or not value:
            raise Refusal(f"{_table(name)} has no `dir`, or it is not a string")
        if "\0" in value:
            raise Refusal(f"`dir` of {_table(name)} is not a path")
        if value.startswith("~/"):
            directory = home / value[2:]
        elif Path(value).is_absolute():
            directory = Path(value)
        else:
            raise Refusal(
                f"`dir` of {_table(name)} is relative: name it by its whole path or `~/…`"
            )
        found.append(Listed(name, directory))
    return tuple(found)


def missing_tools(which: Callable[[str], str | None] = shutil.which) -> tuple[str, ...]:
    """What a restored machine needs and `PATH` lacks: `git`, one of the harnesses' programs
    (`claude` and `codex`, as `harnesses.HARNESSES` names them), and a `python3` or a `uv` for the
    hooks. Said, never refused: a missing `git` makes each attach fail in attach's own words."""
    missing: list[str] = []
    if which("git") is None:
        missing.append("git")
    harnesses = [harness.name for harness in HARNESSES]
    if all(which(name) is None for name in harnesses):
        missing.append(" or ".join(harnesses))
    if which("python3") is None and which("uv") is None:
        missing.append("python3 or uv")
    return tuple(missing)


# The overlay's copy of Claude Code's user settings. Restore reads two keys of it,
# `extraKnownMarketplaces` and `enabledPlugins`, and writes nothing from it.
PLUGINS_FILE = Path("machine") / "claude" / "settings.json"
_PLUGINS_LABEL = PLUGINS_FILE.as_posix()
# The `extraKnownMarketplaces` source kinds restore carries, each with the one key whose string
# value is `claude plugin marketplace add`'s argument. A source of another kind, or holding any
# other key (a `git` source's `ref`, a `github` source's `path` or `sparsePaths`), is a note and is
# not added: registered without that key, the marketplace would not be the one the file names.
_CARRIED = {"github": "repo", "git": "url"}


def listed_plugins(overlay: Path) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """`(marketplace add-arguments, plugin ids, notes)` the overlay's copy of Claude Code's user
    settings names.

    An `extraKnownMarketplaces.<name>.source` that is exactly `{"source": "github", "repo": R}` or
    `{"source": "git", "url": U}`, string values, gives `R` or `U`; any other source is a note
    naming the marketplace and what restore does not carry. Every `enabledPlugins` key whose value
    is `true` is an id, as written. No file lists nothing; one that cannot be read as a JSON
    object, or whose two keys are not objects, is one note and lists nothing of what it could not
    read.
    """
    path = overlay / PLUGINS_FILE
    try:
        text = fsops.read_regular_text(path)
    except FileNotFoundError:
        return (), (), ()
    except (OSError, UnicodeDecodeError) as exc:
        return (), (), (f"{fsops.unreadable(_PLUGINS_LABEL, exc)}, so no plugin was installed",)
    try:
        document = json_object(lenient_settings(text), _PLUGINS_LABEL, error=Failure)
    except Failure as exc:
        return (), (), (f"{exc}, so no plugin was installed",)
    notes: list[str] = []
    marketplaces: list[str] = []
    known = document.get("extraKnownMarketplaces", {})
    if not isinstance(known, dict):
        notes.append(f"`extraKnownMarketplaces` in {_PLUGINS_LABEL} is not an object")
        known = {}
    for name, entry in known.items():
        source = entry.get("source") if isinstance(entry, dict) else None
        source = source if isinstance(source, dict) else {}
        kind = source.get("source")
        key = _CARRIED.get(kind) if isinstance(kind, str) else None
        if key is None or not isinstance(source.get(key), str):
            said = "is not a `github` source with a `repo` or a `git` source with a `url`"
        elif extra := sorted(set(source) - {"source", key}):
            said = (
                f"holds {', '.join(f'`{clipped(k)}`' for k in extra)}, which restore does not carry"
            )
        else:
            marketplaces.append(source[key])
            continue
        notes.append(f"marketplace {clipped(name)} of {_PLUGINS_LABEL}: its source {said}")
    enabled = document.get("enabledPlugins", {})
    if not isinstance(enabled, dict):
        notes.append(f"`enabledPlugins` in {_PLUGINS_LABEL} is not an object")
        enabled = {}
    ids = tuple(i for i, on in enabled.items() if on is True)
    return tuple(marketplaces), ids, tuple(notes)


def install_listed(
    marketplaces: Sequence[str], ids: Sequence[str], *, home: Path, runner: Runner
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(installed ids, notes)`: each marketplace added, then each id installed, with setup's own
    argv (`_MARKETPLACE_ADD["claude"]` and `_PLUGIN_INSTALL["claude"]` in `setup.run`) and on every
    run, so what Claude Code answers for one already registered or installed is what is said. A
    non-zero exit, a missing `claude` included (`Runner` answers `127`), is a note, never a
    refusal."""
    add: Callable[[str], list[str]] = _MARKETPLACE_ADD["claude"]
    install: Callable[[str], list[str]] = _PLUGIN_INSTALL["claude"]
    done: list[str] = []
    notes: list[str] = []
    for argument in marketplaces:
        added = runner.launch(add(argument), home)
        if added.code != 0:
            said = f"{clipped(argument)} ({answered(added)})"
            notes.append(f"claude: could not register marketplace {said}")
    for plugin in ids:
        argv = install(plugin)
        ran = runner.launch(argv, home)
        if ran.code == 0:
            done.append(plugin)
        else:
            notes.append(f"claude: {clipped(' '.join(argv))} did not succeed ({answered(ran)})")
    return tuple(done), tuple(notes)


class Outcome(NamedTuple):
    """What one listed project came to. `name` and `dir` are the owner's file's; `detail` is
    attach's own words, printed through `drybag.printed`."""

    name: str
    dir: Path
    state: Literal["attached", "absent", "refused", "failed"]
    detail: str
    promoted: int


@dataclass(frozen=True)
class RestoreReport:
    """What `restore` did, beyond the machine setup that ran before it."""

    missing: tuple[str, ...]
    plugins: tuple[str, ...]
    notes: tuple[str, ...]
    projects: tuple[Outcome, ...]


def restore(
    overlay: Path,
    listed: Sequence[Listed],
    *,
    home: Path,
    machine: Path,
    runner: Runner,
    yes: bool,
) -> RestoreReport:
    """Attach each listed checkout that exists, each under the store its table key names (the
    module says why), and a checkout with no `drybag.toml` under the project that key names.

    A `dir` that is not there is `absent`, reported and skipped; attach's `Refusal` is `refused`
    and its `Failure` `failed`, each with the message as `detail`, and the next project goes on.

    With `yes`, each attached project's unreviewed notes are then recorded as reviewed on this
    machine (`promote_share`), counted in the outcome's `promoted`; a project whose notes cannot be
    recorded is `failed`, attached all the same. **The notes recorded are every unreviewed note of
    the overlay's share for the table key of the owner's projects file, which no repository
    writes:** `common/memory`, each listed tag's `groups/<tag>/memory` and the project's own groups,
    notes written on another machine that nobody reviewed included. Only overlay paths are walked,
    and a group directory that is a link is followed. Without `yes` nothing is recorded as
    reviewed, whatever the overlay holds.
    """
    marketplaces, ids, listing_notes = listed_plugins(overlay)
    installed, install_notes = install_listed(marketplaces, ids, home=home, runner=runner)
    outcomes: list[Outcome] = []
    owner = None
    for item in listed:
        try:
            if not fsops.exists(item.dir):
                outcomes.append(Outcome(item.name, item.dir, "absent", "not there", 0))
                continue
            owner = command_owner(machine) if owner is None else owner
            attached = attach(
                item.dir,
                store=project_notes(overlay, item.name),
                machine=machine,
                runner=runner,
                owner=owner,
                project=item.name if read_document(item.dir) is None else None,
            )
        except Refusal as exc:
            outcomes.append(Outcome(item.name, item.dir, "refused", str(exc), 0))
            continue
        except Failure as exc:
            outcomes.append(Outcome(item.name, item.dir, "failed", str(exc), 0))
            continue
        except OSError as exc:
            outcomes.append(Outcome(item.name, item.dir, "failed", fsops.said(exc), 0))
            continue
        recorded = "recorded" if attached.binding_recorded else "already recorded"
        detail = "; ".join((f"binding {attached.binding} {recorded}", *attached.notes))
        if yes:
            try:
                promoted = len(promote_share(overlay, item.name, machine=machine))
            except (Failure, Refusal) as exc:
                said = f"{detail}; its notes were not recorded as reviewed: {exc}"
                outcomes.append(Outcome(item.name, item.dir, "failed", said, 0))
                continue
            outcomes.append(Outcome(item.name, item.dir, "attached", detail, promoted))
        else:
            outcomes.append(Outcome(item.name, item.dir, "attached", detail, 0))
    notes = (*listing_notes, *install_notes)
    return RestoreReport(missing_tools(), installed, notes, tuple(outcomes))


def line(outcome: Outcome) -> str:
    """One project's line of the summary."""
    said = f"{quoted(outcome.name)} at {quoted(str(outcome.dir))}: {outcome.state}"
    said = f"{said} ({outcome.detail})" if outcome.detail else said
    return f"{said}; {outcome.promoted} note(s) recorded as reviewed" if outcome.promoted else said
