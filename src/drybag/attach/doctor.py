"""The binding's row in `drybag doctor`: the delivery and the memory pointer.

`attached` is this area's: it reads the attach ledger this area writes, in the git common
directory, asks this machine, through this area's own `read_binding`, whether a binding stands
behind it, and then reads what the binding delivers: the render `attach` left under the
machine's data directory, the paths it placed in the main checkout, and the `autoMemoryDirectory`
key there. Every checkout of a repository reads the main checkout's settings file, since `attach`
writes there alone. `doctor`'s core discovers this module by name (CONTRIBUTING.md, "Areas") and
imports nothing of this area.

The overlay root is the core's answer, `Context.overlay_root`, resolved at most once per report
for every row that reads it. The attach ledger comes from the `_Ledger` this module's `register()`
creates, one per report, so every question the row asks of the file gets one answer.

Every import sits inside a function body, as in a `hooks.py`: this module is imported by
discovery, and a module-level import here would be one more thing every `doctor` run loads
before it has asked anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple

if TYPE_CHECKING:
    from pathlib import Path

    from drybag.attach.binding import Binding
    from drybag.attach.write import AttachLedger
    from drybag.config.schema import Config
    from drybag.doctor.api import Context, Contribution, Row, Status


# What `_ledger_state` answers beside a ledger: no file at the ledger's path, and a file there that
# cannot be read as one. Not statuses and not sentences: each reader decides both.
NO_LEDGER: Final = "no-ledger"
# Also the fourth of `_binding_answer`'s reasons, below: the same fact, and it is about the
# repository rather than about this machine.
UNREADABLE_LEDGER: Final = "unreadable-ledger"


def _ledger_state(root: Path) -> AttachLedger | str:
    """This repository's attach ledger, `NO_LEDGER` when no file is there, or `UNREADABLE_LEDGER`
    for `read_ledger`'s `NotALedger`. `root` is the main checkout (`_owner`); the ledger is in the
    git common directory, so any checkout names it. Where git cannot be run, the row cannot say
    (`UNASKABLE`); in no repository there is no ledger.
    """
    from drybag.attach.write import NotALedger, read_ledger
    from drybag.binding import LEDGER_FILE, common_directory
    from drybag.errors import Failure
    from drybag.gitenv import GitUnavailable

    try:
        recorded = read_ledger(common_directory(root) / LEDGER_FILE)
    except NotALedger:
        return UNREADABLE_LEDGER
    except GitUnavailable:
        return UNASKABLE
    except Failure:
        return NO_LEDGER
    return NO_LEDGER if recorded is None else recorded


class _Ledger:
    """`_ledger_state` for one report, read on first use and never again.

    Created once per report by `register()`, so every question `attached` asks of the file gets
    one answer: no arm can read the ledger as there while another reads it as absent or
    unreadable.
    """

    def __init__(self) -> None:
        self._state: tuple[AttachLedger | str] | None = None

    def state(self, root: Path) -> AttachLedger | str:
        if self._state is None:
            self._state = (_ledger_state(root),)
        return self._state[0]


# The remedy for an overlay project record that cannot be read: it binds nothing, so the binding
# is read all the same, and the owner repairs the file, often a merge's conflict markers.
RECORD_REPAIR: Final = "repair that file in the overlay, often a merge's conflict markers"

# What the delivery is, as `_delivery` reads it: each clause is said in the row's detail beside the
# binding, formatted only with drybag's own words and paths this machine chose, through
# `printed.quoted`, and never with the whole binding id (`binding.short`). Red where nothing loads,
# or where git shows what `attach` keeps for this machine alone; a warning where what loads is not
# what the overlay holds now.
DELIVERY_IN_PLACE: Final = "the delivery is in place"
OVERLAY_GONE: Final = (
    "the overlay this machine records, {path}, is not there, so the delivered hooks that run its "
    "scripts have nothing to run"
)
PLACED_TRACKED: Final = "this repository tracks {paths}, which attach placed for this machine alone"
PLACED_SHOWN: Final = (
    "git would not ignore {paths}, which attach placed for this machine alone: a `.gitignore` line "
    "starting with `!` re-includes it"
)
PLACED_UNASKED: Final = (
    "git could not say whether the paths attach placed are tracked or ignored, or whether a "
    "tracked file names the delivery"
)
ID_COMMITTED: Final = (
    "a tracked file names this checkout's delivery, and with it the binding id, which never leaves "
    "this machine: {paths}"
)
UNINSTALLED: Final = (
    "the ledger records no install of the overlay's plugin, so none of the overlay's hooks, skills "
    "or agents is delivered"
)
RENDER_GONE: Final = (
    "the render directory of binding {short} under {parent} is gone, so none of the overlay's "
    "hooks, skills or agents loads, and nothing else says so"
)
MARKETPLACE_GONE: Final = (
    "the render of binding {short} has no {file}, so its plugin does not load, and nothing else "
    "says so"
)
LAUNCHER_GONE: Final = (
    "the launcher the overlay's path-scoped rules run through, {path}, is not there, so those "
    "rules are not delivered"
)
DRIFTED: Final = (
    "the overlay's hooks, skills or agents changed since the last attach, so what runs is the "
    "render that attach left"
)
OVERLAY_UNREAD: Final = (
    "the overlay's hooks, skills or agents could not be read, so whether they changed since the "
    "last attach is not known"
)
# What the project's tags leave unresolved, as `plates` and `attach.tags` say it: a warning, never
# a refusal, since the order of `tags` settles every collision.
RESERVED_GROUP: Final = (
    "the memory directory {name} of the project starts with `tag-`, which is a tag's link name in "
    "the composed store, so it is not linked"
)
TAGS_REMEDY: Final = (
    "in the overlay, fix each tag, record or directory a note names; for a name two tags hold, "
    "rename one of them or reorder `tags` in the project's `project.toml`"
)


class _Finding(NamedTuple):
    """One thing `_delivery` found: its status, the clause the row says it in, and its remedy."""

    status: Status
    clause: str
    remedy: str


def _worst(findings: list[_Finding]) -> _Finding | None:
    """The first red finding, else the first warning, else `None`."""
    from drybag.doctor.api import RED

    return next((each for each in findings if each.status == RED), None) or next(
        iter(findings), None
    )


def _delivery(
    context: Context, main: Path, recorded: AttachLedger, bound: Binding, id: str
) -> _Finding:
    """What the binding delivers, read where `attach` left it.

    **Red where nothing loads, or where git would show what is this machine's alone.** Claude Code
    runs the plugin from the render directory in place, and with that directory or its marketplace
    file gone it loads nothing and says nothing, so this row is the one place that says so; the
    overlay gone is the same for the hooks that run its scripts. A placed path that is tracked, or
    that a `.gitignore` re-includes, is one a collaborator gets, and a tracked file naming the
    delivery carries the binding id, the name a clone would need (`drybag.binding`).

    **A warning where what loads is not what the overlay holds now**: no install recorded, the
    launcher the path-scoped rules run through gone, the overlay's parts changed since the render
    (its digest against the ledger's, computed with the launcher the render holds, so a report run
    through another launcher reads no drift for that alone), the `origin` note, and, last, what the
    project's tags leave unresolved (`attach.tags`) and the project's memory directories in the tag
    namespace, which the composed store does not link. The digest is a convenience and refuses
    nothing (`attach.render`).

    Every finding is read, and the first red one answers, else the first warning: no order of the
    reads lets a warning stand in front of a red."""
    from pathlib import Path

    from drybag import fsops
    from drybag.attach import ATTACH_COMMAND, exclude
    from drybag.attach.render import (
        MARKETPLACE_FILE,
        digest,
        launcher,
        marketplace_name,
        read_parts,
        recorded_command,
    )
    from drybag.attach.tags import collisions
    from drybag.attach.write import named_placed, placed_paths
    from drybag.binding import delivery_directory, short
    from drybag.config.overlay_layers import read as read_layers
    from drybag.doctor.api import OK, RED, WARN
    from drybag.errors import Failure, Refusal
    from drybag.gitenv import git_run
    from drybag.memory.api import reserved_project_groups
    from drybag.printed import quoted

    rerun = f"run `{ATTACH_COMMAND}`"
    found: list[_Finding] = []
    if not fsops.is_dir(bound.overlay):
        found.append(
            _Finding(
                RED,
                OVERLAY_GONE.format(path=quoted(str(bound.overlay))),
                "move the overlay back, or run `drybag setup --overlay <path>` to record where "
                f"it is now; then {rerun}",
            )
        )
    # The paths `attach` placed, in the main checkout, where it places them from any worktree.
    placed = placed_paths(recorded.rules)
    try:
        tracked = [named_placed(held)] if (held := exclude.tracked(main, placed)) else []
        shown = [named_placed(held)] if (held := exclude.unignored(main, placed)) else []
    except Refusal:
        tracked, shown, unasked = [], [], True
    else:
        unasked = False
    if tracked:
        found.append(
            _Finding(
                RED,
                PLACED_TRACKED.format(paths=", ".join(tracked)),
                f"take each out of the index with `git rm --cached`, then {rerun}",
            )
        )
    if shown:
        found.append(
            _Finding(
                RED,
                PLACED_SHOWN.format(paths=", ".join(shown)),
                f"take that line out of the `.gitignore`, then {rerun}",
            )
        )
    # Every tracked file, in the work tree's copy, asked for the whole name and never the prefix,
    # which this repository's own sources spell: only this binding's id is the secret.
    delivered = marketplace_name(id)
    code, out = git_run(context.root, "grep", "-l", "-z", "-I", "-F", "-e", delivered)
    # Exit 1 is "no file names it", an answer; anything but 0 and 1 is git not answering, which is
    # never read as "none".
    if unasked or code not in (0, 1):
        found.append(
            _Finding(
                WARN,
                PLACED_UNASKED,
                "run `drybag doctor` again where `git ls-files`, `git check-ignore` and `git "
                "grep` work",
            )
        )
    if code == 0:
        named = [quoted(name) for name in out.split("\0") if name]
        found.append(
            _Finding(
                RED,
                ID_COMMITTED.format(paths=", ".join(named)),
                "take the name out of each file named above before it is pushed: a clone that "
                "carries it can name this machine's delivery",
            )
        )
    directory = delivery_directory(id)
    render = recorded.render
    if render is None:
        found.append(_Finding(WARN, UNINSTALLED, f"{rerun}, which installs it"))
    elif not fsops.is_dir(directory):
        found.append(
            _Finding(
                RED,
                RENDER_GONE.format(short=short(id), parent=quoted(str(directory.parent))),
                f"{rerun}, which renders it again",
            )
        )
    elif not fsops.is_file(directory / MARKETPLACE_FILE):
        found.append(
            _Finding(
                RED,
                MARKETPLACE_GONE.format(short=short(id), file=MARKETPLACE_FILE),
                f"{rerun}, which renders it again",
            )
        )
    command = recorded_command(directory)
    executable = None if command is None else launcher(command)
    if executable is not None and not fsops.exists(Path(executable)):
        found.append(
            _Finding(
                WARN,
                LAUNCHER_GONE.format(path=quoted(executable)),
                f"{rerun} from the drybag you run now, which renders the entry through it",
            )
        )
    if render is not None:
        try:
            now = digest(id, read_parts(bound.plates, command=command))
        except (Failure, Refusal, OSError):
            found.append(
                _Finding(WARN, OVERLAY_UNREAD, f"run `{ATTACH_COMMAND} --check`, which says why")
            )
        else:
            if now != render.digest:
                found.append(
                    _Finding(WARN, DRIFTED, f"{rerun}, which renders the overlay as it is now")
                )
    if bound.note is not None:
        found.append(_Finding(WARN, bound.note, rerun))
    if bound.fault is not None:
        found.append(_Finding(WARN, bound.fault, RECORD_REPAIR))
    # Last, so a record that cannot be read answers with its own fault and not with the note that
    # the same record lists no tags.
    said = [
        *bound.plate_notes,
        *collisions(bound.plates, read_layers(main, machine=context.machine)[0]),
        *(
            RESERVED_GROUP.format(name=quoted(name))
            for name in reserved_project_groups(bound.overlay, bound.project)
        ),
    ]
    if said:
        found.append(_Finding(WARN, "; ".join(said), TAGS_REMEDY))
    return _worst(found) or _Finding(OK, DELIVERY_IN_PLACE, "")


def _attached(context: Context, config: Config, ledger: _Ledger) -> Row:
    """The binding, what it delivers, and, where the notes are composed, the memory pointer.

    **Delivery does not depend on the memory mode** (`attach.write`), so the delivery is read for
    every repository whose ledger this machine's binding confirms; the pointer only where
    `memory.mode` is `overlay`. With no ledger, a repository whose notes are not composed is
    simply not attached, which is green.

    Every checkout reads the main checkout's ledger and settings file (`_owner`): `attach` writes
    both there, whichever worktree it ran from, and Claude Code reads that settings file from every
    worktree. The delivery and the pointer are said side by side, and the row takes the worse
    status of the two and the worse one's remedy first.
    """
    from drybag.attach import ATTACH_COMMAND
    from drybag.binding import LEDGER_NAME
    from drybag.config.machine import owner_home
    from drybag.config.schema import OVERLAY_MODE
    from drybag.doctor.api import OK, RED, WARN, Row
    from drybag.memory.api import UNBOUND

    main = _owner(context)
    linked = config.memory.mode == OVERLAY_MODE
    if ledger.state(main) == NO_LEDGER:
        if not linked:
            return Row(OK, f"{LEDGER_NAME} does not exist, so nothing records an attach")
        return Row(
            WARN,
            f"memory.mode is overlay and {LEDGER_NAME} does not exist, so nothing records an "
            f"attach",
            f"run `{ATTACH_COMMAND} --check`",
        )
    if owner_home() is None:
        # This machine's state and not a broken check: the render and the composed store are
        # under the home's data directory, so it is a warning that says what it costs.
        return Row(
            WARN,
            "HOME names no home directory, so there is no data directory to check the delivery in",
            "set HOME to your home directory",
        )
    answer = _binding_answer(context, config, ledger, main)
    # The ledger exists, so from here on this row's job is to say what **this machine** makes of
    # it: whether the overlay and the binding stand behind it. Every arm below but the last refuses
    # to print the word "attached".
    if isinstance(answer, str):
        return _uncorroborated(answer)
    state = answer.state
    from drybag.attach.binding import OVERLAY_REPAIR, damaged_overlay
    from drybag.binding import read_id
    from drybag.errors import Failure

    # An overlay whose root or `projects/` is a file holds none of this checkout's notes or grants,
    # bound or not; the remedy for that is the overlay's repair, and never removing the ledger,
    # which is this checkout's record of a real attach.
    damaged = damaged_overlay(answer.overlay)
    if damaged is not None:
        return Row(
            WARN,
            f"{LEDGER_NAME} records an attach, but {damaged} is not a directory, so the overlay "
            f"this machine records is damaged and holds none of this checkout's notes or grants",
            OVERLAY_REPAIR.format(path=damaged, command="`drybag doctor`"),
        )
    # The id `bound_project` read to answer `bound`; read again for the render it names, and none
    # (git gone since, or the id removed in between) is no binding, as `unbound` is.
    try:
        id = read_id(context.root)
    except Failure:
        id = None
    if state == UNBOUND or id is None:
        return Row(
            WARN,
            f"{LEDGER_NAME} records an attach, but this machine records no binding of this "
            f"checkout to this project",
            _re_attach(),
        )
    recorded = ledger.state(main)
    if isinstance(recorded, str):  # `_binding_answer` answered a binding, so never
        return _uncorroborated(recorded)
    delivery = _delivery(context, main, recorded, answer, id)
    if not linked:
        return Row(
            delivery.status, f"attached; {delivery.clause}; the binding is {state}", delivery.remedy
        )
    pointer = _pointer(main, id)
    rank = {OK: 0, WARN: 1, RED: 2}
    pair = (delivery.status, delivery.remedy), (pointer.status, pointer.remedy)
    (status, worse), (_, other) = sorted(pair, key=lambda each: -rank[each[0]])
    return Row(
        status,
        f"attached; {delivery.clause}; {pointer.clause}; the binding is {state}",
        "; ".join(dict.fromkeys(remedy for remedy in (worse, other) if remedy)),
    )


# Why the overlay could not corroborate the ledger, as `_binding_answer`'s three answers. Not
# statuses and not sentences: the row below decides both, and these are the question's own
# vocabulary. `UNRESOLVED` is about the store the ledger names, the other two about this machine.
UNRESOLVED: Final = "unresolved"
NO_OVERLAY: Final = "no-overlay"
UNASKABLE: Final = "unaskable"
# About this machine too: its machine file could not be read (`Context.overlay_root`'s
# `MachineUnread`), so whether it records an overlay is not known. Read as `NO_OVERLAY` it told the
# owner the machine records none, and as `UNASKABLE` that `git` or the overlay was at fault.
MACHINE_UNREAD: Final = "machine-unread"
# The fourth is `UNREADABLE_LEDGER`, above, and it is about the ledger rather than about this
# machine: a ledger that is there and will not parse. Told with the other two, the row would blame
# `git` or an unreadable record and send the reader to run `drybag doctor` where `git` runs,
# which cannot fix it, and its `skip` never reaches the exit code, so a malformed ledger would go
# unreported.


def _re_attach() -> str:
    """The remedy for a ledger the overlay does not corroborate. A function only because the
    command it names is imported, and an import here sits inside a function body."""
    from drybag.attach import ATTACH_COMMAND

    return f"run `{ATTACH_COMMAND} --check`"


def _uncorroborated(reason: str) -> Row:
    """The row for a ledger the overlay did not confirm, split by what the reason is *about*.

    `warn` is for a ledger the machine can act on and `skip` for one it cannot judge here, and the
    split is the point: `skip` never reaches the exit code, so using it for a ledger that is wrong
    would silence it, and using `warn` for a machine where `setup` has never run would make
    `doctor` warn on every correct fresh install. Neither row ever says "attached".

    **Four reasons and not three.** A ledger that is there and will not parse warns. Answered with
    the machine-side two, its row would blame `git` for a malformed file and offer a remedy — run
    this somewhere `git` works — that could not fix it.
    """
    from drybag.attach import ATTACH_COMMAND
    from drybag.binding import LEDGER_NAME
    from drybag.doctor.api import SKIP, WARN, Row

    if reason == UNRESOLVED:
        return Row(
            WARN,
            f"{LEDGER_NAME} records an attach, and the store it names is not this project's "
            f"directory inside the overlay this machine records",
            _re_attach(),
        )
    if reason == UNREADABLE_LEDGER:
        return Row(
            WARN,
            f"{LEDGER_NAME} is here and cannot be read as a ledger, so nothing in it can be "
            f"corroborated and this checkout's attach state is unknown",
            f"remove {LEDGER_NAME}, then run `{ATTACH_COMMAND} --check`",
        )
    if reason == MACHINE_UNREAD:
        # No remedy of its own: the `preferences` row is red on the same file, and names it and
        # what to do.
        return Row(
            SKIP,
            f"{LEDGER_NAME} records an attach and the machine configuration file could not be "
            f"read, so whether this checkout is attached could not be answered here; the "
            f"`preferences` row says why",
            "",
        )
    if reason == NO_OVERLAY:
        return Row(
            SKIP,
            f"{LEDGER_NAME} records an attach and this machine records no overlay to check it "
            f"against, so whether this checkout is attached could not be answered here",
            "run `drybag setup --overlay <path>` to record the overlay, then `drybag doctor` again",
        )
    return Row(
        SKIP,
        f"{LEDGER_NAME} records an attach and the overlay could not be asked about it here — no "
        f"`git`, or an overlay record this process could not read — so whether this checkout "
        f"is attached could not be answered",
        "run `drybag doctor` again where `git` runs and the overlay is readable",
    )


# What the memory pointer is, as `_pointer` reads it: each is said in the row's detail beside the
# delivery, and none prints the pointer's value, which names the whole binding id. `{settings}` is
# the harness's local settings file (`attach.permissions.LOCAL_SETTINGS`).
POINTER_IN_PLACE: Final = "autoMemoryDirectory points the harness at the composed store"
STORE_GONE: Final = (
    "autoMemoryDirectory points the harness at the composed store, which is gone, so the harness "
    "reads no memory"
)
STORE_DANGLES: Final = (
    "autoMemoryDirectory points the harness at the composed store, and a link in it names a place "
    "that is not there, so the harness reads none of the notes behind it"
)
POINTER_MISSING: Final = (
    "the main checkout's {settings} holds no autoMemoryDirectory, so the harness reads none of "
    "the overlay's notes"
)
POINTER_ELSEWHERE: Final = (
    "autoMemoryDirectory in the main checkout's {settings} names another directory than the "
    "composed store, so the harness may read notes this repository is not bound to"
)
POINTER_UNREAD: Final = (
    "the main checkout's {settings} could not be read as settings, so where the harness reads "
    "memory is not known"
)


def _pointer(main: Path, id: str) -> _Finding:
    """What the `autoMemoryDirectory` key in the main checkout's `settings.local.json` says, which
    is the one value `attach` writes there in overlay mode: the composed store the binding id
    names. Red where the harness reads no memory or another directory; a warning where the key is
    missing or the file cannot be read as settings, since what the harness makes of it then is
    the harness's."""
    from drybag import fsops
    from drybag.attach import ATTACH_COMMAND
    from drybag.attach.permissions import LOCAL_SETTINGS
    from drybag.attach.write import POINTER_KEY
    from drybag.doctor.api import OK, RED, WARN
    from drybag.jsonobject import json_object
    from drybag.memory.api import composed_directory

    rerun = f"run `{ATTACH_COMMAND}`"
    unread = POINTER_UNREAD.format(settings=LOCAL_SETTINGS)
    composed = composed_directory(id)
    try:
        text = fsops.read_regular_text(main / LOCAL_SETTINGS, newline="")
    except (FileNotFoundError, NotADirectoryError):
        text = ""
    except (OSError, UnicodeDecodeError):
        return _Finding(WARN, unread, f"make it one JSON object, then {rerun}")
    try:
        document = json_object(text, LOCAL_SETTINGS, error=ValueError) if text.strip() else {}
    except ValueError:
        return _Finding(WARN, unread, f"make it one JSON object, then {rerun}")
    value = document.get(POINTER_KEY)
    if value is None:
        missing = POINTER_MISSING.format(settings=LOCAL_SETTINGS)
        return _Finding(WARN, missing, f"{rerun}, which writes it")
    if value != str(composed):
        elsewhere = POINTER_ELSEWHERE.format(settings=LOCAL_SETTINGS)
        return _Finding(RED, elsewhere, f"remove {POINTER_KEY} from that file, then {rerun}")
    if not fsops.is_dir(composed):
        return _Finding(RED, STORE_GONE, f"{rerun}, which builds it again")
    # A link whose target is gone: the overlay moved and no attach has relinked the store since.
    if any(fsops.is_symlink(link) and not fsops.exists(link) for link in composed.iterdir()):
        return _Finding(WARN, STORE_DANGLES, f"{rerun}, which links it again")
    return _Finding(OK, POINTER_IN_PLACE, "")


def _owner(context: Context) -> Path:
    """The main checkout, where `attach` writes the ledger and the settings file whichever worktree
    it ran from. A bare repository's worktree, which names none, and a `git` that cannot say leave
    this checkout, the one answer that needs no `git`; the binding row above has already asked
    `git` by then, so this is the race and not the layout."""
    from drybag.binding import main_checkout_or_none
    from drybag.errors import Failure, Refusal

    try:
        return main_checkout_or_none(context.root) or context.root
    except (Failure, Refusal):
        return context.root


def _binding_answer(context: Context, config: Config, ledger: _Ledger, main: Path) -> Binding | str:
    """The overlay binding this repository would attach under, or the label of why there is none.

    Three different situations are three answers and never one `None` — a ledger naming a store
    the overlay does not permit, a machine that records no overlay at all, and a machine with no
    usable `git` — because collapsed, the `attached` row would read the last two as *attached*.
    They are not one finding. A ledger whose store is not this project's share of the recorded
    overlay is a fact about **the ledger**, so it earns a warning. A missing overlay or a missing
    `git` is a fact about **our own inputs**, and a row that accused the ledger on it would be
    reporting on itself.
    So is a machine file that could not be read (`MACHINE_UNREAD`), asked first as `NO_OVERLAY` is:
    `read_binding` would raise its refusal as a `Failure`, read as `UNASKABLE`, which blames `git`.

    The three are told apart without restructuring `read_binding`, which raises `Refusal` for
    two of them: `context.overlay_root` is the answer of the same `overlay_root(machine)` that
    function calls with the same argument, so asking it first takes the overlay-is-missing arm
    off the table, and what is left of `Refusal` is the store that is not this project's
    permitted root. Anything else that goes wrong — a `git` that cannot be asked, a ledger this
    process may not read — answers `UNASKABLE`, which is the conservative
    direction: it never accuses the repository for something it may not have done.
    """
    from pathlib import Path

    from drybag.attach.binding import read_binding
    from drybag.doctor.api import MachineUnread
    from drybag.errors import Failure, Refusal
    from drybag.gitenv import GitUnavailable

    if context.overlay_root is None:
        return NO_OVERLAY
    if isinstance(context.overlay_root, MachineUnread):
        return MACHINE_UNREAD
    recorded = ledger.state(main)
    # A file that will not parse is a fact about the ledger and not our inputs, so it does not join
    # the other two; `UNASKABLE` is `_ledger_state`'s own, from a `git` that cannot be run.
    # `NO_LEDGER` never reaches here: `_attached` answers it before asking.
    if isinstance(recorded, str):
        return recorded
    try:
        # The report's own `Config`, which `read_binding` would otherwise load a second time.
        return read_binding(
            context.root, store=Path(recorded.store), machine=context.machine, config=config
        )
    except Refusal:
        return UNRESOLVED
    except (Failure, GitUnavailable):
        return UNASKABLE


def register() -> Contribution:
    """The `attached` row, with one `_Ledger` per report."""
    from drybag.doctor.api import Contribution, configured

    ledger = _Ledger()
    # `configured`: which store, which tree and whether the overlay is consulted at all are the
    # configuration `attach` acted on (`Context.attach_config`), so with none the row skips.
    attached = configured(lambda context, config: _attached(context, config, ledger), attach=True)
    return Contribution(checks=(("attached", attached),))
