"""The sync's git layer: its calls through the core's launch, the job's own settings and the guard.

Every call the job makes is `gitenv.git_call`: the one environment every git drybag starts is
given, at no less than the suite's floor, its whole process group ended on a timeout, so an ssh or
a credential helper under git cannot outlive the step that started it. A job started from inside a
git hook still addresses the overlay and its real index, since no repository variable is kept. A
helper's `timeout` is its whole budget: one that makes several calls gives each what is left
(`left`). Each call is given `JOB_SETTINGS` on top.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from drybag import fsops
from drybag.gitenv import QUERY_TIMEOUT_SECONDS, Called, git_call

# `merge-tree --write-tree --merge-base` (2.40), `GIT_ATTR_SOURCE` (2.41), which an older git
# ignores without a word, and a `merge-tree` that reads attributes from it without crashing (2.43).
MIN_GIT = (2, 43)
_VERSION = re.compile(r"git version (\d+)\.(\d+)")


class GitError(RuntimeError):
    """A git call the rules depend on failed; the message carries git's own words."""


class GitTimeout(GitError):
    """A git call ran out of its time; the job records `timeout`, not a failure."""


def git(
    repo: Path,
    *args: str,
    timeout: float = QUERY_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> Called:
    """`git <args>` in `repo` with `JOB_SETTINGS`, and `env` over them, laid over the core's
    environment."""
    return git_call(repo, *args, timeout=timeout, env={**JOB_SETTINGS, **(env or {})}, stdin=stdin)


def left(deadline: float, what: str) -> float:
    """What is left of a budget (`deadline`, a `time.monotonic` instant) for its next call;
    `GitTimeout` when nothing is."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GitTimeout(f"{what} did not finish in time")
    return remaining


def timed(result: Called, what: str) -> Called:
    """`result`, or `GitTimeout` when the call ran out of its time: never read as an answer."""
    if result.timed_out:
        raise GitTimeout(f"{what} timed out")
    return result


def checked(result: Called, what: str) -> Called:
    """`result` of a call that must succeed: `GitTimeout` on a timeout, `GitError` on a failure."""
    if timed(result, what).code != 0:
        raise GitError(f"{what} failed ({result.code}): {result.err.strip()[:500]}")
    return result


def ask(repo: Path, deadline: float, *args: str) -> Called:
    """`git <args>` given what is left of a helper's budget; never a timed-out answer."""
    return timed(git(repo, *args, timeout=left(deadline, "a git call")), f"git {args[0]}")


def rev_or_none(repo: Path, rev: str, *, timeout: float) -> str | None:
    """The sha `rev` names now; `None` when it cannot be read."""
    found = git(repo, "rev-parse", "-q", "--verify", "--end-of-options", rev, timeout=timeout)
    return found.out.strip() or None if found.code == 0 and not found.timed_out else None


def with_config(env: Mapping[str, str], entries: Mapping[str, str]) -> dict[str, str]:
    """`env` with `entries` added to the configuration it gives git (`GIT_CONFIG_COUNT`), as
    `git -c` would give them, after any it already gives."""
    given = dict(env)
    count = int(given.get("GIT_CONFIG_COUNT", "0"))
    for offset, (key, value) in enumerate(entries.items(), start=count):
        given.update({f"GIT_CONFIG_KEY_{offset}": key, f"GIT_CONFIG_VALUE_{offset}": value})
    given["GIT_CONFIG_COUNT"] = str(count + len(entries))
    return given


# What every git call of the job is given on top of the core's environment, and nothing asked to
# build it. `GIT_ATTR_SOURCE=HEAD`: attributes come from the committed tree, never from a
# `.gitattributes` a memory writer left in the working tree, where a filter would make the job
# commit, and the scanner read, something other than the note. `GIT_NO_REPLACE_OBJECTS=1`: a
# `refs/replace/` entry must not show the checks one commit while the push sends another.
# `LC_ALL=C`: the few messages the job matches read English whatever the locale. `core.askPass`
# emptied: git asks an askpass before the terminal, and the owner's would pop a dialog. And
# `core.hooksPath=/dev/null`: the job's `update-ref` and `push` run none of the overlay's hooks.
# No ssh options: a child with no terminal, no stdin, no askpass and a group kill at its step's
# bound cannot wait for a person.
JOB_SETTINGS: Mapping[str, str] = MappingProxyType(
    with_config(
        {"GIT_ATTR_SOURCE": "HEAD", "GIT_NO_REPLACE_OBJECTS": "1", "LC_ALL": "C"},
        {"core.askPass": "", "core.hooksPath": "/dev/null"},
    )
)


def version_ok(text: str) -> bool:
    """Whether `git version` output names `MIN_GIT` or newer; an unreadable answer is not."""
    match = _VERSION.search(text)
    return match is not None and (int(match[1]), int(match[2])) >= MIN_GIT


UNMERGED = "the overlay has unmerged paths; the sync waits until they are resolved"
# The marker each operation in progress leaves, by `--git-path` name, and how to say it.
_IN_PROGRESS = {
    "rebase-merge": "mid-rebase",
    "rebase-apply": "mid-rebase (or mid-am)",
    "MERGE_HEAD": "mid-merge",
    "CHERRY_PICK_HEAD": "mid-cherry-pick",
    "REVERT_HEAD": "mid-revert",
}
_MARKER_PATHS = tuple(part for name in _IN_PROGRESS for part in ("--git-path", name))


def guard(repo: Path, *, timeout: float = QUERY_TIMEOUT_SECONDS) -> str | None:
    """Why the overlay is not in a shape the sync may act on at all, or `None`.

    Refused, with the reason the state shows: git older than `MIN_GIT`, not a repository, an
    operation in progress, unmerged paths, `HEAD` not on `main`, or `main` not tracking
    `origin/main`. Work in progress, staged or not and anywhere outside memory, is no reason: the
    job never touches it. `timeout` bounds the guard as a whole, and a call that runs out of it is
    `GitTimeout`, never an answer (a timed-out `symbolic-ref` is not a detached `HEAD`).
    """
    deadline = time.monotonic() + timeout
    version = ask(repo, deadline, "version")
    if version.code != 0 or not version_ok(version.out):
        found = version.out.strip() or version.err.strip() or "no answer"
        return (
            "git 2.43 or newer is needed (merge-tree --write-tree --merge-base, GIT_ATTR_SOURCE); "
            f"found: {found}"
        )
    markers = ask(repo, deadline, "rev-parse", *_MARKER_PATHS)
    if markers.code != 0:
        return f"not a git repository: {markers.err.strip()[:200] or repo}"
    for saying, marker in zip(_IN_PROGRESS.values(), markers.out.splitlines(), strict=False):
        if fsops.exists(repo / marker):
            return f"the overlay is {saying}; the sync waits: finish or abort it"
    # A conflicted `stash pop`, `checkout -m` or `apply --3way` leaves no marker, only unmerged
    # entries and a working tree with conflict markers, which the job would commit as notes.
    unmerged = ask(repo, deadline, "ls-files", "--unmerged", "-z")
    if unmerged.code != 0 or unmerged.out:
        return UNMERGED
    head = ask(repo, deadline, "symbolic-ref", "-q", "HEAD")
    if head.code != 0:
        return "HEAD is detached; the sync runs only on main: `git switch main`"
    if (branch := head.out.strip().removeprefix("refs/heads/")) != "main":
        return f"the overlay is on {branch}, not main: `git switch main`"
    remote = ask(repo, deadline, "config", "--get", "branch.main.remote").out.strip()
    merge = ask(repo, deadline, "config", "--get", "branch.main.merge").out.strip()
    if (remote, merge) != ("origin", "refs/heads/main"):
        if not remote:  # short enough for `doctor`'s line (`printed.CLIPPED_CHARS`)
            fix = "`git remote add origin <url>`, then `git push -u origin main`"
            return f"main tracks nothing; give the overlay a remote: {fix}"
        tracking = f"{remote}/{merge.removeprefix('refs/heads/')}"
        return f"main tracks {tracking}, not origin/main"
    return None
