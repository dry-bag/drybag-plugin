"""When a session's hook event starts the note sync's job, a sleeper, or nothing.

Imported by `sync.hooks` only once the machine file switches the sync on. `decide` reads one `git
status` of the overlay and the state file, and one `rev-parse` of `origin/main` when an unchanged
hold or failure would back it off, and answers one of four things: **run** the job now;
**schedule** it, marking a trigger pending and spawning a sleeper that waits out the throttle (the
job's own `sleeper.lock` makes a second sleeper exit at once, so a burst of turns is one run);
**pending**, a mark alone, while a job runs, whose next round takes it; or **none**.

A `Stop` or `SessionEnd` starts a job when a memory path is dirty or untracked, when `@{u}..HEAD`
holds commits, or when the last outcome is not settled or a hold is recorded; a session start
always does, to fetch, except after a compaction, which is the same session going on. While a
hold or a failure only its owner can clear is recorded and neither `HEAD`, the upstream ref nor
the dirty notes changed since the last job, a `Stop` and a `SessionEnd` wait
(`state.backed_off`), unpushed commits included, since a local hold's own commits keep
`@{u}..HEAD` non-empty. A trigger inside the throttle is scheduled, never dropped; `SessionEnd` is
never throttled, since no later turn would pick it up.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from drybag import fsops
from drybag.config.overlay import overlay_root
from drybag.hooks.api import HookEvent
from drybag.sync import job
from drybag.sync.git import GitError, rev_or_none
from drybag.sync.rules import Status, status
from drybag.sync.state import (
    LOCK_FILE,
    SETTLED,
    THROTTLE_SECONDS,
    Lock,
    StateUnreadable,
    SyncState,
    backed_off,
    mark_pending,
    notes_mark,
    parse_time,
    read_state,
    state_directory,
    throttled,
)

# A named cap: how long each of the trigger's own git questions may take. `hooks/hooks.json`
# gives the `Stop` entry ten seconds, which hold two of them (the status and the upstream ref)
# and the interpreter's start-up; what the harness allows a `SessionEnd` hook is its own.
STATUS_TIMEOUT = 3.0


def _tree(overlay: Path) -> Status | None:
    """The overlay's memory status and branch line (`rules.status`); `None` if it fails."""
    try:
        return status(overlay, timeout=STATUS_TIMEOUT)
    except GitError:
        return None


def _backed_off(overlay: Path, state: SyncState, tree: Status | None, now: datetime) -> bool:
    """Whether a standing hold or failure, and the dirty notes and unpushed commits, are old
    news."""
    if tree is None or tree.head is None or not backed_off(state, tree.head, now):
        return False
    # Only now are the notes and the upstream ref worth reading: the owner's own fetch is news.
    dirty = notes_mark(overlay, tree.paths)
    upstream = rev_or_none(overlay, "refs/remotes/origin/main^{commit}", timeout=STATUS_TIMEOUT)
    return backed_off(state, tree.head, now, dirty=dirty, upstream=upstream)


def _triggered(
    event: str, overlay: Path, state: SyncState | None, now: datetime, tree: Status | None
) -> bool:
    """Whether a `Stop` or `SessionEnd` finds something for a job to do. `SessionEnd` waits out
    the back-off only under a hold, which keeps its notes here whatever runs: no later turn of
    the session would retry a failure a rerun clears."""
    news = tree is not None and (tree.ahead > 0 or bool(tree.paths))
    if state is None:
        return news
    if (event != "SessionEnd" or state.holds.any) and _backed_off(overlay, state, tree, now):
        return False
    return news or state.outcome.state not in SETTLED or state.holds.any


def _busy(d: Path) -> bool:
    """Whether a job holds `job.lock` now. Taking it for an instant is safe: a job that found it
    busy in that instant only marked pending, and the run or sleeper spawned next follows it."""
    try:
        lock = Lock.try_acquire(d / LOCK_FILE)
    except OSError:
        return False
    if lock is None:
        return True
    lock.release()
    return False


def decide(
    event: str, source: str | None, overlay: Path, d: Path, now: datetime
) -> tuple[str, datetime | None]:
    """`("run", None)`, `("schedule", <wake time>)`, `("pending", None)` or `("none", None)` for
    `event` with the session start's `source`, the overlay at `overlay` and the state directory
    `d`."""
    if not fsops.exists(overlay / fsops.CONTROL_DIRECTORY):
        return "none", None
    try:
        state, unreadable = read_state(d), False
    except StateUnreadable:  # a state nobody can read is not `ok`; the job replaces it
        state, unreadable = None, True
    if event == "SessionStart":
        if source == "compact":
            return "none", None
    elif not (unreadable or _triggered(event, overlay, state, now, _tree(overlay))):
        return "none", None
    if event == "SessionEnd":
        return "run", None
    if _busy(d):
        return "pending", None  # the running job's next round takes it
    last = state.last_job_at if state is not None else None
    if last is not None and throttled(last, now):
        return "schedule", parse_time(last) + timedelta(seconds=THROTTLE_SECONDS)
    return "run", None


def trigger(event: HookEvent) -> None:
    """The handler's work once the sync is on: decide, mark a trigger pending, and spawn the job
    or a sleeper."""
    overlay = overlay_root(None)
    if overlay is None:
        return
    d = state_directory()
    source = event.raw.get("source")
    named = source if isinstance(source, str) else None
    decision, wake = decide(event.name, named, overlay, d, datetime.now(UTC))
    if decision in ("schedule", "pending"):
        mark_pending(d)
    if decision in ("run", "schedule"):
        job.spawn(overlay, wake=wake)
