"""The private overlay's rows in `drybag doctor`: `pre-commit` and `overlay-requires`.

Both are about this machine's overlay rather than about the project, and the overlay is this
area's: it renders the overlay, declares the floor `overlay-requires` judges, and ships the
`.pre-commit-config.yaml` whose installation `pre-commit` asks about. `doctor`'s core discovers
this module by name (CONTRIBUTING.md, "Areas") and imports nothing of this area.

The overlay root is the core's answer, `Context.overlay_root`, which the report resolves at most
once for every row that reads it, so both rows read one answer and nothing one run resolved
reaches the next.

Every import sits inside a function body, as in a `hooks.py`: this module is imported by
discovery, and a module-level import here would be one more thing every `doctor` run loads
before it has asked anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from drybag.doctor.api import Context, Contribution, MachineUnread, Row, Status

# The two overlay-gated rows ask one question before anything else, and both must say the same
# thing about it, so the sentences live in one place rather than in a copy per row: a copy carries
# a defect into the other row with it.
#
# An overlay root that is not a recorded directory is three states, with three sentences.
# `Context.overlay_root` answers `None` for "this machine records no overlay", which is the
# ordinary state before `drybag setup` has run and which nothing can be done about from here;
# it answers a `Path` for a recorded root whether or not anything is there. A machine that
# recorded an overlay and then moved it -- the owner reorganising their own directories is the
# ordinary way -- is not told "no overlay root is recorded on this machine", which would be
# false, with an empty remedy under it. It is the second state, not the first, that is worth
# acting on: the overlay is where the notes live, and a recorded root that is not there breaks
# the store as well as these two rows.
NO_OVERLAY_RECORDED = "no overlay root is recorded on this machine"
# The overlay root inside another repository's work tree, `{holder}` the directory holding that
# `.git`, through `printed.quoted`. A warning: where the overlay lives is the owner's choice, and
# `setup` records it anywhere, but a work tree that holds it is one another repository's merge can
# write files into.
NESTED = (
    "the overlay root is inside the work tree of the repository at {holder}, which can put files "
    "into it"
)
NESTED_REMEDY = (
    "move the overlay to a directory no repository's checkout holds, and record it there with "
    "`drybag setup --overlay <path>`"
)
# A third state: the machine file that would record the root could not be read
# (`Context.overlay_root`'s `MachineUnread`). Saying "no overlay root is recorded" of it was false,
# and the `preferences` row, red, names the file and its remedy, so this skip carries none.
MACHINE_UNREAD = (
    "the machine configuration file could not be read, so whether this machine records an "
    "overlay is not known here; the `preferences` row says why"
)
OVERLAY_GONE = (
    "the overlay root this machine records is not a directory, so nothing about the overlay "
    "can be checked from here"
)
OVERLAY_GONE_REMEDY = (
    "put the overlay back where the machine configuration records it, or run `drybag setup "
    "--preset recommended --overlay <path>` to record where it is now"
)


def _overlay_absent(overlay: Path | MachineUnread | None) -> Row:
    """Why there is no overlay to measure, told apart into the two states that are not alike.

    Called by both overlay-gated rows and by nothing else, so the sentence a reader gets is the
    same whichever row they read it in -- the constants above say why the two states are two
    sentences.

    The argument is the root rather than the `Context`, so that the caller's own
    `not isinstance(overlay, Path) or not fsops.is_dir(overlay)` narrows `overlay` to a `Path` for
    the rest of its body. The condition stays at each call site because each row reads the root
    afterwards; what must not be spelled twice is the answer, and it is not.

    Every arm is a `skip` and none reaches the exit code. The remedy is the difference, and
    it follows `Check`'s rule rather than the row's status: "no overlay recorded" is the ordinary
    state of a machine that has not run `drybag setup`, and no command in this row's gift
    changes it; a machine file that could not be read is a fault the `preferences` row names, red
    and with its remedy; a root that is recorded and is not there is a fault on this machine that
    nothing else in the report names, and there is a command for it.
    """
    from drybag.doctor.api import SKIP, MachineUnread, Row

    if overlay is None:
        return Row(SKIP, NO_OVERLAY_RECORDED, "")
    if isinstance(overlay, MachineUnread):
        return Row(SKIP, MACHINE_UNREAD, "")
    return Row(SKIP, OVERLAY_GONE, OVERLAY_GONE_REMEDY)


def _pre_commit(context: Context) -> Row:
    """Whether the overlay's own secret scan is armed on **this** machine.

    The hook lives in a clone's own `.git`, so each machine arms its own: `setup --overlay` runs
    `pre-commit install` in every overlay it makes or records, and says so when it cannot. A
    clone no `setup` recorded, or one recorded where `pre-commit` was missing, is what this finds.
    """
    from pathlib import Path

    from drybag import fsops
    from drybag.doctor.api import OK, WARN, Row
    from drybag.errors import Refusal
    from drybag.gitenv import hooks_dir
    from drybag.overlay.layout import PRE_COMMIT_CONFIG, PRE_COMMIT_HOOK

    overlay = context.overlay_root
    if not isinstance(overlay, Path) or not fsops.is_dir(overlay):
        return _overlay_absent(overlay)
    if not fsops.is_file(overlay / PRE_COMMIT_CONFIG):
        return Row(
            WARN,
            f"the overlay has no {PRE_COMMIT_CONFIG}, so there is no commit-time secret scan "
            f"for the notes it holds",
            "run `drybag overlay upgrade` to refresh the overlay's shipped files",
        )
    try:
        hooks = hooks_dir(overlay)
    except Refusal:
        # `git` is invoked, never imported, and a `git` that cannot answer is a reported finding
        # rather than a traceback -- and rather than a guess at `.git/hooks`, which is wrong for an
        # overlay that keeps its hooks elsewhere (`overlay.layout.PRE_COMMIT_HOOK` says when).
        return Row(
            WARN,
            "`git` could not name the overlay's hooks directory, so whether its commit-time "
            "secret scan is installed cannot be answered here",
            f"run `git -C {overlay} rev-parse --git-path hooks` and read what it says",
        )
    if not fsops.exists(hooks / PRE_COMMIT_HOOK):
        return Row(
            WARN,
            "the overlay's commit-time secret scan is configured and not installed on this "
            "machine; the push-time scan still runs",
            f"run `pre-commit install` in {overlay}",
        )
    return Row(OK, "the overlay's commit-time secret scan is installed")


def _overlay_requires(context: Context) -> Row:
    """Whether the drybag running satisfies the floor the overlay declares, and whether the
    overlay lies where another repository can write into it.

    The floor's verdict comes first, and one that finds something stands; otherwise an overlay
    root inside another repository's work tree is a warning (`NESTED`). That is read off the disk,
    a `.git` in a directory above the root, as `gitenv.in_work_tree` reads it, and runs no `git`.

    A row of its own, gated on a recorded overlay exactly as `pre-commit` is: the subject is
    this machine's overlay, not this project, so a `local-only` project on a machine that
    records one is never red for it -- it is warned instead, which is where the unmet arm below
    splits. The spec string is the owner's own and is printed as `requires_of`
    normalised it.
    """
    import os
    from pathlib import Path

    from drybag import fsops
    from drybag.doctor.api import OK, SKIP, WARN, Row
    from drybag.printed import quoted

    overlay = context.overlay_root
    if not isinstance(overlay, Path) or not fsops.is_dir(overlay):
        return _overlay_absent(overlay)
    row = _floor(overlay, context)
    holder = next((above for above in overlay.parents if os.path.lexists(above / ".git")), None)
    if holder is None or row.status not in (OK, SKIP):
        return row
    return Row(WARN, NESTED.format(holder=quoted(str(holder))), NESTED_REMEDY)


def _floor(overlay: Path, context: Context) -> Row:
    """The floor the overlay declares, against the drybag running: the overlay's requirement
    as the verdict it can be, since an overlay runs nothing and so cannot refuse to."""
    import drybag
    from drybag import REPOSITORY_URL
    from drybag.config.schema import OVERLAY_MODE
    from drybag.doctor.api import OK, RED, SKIP, WARN, Row
    from drybag.overlay.layout import IDENTITY_RECORD
    from drybag.overlay.requires import requires_of, satisfies

    spec = requires_of(overlay)
    if spec is None:
        return Row(SKIP, "the overlay declares no drybag requirement", "")
    running = drybag.__version__
    verdict = satisfies(spec, running)
    if verdict is None:
        return Row(
            WARN,
            "the overlay's `requires` is not a >=X.Y.Z form this drybag reads",
            f"write `requires` in the overlay's {IDENTITY_RECORD} as >=X.Y.Z",
        )
    if not verdict:
        # **Red only when this project consults the overlay**, which is the reason this
        # requirement has a row rather than being folded into `versions`: a `local-only` project
        # on a machine that records an overlay must not go red for a requirement it has no
        # relationship with. `memory.mode` is what says whether this repository keeps its
        # notes in the overlay, and red is a statement that *this installation* is wrong -- it
        # gates the exit code. The machine owner is still told, at the level `pre-commit` uses
        # in its analogous machine-scoped state. `memory.mode` is compared and never printed,
        # exactly as the `attached` row compares it.
        consults = context.config is not None and context.config.memory.mode == OVERLAY_MODE
        unmet: Status = RED if consults else WARN
        return Row(
            unmet,
            f"the overlay requires drybag {spec} and {running} does not satisfy it",
            f"install a drybag that satisfies {spec}: uv tool install git+{REPOSITORY_URL}@<tag>",
        )
    return Row(OK, f"the overlay requires drybag {spec}, which {running} satisfies")


def register() -> Contribution:
    """The two overlay rows, both reading the report's one overlay root."""
    from drybag.doctor.api import Contribution

    return Contribution(
        checks=(
            ("pre-commit", _pre_commit),
            ("overlay-requires", _overlay_requires),
        )
    )
