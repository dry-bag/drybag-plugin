"""Where the notes are, and the four ways that answer can be a lie.

A store is a per-project value with no machine-level default, because the wrong answer is not
"no memory" but *another project's* memory reaching this session. Four things are therefore
checked, and each closes a hole the other three leave open:

1. **The shape.** In overlay mode the store is the composed store `attach` builds under this
   machine's data directory, named by the checkout's binding id (`composed_directory`), holding
   one link per group: `developer` points into the overlay's `common/memory`, which is shared
   across projects and cannot live under `projects/<name>/`, and `tag-<tag>` into the
   `groups/<tag>/memory` of each tag the project's record lists. It is the directory the harness
   reads through `autoMemoryDirectory`, so drybag's bundles and the harness read one store. In
   `in-repo` mode `paths.memory` is a real directory, never one link for the whole store.
2. **Containment inside the store.** A group name is repository-controlled — `memory.groups`
   is an ordinary `drybag.toml` list — so `groups = ["../secret"]` must not become a read,
   and certainly not a write, outside the store. `config/paths.py` says in as many words that
   this field reaches no guard of its own and that the module consuming it owns the check.
3. **The link's target.** A link is honoured only when it lands inside *this project's* share
   of the recorded overlay: `common/memory`, the `memory` of a tag the bound project's record lists,
   or `projects/<the bound name>/memory`. Testing
   containment in the overlay root alone lets an honestly-named, honestly-bound project point
   one directory sideways at another client's notes.
4. **The binding.** This machine must record the checkout's binding id, kept in its git common
   directory, as bound to this project (`drybag.binding`): a clone that names itself after
   one of the owner's projects has no id, so nothing binds it. `git` runs with a scrubbed
   environment, because an inherited `GIT_DIR` would otherwise answer for a different
   repository altogether.

**What the environment can and cannot choose.** The store's *location* is never named by a variable
this process reads: `resolve` takes an `env` mapping and ignores it by contract, and
`gitenv.git_answer` runs with a scrubbed environment, so neither a `DRYBAG_STORE`-shaped variable
nor an inherited `GIT_DIR` can point this module at another project's notes. Both halves are pinned
by tests, and both matter because a committed `.claude/settings.json` may carry an `env` block that
applies with no trust prompt in a non-interactive session.

**The machine file makes the same claim.** `machine_config_path` reads neither
`DRYBAG_CONFIG` nor `XDG_CONFIG_HOME`, since gating one alone is worth nothing: each of them
reaches the same file, and this area routes the store's overlay anchor (`overlay_root(None)`)
through it. A committed `env` block setting `DRYBAG_CONFIG`, or direnv, mise or a devcontainer
setting `XDG_CONFIG_HOME` from a file the clone commits, would choose which overlay root
`permitted_roots` is computed from, wherever no `--machine` is threaded.

Stated exactly, because the exposure is not the same size as the invariant: pointing a variable
somewhere of the author's choosing would **suppress** memory — no overlay root means overlay
stores refuse to resolve. It is still an invariant the binding leans on, and holding it in
`config/machine.py` — the foundation's file — is what makes the claim above about `env` a claim
about the whole module rather than about `resolve` alone.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from drybag import fsops
from drybag.binding import Bound, bound_project, short
from drybag.config.overlay import overlay_root
from drybag.config.paths import PathEscape, PathUnasked, contained, data_directory
from drybag.config.plates import COMMON as COMMON_PLATE
from drybag.config.plates import PROJECTS as PROJECTS
from drybag.config.plates import RECORD as RECORD
from drybag.config.plates import (
    TAG_LINK_PREFIX,
    Plate,
    PlateKind,
    plates,
    read_record,
)
from drybag.config.reading import MachineConfigError
from drybag.config.schema import HARNESS_MODE, LOCAL_ONLY_MODE, OVERLAY_MODE, Config
from drybag.errors import Failure
from drybag.gitenv import git_answer, without_userinfo
from drybag.printed import quoted

LOCAL_STORE = Path(".drybag") / "local" / "memory"
# The surface's name for the project record `config.plates` names.
# The one directory under `projects/<name>/` that holds notes, named once: `permitted_roots`
# builds the path with it, and `attach` states the path's shape with it where it may not print
# the project's name, and lays out the group directories under it.
STORE_DIR = "memory"
COMMON = Path(COMMON_PLATE) / STORE_DIR
# Under the machine's data directory: one composed store per binding id (`memory.composed`).
STORES = "stores"


def composed_directory(id: str) -> Path:
    """`<data>/stores/<id>/`, derived from the id and nothing else: the overlay-mode store."""
    return data_directory() / STORES / id


def stores_holding(path: Path) -> Path | None:
    """`<data>/stores/`, spelled or resolved, where `path` is in a composed store on this machine,
    which no repository commits and no overlay shares; else `None`."""
    try:
        stores = data_directory() / STORES
    except Failure:
        return None
    held = (base for base in (stores, stores.resolve()) if path.is_relative_to(base))
    return next((base for base in held if path != base), None)


def shown(path: Path) -> str:
    """`path` as every message names a store path: one under `<data>/stores/` with the binding id
    it names cut to its short form and an ellipsis (`binding.short`), since the whole id is the
    secret a clone would need and is never printed; any other path whole."""
    base = stores_holding(path)
    if base is None:
        return str(path)
    id, *rest = path.relative_to(base).parts
    return "/".join([str(base), f"{short(id)}…", *rest])


@dataclass(frozen=True)
class Store:
    path: Path
    mode: str
    root: Path
    groups: dict[str, Path] = field(default_factory=dict)
    # The machine file this store was resolved against, carried rather than re-passed.
    #
    # Not an optional keyword on every function that reads the store: a `None` there is not
    # inert — it re-reads the machine file under the owner's home rather than the
    # one `resolve` was given, silently changing `permitted_roots` and the index destination —
    # so a caller that forgot one argument would get a different overlay and a different write
    # target, with nothing to say so.
    #
    # `Store` is frozen and `resolve` builds it exactly once, from the `machine` it was given.
    # Putting the value here makes the mismatch unrepresentable instead of documented.
    machine: Path | None = None


@dataclass(frozen=True)
class Unresolved:
    """Why no store resolved: drybag's own sentence, and the repository's words it is about.

    Two fields because the two reach a reader differently. `said` is built of nothing a
    repository chose, so it prints as it is and a command puts it *before* the region
    `trust.wrap` marks as data — which is where a way out has to be for anyone to act on it.
    `detail` may carry `memory.groups` entries, `paths.memory` or the project's name, and reaches
    a reader only inside that region (`memory.commands._no_store`). Every cause has a sentence of
    its own, so only `detail` may be absent; `str()` joins what there is, for a caller that only
    reports it wrapped.
    """

    said: str
    detail: str | None = None

    def __str__(self) -> str:
        return ": ".join(part for part in (self.said, self.detail) if part)


_WORKTREES = "worktrees"
_BACK_POINTER = "gitdir"


def _registered_worktree(root: Path) -> Path | None:
    """The checkout `root` is genuinely a registered worktree of, or `None` when it is not one.

    Not whether the worktree lives *under* the main checkout: in git's own documented layout,
    `git worktree add ../side` puts the tree beside the checkout, so a containment test would
    answer that there is no store at all for a `local-only` worktree, whose store is the main
    checkout's.

    **Registration is the question containment would only stand in for.** A linked worktree's
    private git directory is `<common-dir>/worktrees/<name>`, and that directory holds a
    `gitdir` file naming the `.git` file the worktree was created for. Both halves are
    checked, because only the first is written by whoever owns `root`: a `.git` may be a plain
    text pointer, so any directory can *claim* to be a worktree of a repository it was never
    added to, and `git rev-parse` will answer for that repository. The back-pointer is the
    half the claimed repository wrote. (A clone cannot ship either — git refuses to track a
    path named `.git` — but the fallback reads a store out of another repository, so it does
    not rest on that.)

    Both queries go through `git_answer`, which scrubs the environment. That is a second lock on
    the same door rather than this one's support: an inherited `GIT_DIR` answers for whatever
    repository it names, and there it is also its own common dir, so a redirected fallback
    fails the registration test above anyway. Because they are redundant, each is pinned by its
    own test rather than by one that dies only when both are gone —
    `test_an_inherited_git_dir_never_reaches_the_git_helper` for the scrubbing and
    `test_a_directory_that_merely_sits_under_a_checkout_is_not_a_worktree_of_it` for this.
    """
    # One after the other, so a first question `git` could not answer is not followed by a
    # second that waits out the same bound.
    refusal = (
        f"`git` could not answer whether {quoted(str(root))} is a registered worktree, so "
        "drybag cannot fall back to the checkout that owns the store"
    )
    common = git_answer(root, "rev-parse", "--path-format=absolute", "--git-common-dir").require(
        refusal
    )
    private = git_answer(root, "rev-parse", "--path-format=absolute", "--git-dir").require(refusal)
    if common is None or private is None:
        return None
    common_dir, private_dir = Path(common).resolve(), Path(private).resolve()
    if private_dir.parent != common_dir / _WORKTREES:
        return None
    try:
        recorded = (private_dir / _BACK_POINTER).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not recorded or Path(recorded).parent.resolve() != root.resolve():
        return None
    owner = common_dir.parent
    return None if owner == root.resolve() else owner


# The one answer to "does this machine bind this checkout to this project", which `memory`
# commands, `attach`, `attach --check`, `doctor` and the session-start line all read
# (`binding_state`). The remote is no part of it: a moved or missing `origin` is a note
# (`origin_note`), never a state that refuses.
UNBOUND = "unbound"
BOUND = "bound"
BINDING_STATES = (UNBOUND, BOUND)

# The cause, and the way out that fits it. Fixed text: the project's name, which the repository
# chooses, goes in `Unresolved.detail` and never in this.
NO_RECORD = (
    "this machine records no binding of this checkout to this project, so nothing binds it to "
    "the overlay; run `drybag attach` to bind it"
)
# The note a bound checkout's `origin` earns when it is not the remote its binding was recorded
# with. Both are repository-authored and arrive through `printed.quoted`; `{origin}` or
# `{recorded}` is `NO_REMOTE_SAID` where there is none.
ORIGIN_MOVED = (
    "this checkout's `origin` is {origin}, and its binding was recorded with {recorded}; "
    "`drybag attach` records the new one"
)
NO_REMOTE_SAID = "no remote"

# Asked before any of the four: a machine record naming an overlay root that is not there (the
# overlay moved, or this machine never cloned it) must not read as "no record of this project",
# whose way out, `drybag attach`, binds only to the root the machine records. The
# root is the owner's own configuration and not the repository's, so it is part of this sentence,
# through `printed.quoted`, rather than a detail printed inside the region that marks repository
# text.
OVERLAY_GONE = (
    "the overlay root the machine configuration records, {root}, is not a directory on this "
    "machine; record where the overlay is now with `drybag setup --preset NAME --overlay "
    "PATH`, then run `drybag attach`"
)


# Two of the resolver's own sentences, for the causes a store most often fails on. Each is
# drybag's text: the path it is about, and the groups that did not resolve, stay in the detail.
STORE_MISSING = "the memory store's directory does not exist"
# Said where no layer chose a store: memory is the agent harness's own, and drybag delivers none.
LEFT_TO_HARNESS = (
    "memory is left to the agent harness, since no layer sets [memory] mode; set it to choose a "
    "store"
)
BUILT_BY_ATTACH = "; run `drybag attach` to build it"


# The three spellings of one repository that `_repository` reads, each up to the host: the scp form
# git hands out for ssh, its `ssh://` URL and https. Each names the forge's own `git` user or no
# user at all, so a URL naming any other account is none of them.
_SCP = "git@"
_SSH = "ssh://git@"
_HTTPS = "https://"
# A host and a path segment are compared only when spelled in these, which every forge's owner and
# repository names are: no userinfo, no port, no percent-escape, no `~`, nothing outside ASCII.
_NAMED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
# The hosts whose paths name an owner and a repository whatever their case. github.com resolves
# `Owner/Repo` and `owner/repo` to one repository; elsewhere a path's case is the server's to
# judge, and drybag does not guess it.
_CASELESS_PATHS = frozenset({"github.com"})


def _repository(url: str) -> tuple[str, str] | None:
    """The host and the path `url` names, when it is one of the three spellings of a repository
    `binding_state` holds equal, else `None`.

    `git@host:owner/repo`, `ssh://git@host/owner/repo` and `https://host/owner/repo` each answer
    `(host, "owner/repo")`, with one `.git` taken off the path, the host in lower case and, on
    github.com alone, the path too. Nothing else is normalised: a path is compared as written, so
    no `..` is resolved and no `/` collapsed, and a segment made of dots alone, an empty one or a
    host with a port or a user in it is not read at all.

    The scp form's path is git's relative to the `git` user's home, and the other two forms' is
    absolute, so on a plain ssh server `git@host:a/b` and `ssh://git@host/a/b` can be two
    directories. Every forge serves both as one repository, which is the case this rule is for;
    telling the two apart would need the server's layout, which no URL carries. Placing a
    repository at the other of the two paths takes write access to the very server the record
    names, so the gap is not one a clone can reach.
    """
    for prefix, separator in ((_SCP, ":"), (_SSH, "/"), (_HTTPS, "/")):
        if url.startswith(prefix):
            host, _, path = url[len(prefix) :].partition(separator)
            break
    else:
        return None
    # A URL with no separator after its host leaves the path empty, which `_named` refuses.
    segments = path.removesuffix(".git").split("/")
    if not (_named(host) and all(_named(part) for part in segments)):
        return None
    host = host.lower()
    path = "/".join(segments)
    return host, path.lower() if host in _CASELESS_PATHS else path


def _named(part: str) -> bool:
    """Whether `part` is a host or a path segment `_repository` compares: spelled in `_NAMED`, and
    not empty and not dots alone, which a server may resolve rather than read."""
    return part.strip(".") != "" and set(part) <= _NAMED


def binding_state(found: Bound | None, project: str) -> str:
    """`bound` when this machine's record for the checkout's id (`binding.bound_project`, which
    the caller read) names `project`, else `unbound` — and never `bound` because nobody looked.

    `project` is the repository's `[project] name`, and it decides nothing alone: a clone that
    commits the name of one of the owner's projects has no id, so `found` is `None`."""
    return BOUND if found is not None and found.project == project else UNBOUND


def origin_note(recorded: str | None, origin: str | None) -> str | None:
    """`ORIGIN_MOVED` naming both remotes, or `None` when `origin` is the one recorded.

    The same remote in another of the three spellings `_repository` reads — `git@host:owner/repo`,
    `ssh://git@host/owner/repo` and `https://host/owner/repo`, with or without `.git` — is the one
    recorded, so a clone made over https of a repository bound over ssh earns no note. Two URLs
    `_repository` cannot read are the same only when equal. No remote on either side is the same
    only as no remote on the other."""
    if recorded == origin:
        return None
    if recorded is not None and origin is not None:
        # One URL with and without the userinfo a remote can carry a token in is one remote: the
        # binding record keeps it without (`gitenv.without_userinfo`), and nothing else is read
        # across that difference, so the spellings below still take no userinfo.
        if without_userinfo(recorded) == without_userinfo(origin):
            return None
        named = _repository(recorded)
        if named is not None and named == _repository(origin):
            return None
    # Each named without its userinfo, which may be a token.
    return ORIGIN_MOVED.format(
        origin=NO_REMOTE_SAID if origin is None else quoted(without_userinfo(origin)),
        recorded=NO_REMOTE_SAID if recorded is None else quoted(without_userinfo(recorded)),
    )


def read_binding_record(record: Path) -> dict[str, str]:
    """The binding record at `record`, `projects/<name>/project.toml`: each key whose value is a
    non-empty string, mapped to it.

    A filter over `config.plates.read_record`, the one reader of that file, and no failure policy:
    an `OSError`, a `UnicodeDecodeError` and each of `UNPARSEABLE` are raised as met, because what
    a record that cannot be read means is each caller's to say. `attach`'s `_recorded` stops the
    run, so a broken record is never written over. The record binds nothing either: the binding is
    the machine's (`drybag.binding`), and the record keeps the remote and the first attach's
    date for the owner to read, beside the `tags` the owner lists. Whether there is a record at all
    is the caller's question too, asked before this.
    """
    return {
        key: value for key, value in read_record(record).items() if isinstance(value, str) and value
    }


def _composed(project: str, root: Path) -> Path | Unresolved:
    """The composed store of the checkout's id where this machine binds that id to `project`, else
    `NO_RECORD`, with the project's name as the detail it is about; the state is `binding_state`'s.
    A `git` that cannot name the common directory raises, as it does for every other question
    here."""
    found = bound_project(root)
    if found is not None and binding_state(found, project) == BOUND:
        return composed_directory(found.id)
    return Unresolved(NO_RECORD, f"project {quoted(project)}")


def _inside(candidate: Path, parent: Path) -> bool:
    """`candidate` is `parent` or sits under it, both resolved first.

    `Path.is_relative_to` and not a hand-rolled `== base or base in parents`, which is the same
    predicate written out longhand, beside `index._resolved_if_permitted`'s short spelling of
    it. Two spellings of a containment rule is one more place for them to stop agreeing.
    """
    return candidate.resolve().is_relative_to(parent.resolve())


def tag_link(tag: str) -> str:
    """The name a tag's notes take in the composed store: `tag-<tag>`."""
    return TAG_LINK_PREFIX + tag


def project_notes(overlay: Path, project: str) -> Path:
    """This project's own notes in the overlay, the last of `permitted_roots` and the one place it
    puts new notes, which asks for no tag."""
    return overlay / PROJECTS / project / STORE_DIR


def _notes(held: Sequence[Plate]) -> tuple[Path, ...]:
    return tuple(plate.directory / STORE_DIR for plate in held)


def permitted_roots(overlay: Path, project: str) -> tuple[Path, ...]:
    """This project's whole share of the overlay: the common notes, the notes of each tag its
    record lists (`config.plates.plates`, in list order) and its own, last."""
    return _notes(plates(overlay, project)[0])


# The one group whose notes are not this project's, and `common/memory` *is* its store rather
# than its parent: this module's own docstring says so — "`developer` points into the overlay's
# `common/memory`, which is shared across projects and cannot live under `projects/<name>/`" —
# and the overlay template's README says it to the owner. Named here beside `COMMON` and
# `permitted_roots` because the composed store is built from this routing; a second spelling is one
# more place for them to stop agreeing.
COMMON_GROUP = "developer"


def reserved(name: str) -> bool:
    """`name` is a link name the project's own memory directories do not take: `developer`, the
    common notes', and anything in the tag namespace, `tag-<tag>` (`TAG_LINK_PREFIX`)."""
    return name == COMMON_GROUP or name.startswith(TAG_LINK_PREFIX)


def share_directories(overlay: Path, project: str) -> tuple[str, ...]:
    """The directories under `projects/<project>/memory/` that are not hidden, sorted; none where
    the share is not there or cannot be listed."""
    share = project_notes(overlay, project)
    try:
        return tuple(
            sorted(
                name
                for name in os.listdir(share)
                if not name.startswith(".") and fsops.is_dir(share / name)
            )
        )
    except OSError:
        return ()


def reserved_project_groups(overlay: Path, project: str) -> tuple[str, ...]:
    """The directories under `projects/<project>/memory/` that are not linked because they are in
    the tag namespace, sorted; `developer` is not among them, having been left out always."""
    return tuple(
        name for name in share_directories(overlay, project) if name.startswith(TAG_LINK_PREFIX)
    )


def overlay_group_target(held: Sequence[Plate], group: str) -> Path:
    """Where one group's notes live inside the overlay, among the plates `held`
    (`config.plates.plates`, read once by the caller).

    A rule and deliberately not a probe over what happens to exist: a group directory that is
    not there yet is a first attach, not a reason to link somewhere else. `developer` is the
    common notes, `tag-<t>` is a listed tag's notes, and any other name is the project's own
    directory. The answer is always inside `permitted_roots`, which is what the resolver then
    checks the link against.
    """
    if group == COMMON_GROUP:
        return held[0].directory / STORE_DIR
    tag = group.removeprefix(TAG_LINK_PREFIX) if group.startswith(TAG_LINK_PREFIX) else None
    for plate in held:
        if plate.kind is PlateKind.TAG and plate.name == tag:
            return plate.directory / STORE_DIR
    return held[-1].directory / STORE_DIR / group


def _declared(root: Path, config: Config) -> Path | Unresolved:
    """`paths.memory` under `root`, or why it is refused, in words that name the cause."""
    try:
        return contained(root, config.paths.memory, allow_final_symlink=True)
    except PathUnasked as exc:
        return Unresolved(
            "a directory on the way to paths.memory cannot be checked for a symlink",
            f"paths.memory ({config.paths.memory!r}) cannot be checked for a symlink ({exc.fault})",
        )
    except PathEscape:
        return Unresolved(
            "paths.memory does not stay inside the project",
            f"paths.memory ({config.paths.memory!r}) does not stay inside the project",
        )


def _group_targets(base: Path, config: Config, overlay: Path | None) -> dict[str, Path]:
    """Each group of `memory.groups` this store holds and may read, by its directory; a group
    whose directory escapes the store, or links where it may not, is left out."""
    groups: dict[str, Path] = {}
    allowed: tuple[Path, ...] = ()
    linked: list[str] = []
    if overlay is not None:
        held = plates(overlay, config.project.name)[0]
        allowed = _notes(held)
        linked = [tag_link(plate.name) for plate in held if plate.kind is PlateKind.TAG]
    # The tags' links last and in the project's list order (`walked_groups`).
    for group in [*config.memory.groups, *linked]:
        try:
            target = contained(base, group, allow_final_symlink=True)
        except PathEscape:
            continue
        if not fsops.exists(target):
            # A group directory the store does not hold is an empty group: it contributes no
            # notes, and the store is still the store (the native writer's files included).
            continue
        if fsops.is_symlink(target):
            if overlay is None:
                continue
            if not any(_inside(target, permitted) for permitted in allowed):
                continue
        groups[group] = target
    return groups


# A store and no reason, or no store and the reason: `resolved`'s answer, which never holds both
# and never neither.
Resolution = tuple[Store, None] | tuple[None, Unresolved]


def _recorded_overlay(machine: Path | None) -> Path | None:
    """The overlay root the machine file records, or `None` where it records none or cannot be
    read.

    A command never meets a machine file it cannot read here: its load refuses that file first
    (`config.loader`). A session does, since it reads the configuration that file leaves it
    (`config.session`), and its `SessionStart` warning already names the file
    (`MACHINE_FILE_WARNING`), so the store resolves as one with no overlay recorded, and the
    session's standing rules and volatile notes still arrive where they are not the overlay's."""
    try:
        return overlay_root(machine)
    except MachineConfigError:
        return None


def _resolve_at(
    root: Path, config: Config, override: str | None, machine: Path | None
) -> Resolution:
    mode = config.memory.mode
    if mode == HARNESS_MODE and override is None:
        # Memory is the harness's own: drybag delivers nothing, so there is no store to read.
        return None, Unresolved(LEFT_TO_HARNESS)
    overlay = _recorded_overlay(machine)
    composed: Path | None = None
    if mode == OVERLAY_MODE:
        if overlay is None:
            return None, Unresolved(
                "no overlay root is recorded in the machine configuration; run `drybag setup`"
            )
        if not fsops.is_dir(overlay):
            return None, Unresolved(OVERLAY_GONE.format(root=quoted(str(overlay))))
        found = _composed(config.project.name, root)
        if isinstance(found, Unresolved):
            return None, found
        composed = found
    if composed is not None:
        # Overlay mode reads the composed store and nothing else, `--store` included: a directory
        # a repository chose never stands in for the store that reaches the overlay's notes.
        base = composed
    elif override is not None:
        base = Path(override).expanduser()
    elif mode == LOCAL_ONLY_MODE:
        try:
            # `contained` with no `allow_final_symlink` refuses a symlink at *any* level
            # between the root and the target, which testing `base.is_symlink()` would not:
            # it never looks at `.drybag` and `.drybag/local`, and a group directory
            # reached *through* one of those is not itself a symlink, so the per-group check
            # below would never run either. That is the same hazard the comment below documents for
            # `paths.memory`, left open one directory higher — and in the mode the preset
            # ships by default, where the whole store is otherwise ungoverned by `contained`.
            base = contained(root, str(LOCAL_STORE))
        except PathUnasked as exc:
            return None, Unresolved(
                "a directory on the way to the local-only store cannot be checked for a symlink",
                str(exc),
            )
        except PathEscape as exc:
            return None, Unresolved(
                "the local-only store is not a real directory inside the project",
                f"{exc}; local-only memory must be a real directory",
            )
    else:
        declared = _declared(root, config)
        if isinstance(declared, Unresolved):
            return None, declared
        base = declared
        # Check 1, the shape: in `in-repo` mode `paths.memory` itself must be a real directory, not
        # one link for the whole store: the store would silently become whatever `paths.memory`
        # was pointed at.
        if fsops.is_symlink(declared):
            return None, Unresolved(
                f"paths.memory is a symlink, and {mode} memory must be a real directory",
                f"{config.paths.memory} is a symlink; {mode} memory must be a real directory",
            )
    if not fsops.is_dir(base):
        # `attach` builds the directory only in overlay mode, where it is the composed store;
        # elsewhere it refuses, so it is named as the way out only there.
        said = STORE_MISSING + (BUILT_BY_ATTACH if mode == OVERLAY_MODE else "")
        return None, Unresolved(said, shown(base))
    groups = _group_targets(base, config, overlay if mode == OVERLAY_MODE else None)
    return Store(base, mode, root, groups, machine), None


def resolved(
    root: Path,
    config: Config,
    *,
    override: str | None = None,
    machine: Path | None = None,
) -> Resolution:
    """The store and, when there is none, why — in **one** pass.

    `resolve` walks the whole resolution too, and a caller that needs both answers (every
    `memory` command does: the store to work with, the reason to report) would otherwise walk it
    twice. Each walk runs up to four `git` queries with a five-second timeout apiece, so a
    hanging `git` would cost a refused command up to forty seconds — inside a `SessionStart`
    hook, once per bundle entry. `resolve` stays, because a caller that wants only the store
    should not have to say so; this is the one for callers that want both.

    The reason's `detail` is built out of `config.paths.memory`, repository-controlled and not
    schema-constrained — a TOML multi-line string carries literal newlines through unchanged —
    so it must never reach model context unwrapped: a consumer that must show it wraps it first
    with `trust.wrap`. Its `said` is drybag's own and prints as it is.

    There is always exactly one of the two, and the type says so (`Resolution`): a caller that
    tests `found[0] is None` holds a reason, and has no "no reason" case to invent words for.
    """
    found = _resolve_at(root, config, override, machine)
    if found[0] is not None:
        return found
    # A worktree resolves through the checkout it is *registered against*, never through
    # whatever a redirected `GIT_DIR` named and never merely because a directory sits above it.
    parent = _registered_worktree(root)
    if parent is None:
        return found
    return _resolve_at(parent, config, override, machine)


def resolve(
    root: Path,
    config: Config,
    *,
    override: str | None = None,
    machine: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Store | None:
    # `env` is accepted and never read: a store is never selected through the
    # environment, and a parameter that exists and is ignored is a claim a test can pin.
    del env
    return resolved(root, config, override=override, machine=machine)[0]


def walked_groups(store: Store, config: Config) -> list[str]:
    """The groups every reader of the store walks, in the order they are read: `memory.groups` as
    configured, each only where the store holds it, and the tags' notes (`tag-<t>`, in the
    project's list order, which the store holds them in) after the common notes, or first where
    the configuration lists none. The one spelling of the walk: no reader keeps its own."""
    tagged = [g for g in store.groups if g.startswith(TAG_LINK_PREFIX)]
    walked = [g for g in config.memory.groups if g in store.groups and g not in tagged]
    at = walked.index(COMMON_GROUP) + 1 if COMMON_GROUP in walked else 0
    return [*walked[:at], *tagged, *walked[at:]]


def in_repository(store: Store, path: Path) -> bool:
    """Whether one particular file this store yields sits in the repository itself: the index
    asks it of `MEMORY.md`, whose destination decides whether `memory.index_extra` is published."""
    return _inside(path, store.root)
