"""What the note sync's hooks and its detached job share, in the data directory.

The hooks decide and the job acts; they meet only in `state_directory()`: `state.json`, which the
job writes and `doctor` reads, `job.lock` held by the running job, `sleeper.lock` held by the one
sleeper waiting out the throttle, `pending`, a trigger that arrived while the job was busy, and
`scratch/`, which the job removes whole.

An outcome and a hold are different: the outcome is what the last job did, a hold a range the sync
refuses to move until its owner acts, and a later outcome (`offline`, say) never clears a hold. A
state file that is there and cannot be read is `StateUnreadable`, apart from none at all: `doctor`
says "unknown" for the first and "never ran" for the second.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.config.paths import data_directory
from drybag.gitenv import answer_bytes
from drybag.jsonobject import json_object

OUTCOMES: tuple[str, ...] = (
    "ok", "skipped", "conflict", "raced", "rejected", "offline", "auth_failed", "timeout",
    "hook_failed", "scan_unavailable", "signing_failed", "secret_found", "waiting_clean_tree",
)  # fmt: skip

# The outcomes after which a trigger has nothing to start a job for. `doctor` reports only `ok`
# green: a `skipped` job is a sync that is not running.
SETTLED = ("ok", "skipped")
# The failures a job run again on the same `HEAD`, upstream and notes usually meets again, so a
# `Stop` waits them out as it does a hold (`backed_off`); `SessionEnd` retries them.
STANDING = (
    "conflict", "auth_failed", "hook_failed", "scan_unavailable", "signing_failed",
    "secret_found", "waiting_clean_tree",
)  # fmt: skip

STATE_FILE = "state.json"
LOCK_FILE = "job.lock"
SLEEPER_LOCK_FILE = "sleeper.lock"
PENDING_FILE = "pending"
SCRATCH = "scratch"

# A trigger that follows a job by less than this does not start another: a session's turns end
# every few seconds, and the notes a turn changed are not worth a fetch each. No shipped file
# states it.
THROTTLE_SECONDS = 120
# How long a trigger that only repeats a recorded hold waits, while neither `HEAD` nor the fetched
# upstream moved. A hold needs its owner, so asking the remote again sooner learns nothing.
BACK_OFF_MINUTES = 15
# A named cap: the most bytes of `state.json` read, since `doctor` and every hook read it. The job
# writes a few kilobytes, far under this; no shipped file states it.
STATE_BYTES = 4 * 1024 * 1024
# A named cap: the bytes of each dirty memory file `notes_mark` reads. A note is far smaller, and
# past it the mark still changes with the file's first bytes and its name; no shipped file states
# it.
MARK_BYTES = 1024 * 1024


class StateUnreadable(Exception):
    """`state.json` is there and cannot be used; the message says why."""


@dataclass(frozen=True)
class Outcome:
    state: str
    at: str
    detail: str | None


# Why upstream is held: changes outside memory, a tree git will not check out, a rewrite that
# dropped commits this overlay still has, or one here that dropped commits upstream still has; a
# rewrite's hold names the commits in place of paths.
OUTSIDE, UNREADABLE, REWRITTEN = "outside", "unreadable", "rewritten"
LOCAL_REWRITE = "rewritten here"
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def full_sha(text: str | None) -> bool:
    """Whether `text` is a whole sha-1 or sha-256 object name, as the job records one."""
    return text is not None and _SHA.fullmatch(text) is not None


@dataclass(frozen=True)
class Holds:
    upstream_paths: tuple[str, ...]
    local_commits: tuple[str, ...]
    upstream_why: str | None = None

    @property
    def any(self) -> bool:
        return bool(self.upstream_paths or self.local_commits)


EMPTY_HOLDS = Holds((), ())


@dataclass(frozen=True)
class SyncState:
    """`overlay` is the root the state was recorded for, as the machine file spells it. `head` is
    the `HEAD` the last job left and `dirty` the `notes_mark` of the memory paths it found dirty,
    which the back-off compares. `agreed` is the sha `main` and upstream last agreed on, written
    only when a job verified them equal, which the local rewrite check starts from. A
    `pending_align` an older job wrote is ignored."""

    overlay: str
    outcome: Outcome
    holds: Holds
    fetched_sha: str | None
    last_job_at: str | None
    head: str | None = None
    dirty: str | None = None
    agreed: str | None = None


def state_directory() -> Path:
    """`~/.local/share/drybag/sync`, under the data directory every other record is in."""
    return data_directory() / "sync"


def iso(moment: datetime) -> str:
    """The one timestamp format the state uses: UTC, seconds, with its offset."""
    return _aware(moment).astimezone(UTC).isoformat(timespec="seconds")


def parse_time(text: str) -> datetime:
    """A state timestamp as an aware datetime, a naive one taken as UTC; `ValueError` if none."""
    return _aware(datetime.fromisoformat(text))


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _text(value: Any, *, none: bool = False) -> Any:
    if isinstance(value, str) or (none and value is None):
        return value
    raise TypeError(value)


def _texts(value: Any) -> tuple[str, ...]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise TypeError(value)


def _time(value: Any, *, none: bool = False) -> Any:
    if _text(value, none=none) is not None:
        parse_time(value)
    return value


def _parse(data: dict[str, Any]) -> SyncState:
    """The state in `data`, every field of the type the job writes, or `StateUnreadable`."""
    try:
        raw, up, local = data["outcome"], data["holds"]["upstream"], data["holds"]["local"]
        state = SyncState(
            overlay=_text(data["overlay"]),
            outcome=Outcome(_text(raw["state"]), _time(raw["at"]), _text(raw["detail"], none=True)),
            holds=Holds(_texts(up["paths"]), _texts(local["commits"]), _text(up["why"], none=True)),
            fetched_sha=_text(data["fetched_sha"], none=True),
            last_job_at=_time(data["last_job_at"], none=True),
            head=_text(data["head"], none=True),
            dirty=_text(data["dirty"], none=True),
            agreed=_text(data["agreed"], none=True),
        )
    except (KeyError, TypeError, ValueError):
        raise StateUnreadable(f"{STATE_FILE} does not hold the fields the job writes") from None
    if state.outcome.state not in OUTCOMES:
        raise StateUnreadable("outcome.state is not a sync outcome")
    if state.holds.upstream_why not in (None, OUTSIDE, UNREADABLE, REWRITTEN, LOCAL_REWRITE):
        raise StateUnreadable("holds.upstream.why is not a reason the job holds for")
    return state


def read_state(d: Path) -> SyncState | None:
    """The state in `d`; `None` when there is none; `StateUnreadable` when it cannot be used."""
    try:
        raw = fsops.read_regular_bytes(d / STATE_FILE, limit=STATE_BYTES)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateUnreadable(f"{STATE_FILE} cannot be read ({fsops.said(exc)})") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StateUnreadable(f"{STATE_FILE} is not UTF-8 text") from None
    return _parse(json_object(text, STATE_FILE, error=StateUnreadable))


def write_state(d: Path, s: SyncState) -> None:
    """Write `s` into `d` in one step, mode 0600: a job killed mid-write leaves the old state or
    the new, never half."""
    if s.outcome.state not in OUTCOMES:
        raise ValueError(f"{s.outcome.state} is not a sync outcome")
    document = {
        "overlay": s.overlay,
        "outcome": {
            "state": s.outcome.state,
            "at": s.outcome.at,
            "detail": s.outcome.detail,
        },
        "holds": {
            "upstream": {"paths": list(s.holds.upstream_paths), "why": s.holds.upstream_why},
            "local": {"commits": list(s.holds.local_commits)},
        },
        "fetched_sha": s.fetched_sha,
        "last_job_at": s.last_job_at,
        "head": s.head,
        "dirty": s.dirty,
        "agreed": s.agreed,
    }
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    with fsops.open_directory(d) as directory:
        text = json.dumps(document, indent=2) + "\n"
        fsops.write_atomically_at(directory, STATE_FILE, text, mode=0o600)


class Lock:
    """An exclusive `flock` on an open descriptor: the kernel drops it when the holder dies.

    The descriptor is close-on-exec, so a git child of the job never inherits it: a child that
    outlived a killed job would otherwise keep the lock, and every later trigger would find the sync
    busy for as long as that child ran.
    """

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    @classmethod
    def try_acquire(cls, path: Path) -> Lock | None:
        """The lock on `path`, made if need be, or `None` at once if another holds it."""
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                return None
            raise
        return cls(fd)

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)  # closing the descriptor drops the lock

    def __enter__(self) -> Lock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def mark_pending(d: Path) -> None:
    """Record that a trigger arrived while the job ran; any number of marks is one pending run."""
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.close(os.open(d / PENDING_FILE, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600))


def take_pending(d: Path) -> bool:
    """Consume the marker: `True` if it was set. One `unlink`, so two takers cannot both win."""
    try:
        os.unlink(d / PENDING_FILE)
    except FileNotFoundError:
        return False
    return True


def _age(last: str | None, now: datetime) -> timedelta | None:
    """How long ago `last` was; `None` when unknown, unparseable or in the future.

    A time in the future means the clock jumped back since it was written. Waiting it out could
    stall the sync for as long as the jump, so such a time counts as long past.
    """
    if last is None:
        return None
    try:
        age = _aware(now) - parse_time(last)
    except ValueError:
        return None
    return age if age >= timedelta(0) else None


def throttled(last_job_at: str | None, now: datetime) -> bool:
    """Whether the last job ended less than `THROTTLE_SECONDS` ago."""
    age = _age(last_job_at, now)
    return age is not None and age < timedelta(seconds=THROTTLE_SECONDS)


def notes_mark(overlay: Path, paths: Iterable[str]) -> str:
    """One digest of the dirty memory `paths` and what each holds now, read without following a
    link: what the back-off compares, so an edit of a note that was already dirty is news."""
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        try:
            content = b"=" + fsops.read_bounded_within(overlay, path, MARK_BYTES)[0]
        except OSError:
            content = b"-"  # gone, a link, or not a file: the same mark until that changes
        digest.update(hashlib.sha256(answer_bytes(path)).digest())
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def backed_off(
    state: SyncState,
    head: str,
    now: datetime,
    *,
    dirty: str | None = None,
    upstream: str | None = None,
) -> bool:
    """Whether a trigger that only repeats a recorded hold, or a failure only its owner can clear
    (`STANDING`), should wait.

    True while either stands, `head` is the one the last job left, the dirty notes the caller sees
    (`dirty`, their `notes_mark`, when it knows them) are the ones that job left, the upstream sha
    it sees (`upstream`, when it knows one) is the one that job recorded, and that job is under
    `BACK_OFF_MINUTES` old. Only a job changes the outcome, and a job also restarts the window.
    The caller still starts a job on `SessionStart`.
    """
    if not (state.holds.any or state.outcome.state in STANDING):
        return False
    if state.head is None or state.head != head:
        return False
    if dirty is not None and dirty != state.dirty:
        return False
    if upstream is not None and upstream != state.fetched_sha:
        return False
    age = _age(state.last_job_at, now)
    return age is not None and age < timedelta(minutes=BACK_OFF_MINUTES)
