"""Name what could have falsified a red test run, before the diff is blamed.

Uncommitted work in the tree produces a red run that no baseline A/B can attribute, because both
halves of the A/B run inside it: the measurement describes a tree nobody is merging. It has cost
real time, twice in one branch's retro. It belongs to no stack.

Warn-only by construction: the tool has already run, and the verdict this guards is the
human's next sentence, not the command. Once per context, which the dispatcher's `once_key`
owns (see `hooks.py`, and `drybag.hooks.dispatch`, which decides and banks the one delivery, for
what that currently means).

Whether the note speaks is decided by the command that failed, never by configuration: the command
is split into simple commands with the shared scanner's tokens and segments, each unwrapped as
`bashscan.command_words` describes, and the runner table (`runners.names_a_test_run`) is asked
whether one of them is a test run. When none is, nothing is said: the one delivery per context
belongs to the first failed test run, not to a failed `grep`. A command the scanner cannot read is
no test run (`simple_commands`).

Which harness field says a run was red is read from the documentation, not guessed: the Claude
Code hooks reference gives the Bash `tool_response` as `stdout`, `stderr`, `interrupted` and
`isImage` with no exit code, and a non-zero exit arriving on `PostToolUseFailure` as
`error: "Exit code N\\n…"`. `red_exit` reads both that shape and a `tool_response.exit_code`,
so the notice is keyed on whichever the harness sends rather than on a field one lacks.

Nothing a repository authored leaves this module. The notice carries the fixed sentences below and
a count this module computed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from drybag.gitenv import answer_lines, git_run
from drybag.hints import bashscan
from drybag.hints.runners import names_a_test_run

# Anchored TWICE, redundantly: the `\A` here and the `.match` at the call site each pin the read
# to index 0, so removing either one alone is a no-op -- measured, in both directions -- and only
# removing BOTH unanchors it. Do not read one of them as dead weight: unanchored, a number is
# read out of arbitrary captured stderr, and a line of test output that merely quotes an exit
# code is then taken for the run's own. The documented shape puts `Exit code N` at the very
# START of `error`, which is what the pair is protecting. The digits are bounded, and a longer run
# of them is no exit code: `int()` raises past 4,300 digits, and an exit code has at most three.
_EXIT_CODE_ERROR = re.compile(r"\AExit code (\d{1,9})(?!\d)")
# A named cap (CONTRIBUTING.md#named-caps) of its own, and no shipped file changes with it. Not
# `gitenv.GIT_TIMEOUT_SECONDS`: that constant covers "local, argument-free, read-only" queries, and
# `git status --porcelain` walks the worktree. A timeout here is `None`, "could not answer", which
# the note leaves unsaid.
STATUS_TIMEOUT_SECONDS = 20

LEAD = "Before calling this red a flake, pre-existing, or caused by the branch:"
DIRTY = (
    "{count} uncommitted change(s) in the tree -- this run measured a tree nobody is merging. "
    "A stashed-baseline A/B cannot see this: both halves run in it."
)


def simple_commands(command: str) -> list[list[str]]:
    """Each simple command of `command`, unwrapped to its own program and arguments.

    `[]` for a command the scanner cannot read (`bashscan.tokenize` returns `None`): text that is
    not a command line is no test run, so such a run gets no note. That under-reports a real
    failed run whose command the scanner cannot read, the safe direction for a warn-only note.
    """
    tokens = bashscan.tokenize(command)
    if tokens is None:
        return []
    found: list[list[str]] = []
    for segment in bashscan.segments(tokens):
        words = bashscan.command_words(segment, test_runs=True)
        if words:
            found.append(words)
    return found


def red_exit(raw: Mapping[str, Any]) -> int | None:
    """The non-zero exit code this payload reports, in either shape, or `None`.

    `isinstance(code, bool)` is excluded on purpose: `True` is an `int` equal to 1 in Python,
    so a harness that put a boolean in `exit_code` would otherwise be read as "exited 1".
    """
    response = raw.get("tool_response")
    if isinstance(response, dict):
        code = response.get("exit_code")
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return code
    error = raw.get("error")
    if isinstance(error, str):
        match = _EXIT_CODE_ERROR.match(error)
        if match and int(match.group(1)) != 0:
            return int(match.group(1))
    return None


def dirty_count(root: Path) -> int | None:
    """Uncommitted changes, or `None` when git could not answer. Never `0` for the latter:
    the two mean opposite things to a person deciding whether to trust a red run.

    Through `gitenv.git_run`: `root` is the project root the dispatcher resolved or `--root`
    resolved, not a repository value, and `--` closes the argument list so no pathspec can be
    smuggled in. `status --porcelain` prints a name raw under `core.quotePath=false`, in
    whatever bytes the disk holds it, and `git_run` reads those losslessly; counted by the
    lines git wrote (`answer_lines`), a name holding a line separator is still one entry.
    """
    code, out = git_run(root, "status", "--porcelain", "--", timeout=STATUS_TIMEOUT_SECONDS)
    if code != 0:
        return None
    return sum(1 for line in answer_lines(out) if line.strip())


def notice(dirty: int | None) -> str | None:
    """The dirty-tree sentence under `LEAD` when the tree is dirty, or `None` for silence."""
    if not dirty:
        return None
    return LEAD + "\n- " + DIRTY.format(count=dirty)


def context_for(command: str, root: Path) -> str | None:
    """The notice after `command` failed: `None` unless one of its simple commands is a test run
    the runner table names, and then the dirty-tree line."""
    if not any(names_a_test_run(words) for words in simple_commands(command)):
        return None
    return notice(dirty_count(root))
