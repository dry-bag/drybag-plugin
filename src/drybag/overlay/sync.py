"""Whether the overlay's own repository holds anything the other machine cannot see.

Two questions through `drybag.gitenv.git_run`, every argument a constant, the root the
overlay this machine records. The timeout is two seconds **per call** and not `git`'s default
five, so this function's own worst case is four: it runs inside a session-start handler ahead of
the handler that links the store, and the entry's budget is ten seconds for everything.
`docs/cli.md` states it the same way — "two `git` calls at two seconds each".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from drybag.gitenv import answer_lines, git_run

SYNC_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Sync:
    dirty: int
    ahead: int | None
    asked: bool


def overlay_sync(overlay: Path, *, timeout: float = SYNC_TIMEOUT_SECONDS) -> Sync:
    code, status = git_run(
        overlay, "status", "--porcelain", "--untracked-files=normal", timeout=timeout
    )
    if code != 0:
        return Sync(0, None, False)
    # Counted by the lines git wrote: a name holding a line separator is one entry.
    dirty = sum(1 for line in answer_lines(status) if line.strip())
    code, count = git_run(overlay, "rev-list", "--count", "@{upstream}..HEAD", timeout=timeout)
    ahead = int(count.strip()) if code == 0 and count.strip().isdigit() else None
    return Sync(dirty, ahead, True)
