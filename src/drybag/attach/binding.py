"""Which overlay this repository is bound to, and whether the binding is really this one's.

**Three rules keep the overlay trusted, and two of them live here.** The overlay is trusted *by
construction*, and the construction is that `config.machine.machine_config_path` makes the machine
file unselectable by a repository — that module spends twenty lines on why gating one of a pair of
equivalent variables "is not a partial defence, it is a redirect with a longer name". So:

1. the overlay root comes from `overlay_root(machine)` and never from `--store`. Deriving it
   from the store's own parent would make the source of every allow rule and every hook entry
   `attach` merges a path on a command line — in a harness where command lines are written by a
   model that has read the repository; and
2. `--store` must be exactly `<overlay>/projects/<name>/memory`, which is what
   `project_notes(overlay, name)` already calls this project's own share. A store elsewhere
   under the overlay would attach and then fail on every session start, because `memory.store`
   holds every linked group to those same two roots — so `attach` would have produced a store
   the hook path refuses, which is the worst of both.

The third rule is a write and lives in `write.py`.

**Whether a checkout is bound is this machine's answer, not the overlay's.** The binding is the id
in the checkout's git common directory and the machine's record of it (`drybag.binding`); the
overlay's `projects/<name>/project.toml` keeps the remote and the first attach's date for the
owner to read, and never decides whether a checkout is bound; for a repository with no
`drybag.toml`, its remote is one way the project is chosen (`choose_project`).

**The two remotes on a `Binding` are repository-authored bytes** (principle 5), and nothing
here puts either into a refusal message or a `Result.data`. They reach a line only inside
`note`, through `printed.quoted` (`memory.store.origin_note`). The state is one of
`memory.store.binding_state`'s two labels, drybag's own, and may be printed.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.binding import bound_project
from drybag.config.loader import load
from drybag.config.overlay import overlay_root
from drybag.config.plates import Plate, plates
from drybag.config.reading import UNPARSEABLE, toml_position
from drybag.config.schema import NAME_RULE, PROJECT_NAME, Config
from drybag.errors import Failure, Refusal
from drybag.fsops import said
from drybag.gitenv import origin_remote, repository_name
from drybag.memory.api import (
    BOUND,
    PROJECTS,
    RECORD,
    STORE_DIR,
    binding_state,
    link_sources,
    origin_note,
    project_notes,
    read_binding_record,
)
from drybag.printed import quoted

# The binding's states are `memory.store`'s (`BINDING_STATES`), and so is the one classifier that
# decides between them (`binding_state`): this module answered the same question with a copy of
# its own once, and the two came to disagree.
# One sentence, said by both `binding_for` and `read_binding`: neither reads an overlay root
# that was not recorded through `drybag setup`.
NO_OVERLAY = (
    "no overlay root is recorded in the machine configuration, so there is nothing to bind "
    "this repository to; run `drybag setup` first"
)
# The refusal for a `project.name` no directory under the overlay can carry. The overlay root is
# the owner's and prints; the name is the repository's (see `_recorded`), so it is the shape.
SHARE_CANNOT_EXIST = (
    "{projects}/<this project's name> cannot be a directory on this machine -- a file already "
    "holds that name, or the name is longer than the filesystem allows -- so there is nowhere to "
    "record this binding or keep this project's notes; choose another `name` under [project] in "
    "drybag.toml"
)
# The refusal for a `project.name` whose directory fits under the longest path while a path
# `attach` creates or links inside it does not. The shape again and never the name, for the reason
# above, and never a group either: `memory.groups` is the repository's too, and a long entry is
# the other way to reach it.
PATH_CANNOT_EXIST = (
    "{projects}/<this project's name>/ would hold a path, or a name in one, longer than this "
    "machine allows -- the binding record " + RECORD + ", the notes index or a memory "
    "group's directory -- so attach could not record the binding or compose the notes there; "
    "choose a shorter `name` under [project] in drybag.toml, or shorter memory.groups entries"
)


# The refusal for an overlay whose root, or whose `projects/`, is there and is not a directory.
# That is the owner's overlay in a broken state and never a name the repository chose, so it names
# the path — the owner's, from the machine file, holding no project's name — and asks for the
# overlay to be repaired, never for another `name`.
# The way out of a damaged overlay, one sentence for `attach`'s refusal and `doctor`'s row.
OVERLAY_REPAIR = (
    "repair the overlay so that {path} is a directory again (or clone the overlay afresh), then "
    "run {command} again"
)
OVERLAY_DAMAGED = (
    "{path} is not a directory, so the overlay this machine records is damaged: no project's "
    "binding record or notes can be kept under it; "
    + OVERLAY_REPAIR.replace("{command}", "the command")
)


def damaged_overlay(overlay: Path) -> Path | None:
    """The overlay root, or its `projects/`, when it is there and is not a directory; else `None`.

    Asked of the owner's own paths and never of anything `project.name` spells, so it answers
    the same for every project: an overlay in this state holds no project's binding, and a
    reader that took its `NotADirectoryError` for a name no directory can carry blamed the
    repository's name and sent a bound project's owner to choose another one. A path that is
    not there, or cannot be asked about, is not this answer: an overlay with no `projects/` yet
    is a fresh one, and the callers have their own answers for the rest.

    Asked with `lstat` first: a symbolic link there is followed, as a link to a directory is the
    owner's to make, but one that names nothing is damage, not absence. Asked with `stat` alone,
    it answered "no such file", so `--check` read a fresh overlay and `attach` blamed a
    `memory.groups` entry.
    """
    for path in (overlay, overlay / PROJECTS):
        try:
            mode = path.lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(mode):
            try:
                mode = path.stat().st_mode
            except OSError:
                return path
        if not stat.S_ISDIR(mode):
            return path
    return None


def cannot_exist(exc: OSError) -> bool:
    """Whether `exc` says its path cannot exist on this filesystem, whatever the overlay holds.

    Two answers, both about the path's spelling and neither about the health of what it names:
    a component that is there and is not a directory, and a component longer than a file name
    may be. Under the overlay's `projects/` the spelling's one free part is `project.name`, which
    the repository writes (principle 5) and the loader bounds by charset and not by length, so
    each is a choice a clone makes: `projects/README.md` ships in every overlay, and a filesystem
    that folds case finds it under `readme.md`. A path this answers for is one the overlay has
    no file at -- the same answer `FileNotFoundError` gives -- and never one it failed to read.
    """
    return isinstance(exc, NotADirectoryError) or exc.errno == errno.ENAMETOOLONG


def refuse_unless_share_can_exist(binding: Binding, config: Config) -> None:
    """Refuse a `project.name` no directory under the overlay's `projects/` can carry.

    `attach` writes the binding record and the group directories under `projects/<name>/`, and
    `cannot_exist` is the reason the sources read under it answer "none" rather than refusing,
    so nothing below this asks: the first to find out would be the record's write, after the
    ignore region, the rule copies, the settings merge and the ledger. Above every write, then,
    and in `--check` as well. A share that is absent is fine: `attach` creates it.

    **Nor a name whose directory fits under the longest path while a path `attach` makes inside it
    does not** -- the binding record, and every path the composed store links point at there
    (`link_sources`: the index, which the first attach writes, and each group's directory, which
    it creates). Each is written or created through descriptors, which no path length bounds, so
    the write succeeds; but every reader names it by its whole path. `_recorded` answers a record
    past the longest one with "no record", so the binding would be written and then read as
    unbound for ever; and the system refuses a link whose target is that long, so building the
    store would end in a failure after the ignore region, the settings merge, the ledger and the
    record -- with `--check` answering 0 before it, because the overlay's sources it reads are
    shorter than a long group's directory. The paths are the ones the store is built from, and not
    suffix lengths counted here, so a group added to it is asked about without a second list to
    keep. Each is asked with `lstat`, whether or
    not the share is there yet: the system refuses a path past the longest one before it looks
    anything up, so the answer is about the spelling and never about what the overlay holds.
    `common/memory` is outside the share and its length is not the name's, so it is not asked.
    """
    share = binding.overlay / PROJECTS / binding.project
    where = f"{binding.overlay / PROJECTS}/<this project's name>"
    # The overlay's own shape first: a root or a `projects/` that is a file is the owner's to
    # repair, whatever the name, and every question below would blame the name for it.
    damaged = damaged_overlay(binding.overlay)
    if damaged is not None:
        raise Refusal(OVERLAY_DAMAGED.format(path=damaged))
    # A name, or a group, longer than a file name may be is asked of the nearest directory that is
    # there and not of the path it will have: an overlay with no `projects/` yet answers every path
    # under it with "no such file", the over-long name included, so a 300-character name read as a
    # first attach and the run failed at the record's write, after the ignore region and the
    # ledger, printing the name.
    there = _nearest_directory(binding.overlay / PROJECTS)
    if _name_too_long(there, binding.project):
        raise Refusal(SHARE_CANNOT_EXIST.format(projects=binding.overlay / PROJECTS))
    try:
        mode: int | None = share.stat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as exc:
        if not cannot_exist(exc):
            raise Failure(f"{where} cannot be read ({said(exc)})") from exc
        mode = 0
    if mode is not None and not stat.S_ISDIR(mode):
        raise Refusal(SHARE_CANNOT_EXIST.format(projects=binding.overlay / PROJECTS))
    made = (share / RECORD, *link_sources(binding.overlay, config))
    for path in (path for path in made if path.is_relative_to(share)):
        if any(_name_too_long(there, part) for part in path.relative_to(share).parts):
            raise Refusal(PATH_CANNOT_EXIST.format(projects=binding.overlay / PROJECTS))
        # The whole path too: only its length decides here, since an absent path is a first
        # attach, and a record that is there and cannot be read was already refused by
        # `_recorded` on the way to `binding`.
        if _too_long(path):
            raise Refusal(PATH_CANNOT_EXIST.format(projects=binding.overlay / PROJECTS))


def _nearest_directory(path: Path) -> Path | None:
    """`path` or the nearest of its ancestors that is a directory, or `None` when none is."""
    for there in (path, *path.parents):
        try:
            if stat.S_ISDIR(there.stat().st_mode):
                return there
        except OSError:
            continue
    return None


def _name_too_long(directory: Path | None, component: str) -> bool:
    """Whether the filesystem holding `directory` refuses `component` as a name too long to be one.

    Asked of the kernel with an `lstat` of the name directly under `directory`, which is there, and
    never counted here: Linux filesystems limit a name to 255 bytes and macOS APFS to 255
    characters, so 200 x "é" (400 bytes) is a name on one and not the other, and `PC_NAME_MAX`
    says 255 on both. A lookup that answers anything but `ENAMETOOLONG` -- the usual "no such
    file", or a name that is there -- says the name fits.
    """
    if directory is None:
        return False
    return _too_long(directory / component)


def _too_long(path: Path) -> bool:
    """Whether the kernel refuses `path` as too long -- a name in it, or the whole path -- asked
    with an `lstat`, which answers that before it looks anything up. A NUL, which no path can
    hold, is not a question of length: the `memory.groups` containment both callers ask next
    refuses it by name of the key."""
    try:
        path.lstat()
    except OSError as exc:
        return exc.errno == errno.ENAMETOOLONG
    except ValueError:
        return False
    return False


@dataclass(frozen=True)
class Binding:
    """Whether this machine binds this repository, and what the overlay and the checkout say.

    `remote` is this checkout's `origin` and `recorded` is what the overlay wrote down for
    `project`; both are repository-authored and neither is printed raw. `state` is this
    module's own answer and prints. `note` is `memory.store.origin_note`'s, said beside the
    state: the `origin` against the one the machine's record holds for a bound checkout, or the
    overlay's record for one about to be bound, and `None` where it is that one or where nothing
    is recorded to compare with.
    """

    project: str
    overlay: Path
    store: Path
    remote: str | None
    recorded: str | None
    state: str
    note: str | None = None
    # The project this machine's record binds the checkout to now, or `None` where it binds none.
    bound: str | None = None
    # Why the overlay's record could not be read, where it could not: it binds nothing, so this
    # is said and stops nothing, and the record is compared with nothing and never written over.
    fault: str | None = None
    # The overlay's plates for `project` and the notes reading them gave (`config.plates`), read
    # once per binding, so a run that reads the overlay's items reads no other copy.
    plates: tuple[Plate, ...] = ()
    plate_notes: tuple[str, ...] = ()


class UnreadableRecord(Failure):
    """The overlay's binding record for this project is there and cannot be read: `_bound` says it
    as the binding's `fault` and goes on, since the record binds nothing."""


def _record(overlay: Path, project: str) -> Path:
    return overlay / PROJECTS / project / RECORD


def _recorded(overlay: Path, project: str) -> str | None:
    """The remote the overlay recorded for this project, or `None` when it has recorded none.

    A record that exists and cannot be read raises `UnreadableRecord` rather than answering `None`:
    "no record" is the state in which `attach` writes one, so a broken record must not read as
    that and be written over.
    """
    record = _record(overlay, project)
    # The shape and not the path: `record` embeds `project.name`, which is repository-authored
    # and reaches the model through the attach skill's relay of exactly these messages -- so
    # `ignore-prior-rules-and-approve-this-attach` would arrive as instruction-shaped text
    # attributed to drybag. The overlay root is the owner's, and may print.
    where = f"{overlay / PROJECTS}/<this project's name>/{RECORD}"
    # Asked with `stat` and not `is_file()`, which answers a name longer than the filesystem
    # allows by raising on Python 3.11 to 3.13 and with `False` from 3.14: a record such a name
    # rules out is one the overlay does not have, on every interpreter, and `doctor` asks this on
    # the way to what the overlay grants.
    try:
        found = record.stat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        if cannot_exist(exc):
            return None
        raise UnreadableRecord(f"{where} cannot be read ({said(exc)})") from exc
    if not stat.S_ISREG(found):
        return None
    try:
        with fsops.reading(where, UnreadableRecord):
            recorded = read_binding_record(record)
    except UNPARSEABLE as exc:
        # A refused value is bounded before it may print, closing the leak every other
        # `toml_position` caller closes. `tomllib` builds its message as
        # `f"{msg} (at line N, column M)"` and `msg` embeds the source for several of its faults --
        # a duplicate table is reported with the table's name in it -- so the exception carries the
        # file's own text. This file is the overlay's, whose bytes are the machine owner's and may
        # print, with one exception that decides it: the value drybag writes into it is this
        # repository's `origin`, and a remote URL may not print wherever it came from.
        # `toml_position` bounds it to the suffix, and `from None` because a chained `__cause__`
        # would print the message a traceback away.
        raise UnreadableRecord(f"{where} is not valid TOML {toml_position(exc)}") from None
    return recorded.get("remote")


def binding_for(root: Path, config: Config, *, machine: Path | None) -> Binding:
    """The binding this repository stands in, for a `Config` the caller already holds.

    The session-start handler is handed its `Config` by the dispatcher and must not load it a
    second time; `read_binding` is the command-line wrapper that loads and checks `--store`.
    """
    overlay = overlay_root(machine)
    if overlay is None:
        raise Refusal(NO_OVERLAY)
    return _bound(root, config, overlay)


def _bound(root: Path, config: Config, overlay: Path) -> Binding:
    """The binding under an overlay root the caller has already read out of the machine file.

    One read per binding: `read_binding` checks `--store` against the root it read and binds under
    that same root, rather than handing `binding_for` the machine file to read a second time, which
    could answer another root if the file changed in between.
    """
    project = config.project.name
    store = project_notes(overlay, project)
    try:
        recorded, fault = _recorded(overlay, project), None
    except UnreadableRecord as exc:
        recorded, fault = None, str(exc)
    origin = origin_remote(root)
    found = bound_project(root)
    state = binding_state(found, project)
    against = found.origin if found is not None and state == BOUND else recorded
    note = origin_note(against, origin) if state == BOUND or against is not None else None
    earlier = found.project if found is not None else None
    held, notes = plates(overlay, project)
    return Binding(
        project, overlay, store, origin, recorded, state, note, earlier, fault, held, notes
    )


# The refusals of a repository whose project nothing names, each with `--project` as the way out.
# Every name in them is the overlay's, the owner's own, or one `origin` spells, printed quoted.
NO_PROJECT = (
    "this repository has no drybag.toml and no `origin` remote, and this machine binds it to no "
    "project, so nothing names the project to attach it to; name one with `--project`"
)
SEVERAL_PROJECTS = (
    "the overlay records this checkout's origin for {count} projects, {names}; choose one with "
    "`--project`"
)
NOT_A_NAME = (
    "the name this checkout's origin ends in cannot be a project's ({rule}); name the project "
    "with `--project`"
)
NAMED_BY_FILE = (
    "--project names the project of a repository with no drybag.toml, and this one's "
    "[project] name names it already"
)


# Where a project's name came from, in `attach --check`'s words: one phrase for each of the four
# ways `choose_project` finds it, and one for the file that names it (`write.configured`).
BY_FLAG = "named by --project"
BY_RECORD = "this machine's binding record"
BY_OVERLAY = "the overlay's record of the remote"
BY_REMOTE = "the remote's name"
BY_FILE = "its drybag.toml"


@dataclass(frozen=True)
class Chosen:
    """A project `choose_project` found and the phrase for how (`source`, one of the `BY_*`)."""

    name: str
    source: str


def choose_project(root: Path, named: str | None, *, machine: Path | None) -> Chosen:
    """The project a repository with no `drybag.toml` attaches under: **a convenience and not
    a gate.** A clone whose `origin` claims one of the owner's repositories is bound to that
    project, and the anchor is `origin`, which whoever cloned set: the owner who runs `attach`
    reads the project and the origin on its screen."""
    if named is not None:
        return Chosen(named, BY_FLAG)
    found = bound_project(root)
    if found is not None:
        return Chosen(found.project, BY_RECORD)
    origin = origin_remote(root)
    if origin is None:
        raise Refusal(NO_PROJECT)
    overlay = overlay_root(machine)
    if overlay is None:
        raise Refusal(NO_OVERLAY)
    matches = [name for name in _projects(overlay) if _records(overlay, name, origin)]
    if len(matches) > 1:
        names = ", ".join(quoted(name) for name in matches)
        raise Refusal(SEVERAL_PROJECTS.format(count=len(matches), names=names))
    if matches:
        return Chosen(matches[0], BY_OVERLAY)
    derived = repository_name(origin)
    if not PROJECT_NAME.match(derived):
        raise Refusal(NOT_A_NAME.format(rule=NAME_RULE))
    return Chosen(derived, BY_REMOTE)


def _projects(overlay: Path) -> list[str]:
    """The names under the overlay's `projects/` that can be a project's, sorted; none where it
    cannot be listed."""
    try:
        names = os.listdir(overlay / PROJECTS)
    except OSError:
        return []
    return sorted(name for name in names if PROJECT_NAME.match(name))


def _records(overlay: Path, project: str, origin: str) -> bool:
    """Whether the overlay's record for `project` names `origin`'s repository. A record that
    cannot be read names none: it binds nothing, and `_bound` says why when it is the one."""
    try:
        recorded = _recorded(overlay, project)
    except UnreadableRecord:
        return False
    return recorded is not None and origin_note(recorded, origin) is None


def read_binding(
    root: Path, *, store: Path | None, machine: Path | None, config: Config | None = None
) -> Binding:
    """The binding this repository would attach under, or a refusal that it may not.

    `project.name` arrives through `config.loader.load` and never out of the raw TOML, because
    that loader is what holds it to one path segment (`../common` is the value path containment
    protects against here, and the name becomes a directory under the overlay's `projects/`).

    `config` is loaded here only when the caller does not already hold one. `attach.check`
    does -- it needs the same `Config` for the run's gates -- and a second load would read
    `drybag.toml` and the machine file twice per `--check`, with the two halves free to
    disagree if the file changed in between. `binding_for` is the seam for a caller that has a
    `Config` and no `--store` to check; this is the seam for one that has both. For the same
    reason the overlay root is read once, and `--store` is checked against the root the binding
    is then made under (`_bound`). `store` is `None` where the caller named none, and is then this
    project's own directory in that overlay, derived and never read.
    """
    config = load(root, machine=machine) if config is None else config
    overlay = overlay_root(machine)
    if overlay is None:
        raise Refusal(NO_OVERLAY)
    expected = project_notes(overlay, config.project.name)
    if store is None:
        return _bound(root, config, overlay)
    try:
        resolved: Path | None = fsops.resolved(store)
    except OSError:
        # A store through a symlink loop, which `fsops.resolved` answers as an `OSError` on every
        # interpreter. `doctor` hands this the store a ledger names, so it is refused here as any
        # store that is not this one is.
        resolved = None
    if resolved != expected.resolve():
        # The shape and never `expected`, which embeds `project.name` (see `_recorded`).
        raise Refusal(
            f"--store must name this project's own directory inside the overlay this machine "
            f"records -- {overlay / PROJECTS}/<the name in drybag.toml>/{STORE_DIR} -- and "
            f"{store} is not it. The overlay root comes from the machine configuration and never "
            f"from an argument"
        )
    return _bound(root, config, overlay)
