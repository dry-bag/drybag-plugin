"""Ending a command's whole process group.

A command drybag starts in a session of its own, so what it starts shares its process group,
and that group is ended with SIGKILL, which no member can catch or ignore. A descendant that leaves
the group (a new session, or a job-control shell's own group) is not reached, and nor is one
drybag may not signal (a sudo or setuid descendant). The group's id is the command's pid, so the
group is ended before the command is reaped, while that pid names no group but its own.

`run_bounded` is the bounded launch every git drybag starts goes through (`gitenv.git_call`),
and the note sync's scanner beside it: what a timeout ends is the group, so an ssh or a credential
helper under git cannot outlive the step that started it. It sends SIGTERM first, since a git that
holds `index.lock` removes it on that signal and cannot on SIGKILL. A command that went on SIGTERM
is reaped by then, and its pid names a group only while a member that ignored the signal keeps it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import AnyStr, Generic

from drybag import fsops

# Reaping a command whose group was sent SIGKILL. The signal cannot be caught or ignored, so this
# is slack for the kernel, not a second bound on the command.
KILL_WAIT_SECONDS = 5
# How long a timed-out group has to act on SIGTERM before SIGKILL. git acts at once, so only a
# member that ignores the signal spends it. No shipped file states it.
TERM_WAIT_SECONDS = 2


def kill_group(process: subprocess.Popen[bytes], number: int = signal.SIGKILL) -> None:
    """Send signal `number` to the command's whole process group; a group with nothing left to end
    is no fault.

    Empty, the group answers `ProcessLookupError`; left with only the exited, unreaped command,
    macOS answers `PermissionError` rather than nothing, as it does for any group of zombies. A
    member drybag may not signal, a sudo or setuid descendant, answers `PermissionError` too,
    and is not ended: the command's own exit still answers for it.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, number)


@dataclass(frozen=True)
class Ran(Generic[AnyStr]):
    """What a bounded launch gave back: its exit code, its two streams, as bytes or decoded, and
    whether it ran out of its time, in which case the code is the signal's and the streams what
    was read."""

    code: int
    out: AnyStr
    err: AnyStr
    timed_out: bool


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    stdin: bytes | None = None,
) -> Ran[bytes]:
    """Run `argv` in `cwd` with exactly `env`, in a session of its own, for at most `timeout`.

    Standard input is `stdin`, or closed: nothing run here may wait for an answer. Past `timeout`
    the whole group is sent SIGTERM, then SIGKILL, its pipes read for `TERM_WAIT_SECONDS` after the
    first and `KILL_WAIT_SECONDS` after the second; a descendant that left the group and still
    holds them is abandoned, its streams empty. A program
    or directory that is not there is code 127, as a shell says it. No floor: a caller that wants
    one asks `gitenv`.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 - list form, never a shell; the callers' argv
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        said = f"{argv[0]}: {fsops.said(error)}".encode("utf-8", "surrogateescape")
        return Ran(127, b"", said, False)
    try:
        out, err = process.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        out = err = b""
        for number, wait in (
            (signal.SIGTERM, TERM_WAIT_SECONDS),
            (signal.SIGKILL, KILL_WAIT_SECONDS),
        ):
            kill_group(process, number)
            with contextlib.suppress(subprocess.TimeoutExpired):
                out, err = process.communicate(timeout=wait)
        process.wait()
        return Ran(process.returncode, out, err, True)
    return Ran(process.returncode, out, err, False)
