"""The note sync's row in `drybag doctor`: `sync`.

One row, read off this machine's own records: the switch in the machine file, the git the job
needs, the `gitleaks` on `PATH` it needs, and the state its last job left. It runs nothing but
`git version`, `git rev-parse --git-path` for a `raced` last job and one `lstat`, and writes
nothing. `doctor`'s core discovers this module by name (CONTRIBUTING.md, "Areas"); every import
sits inside a function body, as in a `hooks.py`, because discovery imports it before the report
asks anything and the note layout and the job pull in the configuration layer.

The arms, in the order they are asked: off (a skip, the ordinary state), no overlay recorded, git
below the job's floor, no gitleaks on `PATH`, a hold, a last outcome other than `ok` (`skipped` is
the sync not running), and otherwise green. A state recorded for another overlay is not this
overlay's: it reads as never run, as the job reads it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from drybag.doctor.api import Context, Contribution, Row
    from drybag.sync.state import Holds

# How many names of a hold the row prints before it counts the rest.
SHOWN = 3


def _names(names: tuple[str, ...]) -> str:
    from drybag.printed import clipped

    shown = ", ".join(clipped(name) for name in names[:SHOWN])
    return shown + (f" and {len(names) - SHOWN} more" if len(names) > SHOWN else "")


def _held(holds: Holds, fetched: str | None, overlay: Path) -> Row:
    """The warning for a hold: what is held, and how to take it. A command names the recorded
    upstream sha only when it is one: `state.json` is a file, and an agent may run what this
    prints."""
    from drybag.doctor.api import WARN, Row
    from drybag.sync.job import LOCAL_REVIEW, REVIEW, REWRITE, UNREADABLE_REVIEW
    from drybag.sync.state import LOCAL_REWRITE, OUTSIDE, REWRITTEN, UNREADABLE, full_sha

    merged = any(map(full_sha, holds.upstream_paths))
    merge = ", or a commit that lists no path (a merge)" if merged else ""
    held = {
        OUTSIDE: (f"upstream holds changes outside memory{merge}", REVIEW),
        UNREADABLE: ("upstream's tree cannot be checked out here", UNREADABLE_REVIEW),
        REWRITTEN: ("upstream was rewritten and dropped commits this overlay has", REWRITE),
        LOCAL_REWRITE: ("main no longer has commits that origin/main still has", LOCAL_REVIEW),
    }
    said: list[str] = []
    fixes: list[str] = []
    if holds.upstream_paths:
        what, review = held[holds.upstream_why or OUTSIDE]
        said.append(f"{what} ({_names(holds.upstream_paths)}), so nothing is integrated or pushed")
        fixes.append(
            f"in {overlay}: {review.format(sha=fetched)}"
            if full_sha(fetched)
            else "review upstream by hand; the next job clears the hold once it is settled"
        )
    if holds.local_commits:
        said.append(f"local commits outside memory are not pushed ({_names(holds.local_commits)})")
        fixes.append("review the commits and push them by hand")
    return Row(WARN, "; ".join(said), "; ".join(fixes))


def _older_than_a_job(path: Path) -> bool:
    """Whether `path` was last written longer ago than a whole job runs."""
    import time

    from drybag.sync.job import Limits

    try:
        return time.time() - path.lstat().st_mtime > Limits().job
    except OSError:
        return False


def _sync(context: Context) -> Row:
    from pathlib import Path

    from drybag import fsops
    from drybag.config.machine import machine_config_path
    from drybag.doctor.api import OK, SKIP, WARN, Row
    from drybag.gitenv import GIT_TIMEOUT_SECONDS
    from drybag.printed import clipped
    from drybag.sync import scan
    from drybag.sync.git import MIN_GIT, git, version_ok
    from drybag.sync.state import STANDING, StateUnreadable, read_state, state_directory

    if not context.session.owner.sync.enabled:
        where = context.machine or machine_config_path() or "the machine file"
        return Row(SKIP, f"the note sync is off; `[sync] enabled = true` in {where} switches it on")
    overlay = context.overlay_root
    if not isinstance(overlay, Path) or not fsops.is_dir(overlay):
        return Row(
            WARN,
            "the note sync is on and this machine records no overlay, or one that is not a "
            "directory, so the job has nothing to sync",
            "record the overlay with `drybag setup --overlay <path>`",
        )
    version = git(overlay, "version", timeout=GIT_TIMEOUT_SECONDS)
    if version.code != 0 or not version_ok(version.out):
        found = clipped(version.out.strip() or version.err.strip() or "no answer")
        floor = ".".join(map(str, MIN_GIT))
        return Row(
            WARN,
            f"git {floor} or newer is needed and the job does nothing without it; found: {found}",
            f"install git {floor} or newer",
        )
    if scan.gitleaks() is None:
        said, _, remedy = scan.NO_SCANNER.partition("; ")
        return Row(WARN, said, remedy)
    try:
        state = read_state(state_directory())
    except StateUnreadable as error:
        return Row(
            WARN,
            f"the job's record is unreadable ({error})",
            "remove it; the next job writes a new one",
        )
    if state is None or state.overlay != str(overlay):
        return Row(OK, "the note sync is on and its job has not run for this overlay yet")
    if state.holds.any:
        return _held(state.holds, state.fetched_sha, overlay)
    outcome = state.outcome
    first = clipped(outcome.detail.splitlines()[0]) if outcome.detail else ""
    said = f"the last job ended {outcome.at}: {outcome.state}" + (f" ({first})" if first else "")
    if outcome.state == "ok":
        return Row(OK, said)
    # A killed git's lock ends every job `raced`, never backed off; the job never removes it.
    # Git says where it is: a linked worktree keeps its index under the main repository's `.git`.
    if outcome.state == "raced":
        asked = git(overlay, "rev-parse", "--git-path", "index.lock", timeout=GIT_TIMEOUT_SECONDS)
        lock = overlay / asked.out.strip()
        if asked.code == 0 and _older_than_a_job(lock):
            said += f"; {lock} is older than a whole job runs"
            return Row(WARN, said, f"remove {lock} if no git is running")
    if outcome.state == "skipped":
        fix = "the job acts once this is resolved; the next session event runs it"
    elif outcome.state in STANDING:
        fix = "a rerun meets this again until you act on what it says"
        # A conflict's detail ends in its command, past the clip: the command is given here, from
        # the job's own text and only for a recorded sha that is one, as a hold's is (`_held`).
        from drybag.sync.job import REVIEW
        from drybag.sync.state import full_sha

        if outcome.state == "conflict" and full_sha(state.fetched_sha):
            fix += f"; in {overlay}: {REVIEW.format(sha=state.fetched_sha)}"
    else:
        fix = "a later session event retries"
    return Row(WARN, said, fix)


def register() -> Contribution:
    """The one `sync` row."""
    from drybag.doctor.api import Contribution

    return Contribution(checks=(("sync", _sync),))
