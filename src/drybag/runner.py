"""The one seam between drybag and the programs it launches.

Everything the areas that launch a program (`overlay`, `attach`, `setup`, and the release
scripts) do that leaves this process — asking GitHub for a repository, cloning
one, installing a commit hook — goes through `Runner.launch`, so a test asserts *the argv it would
have run* against a stub instead of shelling out to `gh`, `git` or `pre-commit`. Mocking
`subprocess.run` would hide the argv, which is the only part of these calls that can be wrong in
a way a user notices: a missing `--private` publishes somebody's private overlay.

A missing binary is a finding, never a traceback: drybag installs none of `gh`, `git` and
`pre-commit`, and has to report on a machine that lacks any of them. So an `OSError` from the launch
becomes `Completed(NOT_FOUND, ...)` and the caller turns it into a note. A binary that ran and never
returned gets its own code, `TIMED_OUT`, because "not installed" and "hung for five minutes" are
different findings.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from drybag.gitenv import scrubbed_env

# The shell's own code for "command not found", used here for a binary that could not be
# launched at all, so a caller has one number to read rather than an exception to catch.
NOT_FOUND = 127
# `timeout(1)`'s own code, and a separate one on purpose. `TimeoutExpired` is a
# `SubprocessError`, so a `gh` that hung for the full `NETWORK_TIMEOUT_SECONDS` used to arrive
# as `NOT_FOUND` -- which every caller reads as "gh is not installed". A wrong finding is worse
# than a slow one, and the two states have different remedies.
TIMED_OUT = 124
# Wall-clock bound on one call: a named cap (CONTRIBUTING.md#named-caps), and no shipped file
# changes with it. Deliberately far wider than `gitenv.GIT_TIMEOUT_SECONDS`, which bounds a local,
# argument-free query that neither touches the network nor grows with the repository; every call
# here does the opposite — `gh repo create --clone` waits on GitHub to generate a repository from a
# template, and the clone that follows comes down the wire. Tune this for a hung process, not for a
# slow link.
NETWORK_TIMEOUT_SECONDS = 300
# A `git` is given `gitenv.scrubbed_env()`, the one environment every git drybag starts gets:
# its ssh agent and proxies are kept there, and a session's `GIT_CONFIG_GLOBAL` or
# `GIT_SSH_COMMAND` is not. Every other program inherits the environment rather than have it
# scrubbed: these are the machine owner's own authenticated commands, and `gh` needs its token to
# work at all. What is dropped from them is what points git at a *different* repository, or a
# different index or object store, than the directory it was given -- which is exactly what a
# program that runs git for us (`gh repo create --clone`, `pre-commit`) must not inherit.
#
# **This list is a trade-off and is not exhaustive, and the comment that said it was the two
# that redirect git was wrong.** `GH_HOST` and `GH_CONFIG_DIR` redirect `gh` the same way and
# are kept deliberately: they are how a GitHub Enterprise owner reaches their own host, and
# dropping them would break that installation outright to close a gap the inherited token does
# not have. A caller that needs them gone should say which call and why.
#
# `GH_FORCE_TTY` and `CLICOLOR_FORCE` are dropped for the reason `_ENV_FORCE` below exists: every
# call here captures its output, so terminal behaviour is never wanted, and either one makes `gh`
# colour its JSON (measured with gh 2.101.0: `gh api` and `gh repo view --json` start with an
# escape sequence), which every caller that parses it then reads as no answer. `NO_COLOR` and
# `CLICOLOR=0` do not undo either, so the variables go rather than a third one being added.
_ENV_DROP = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GH_FORCE_TTY",
    "CLICOLOR_FORCE",
)
# Forced rather than inherited, and the reason the cap above means anything. `git` and `gh` ask
# for a credential on stdin, and every call here runs with output captured -- so a prompt is
# invisible and blocks for the whole of `NETWORK_TIMEOUT_SECONDS`, which a caller cannot tell
# from a hang. What makes that a finding rather than an annoyance is which callers reach the
# network through here: `setup --overlay create:` launches `gh repo create` and a clone, whose
# authentication is the machine owner's and which prompts when it is missing -- a write meant to
# run non-interactively, not something a person is sitting in front of waiting to type a password.
_ENV_FORCE = {"GIT_TERMINAL_PROMPT": "0"}
# How a launched program's output is read: as text in the locale's codec, and a byte that codec
# cannot read is U+FFFD rather than a `UnicodeDecodeError`. The rule rests on what every caller
# does with the output — quotes it in a message, matches it against a grammar (`gh`'s JSON), or
# ignores it for an exit code — and on none of them comparing it with a path or opening one,
# which is what `gitenv.git_run`'s lossless reading is for. Read strictly, one such byte would end
# the command as an internal error. U+FFFD prints, and a UTF-8 file can hold it.
_DECODE_ERRORS = "replace"


@dataclass(frozen=True)
class Completed:
    code: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def launch(self, argv: list[str], cwd: Path) -> Completed: ...


@dataclass(frozen=True)
class _SubprocessRunner:
    """The real runner, bounded by `NETWORK_TIMEOUT_SECONDS`, read at call time rather than
    captured here, which is what keeps `tests/test_runner.py`'s case able to lower the module
    constant — and what keeps the bound one number in one place. The cap is for a hung process,
    not a slow link.
    """

    def launch(self, argv: list[str], cwd: Path) -> Completed:
        bound = NETWORK_TIMEOUT_SECONDS
        if argv[:1] == ["git"]:
            env = scrubbed_env()
        else:
            env = {key: value for key, value in os.environ.items() if key not in _ENV_DROP}
        env.update(_ENV_FORCE)
        try:
            done = subprocess.run(  # noqa: S603 - list form, never a shell; see the module docstring
                # Resolved through PATH on purpose: the owner's own `gh` and `git` must answer.
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                # See `_DECODE_ERRORS`: output is a message or a grammar's input, never a path.
                errors=_DECODE_ERRORS,
                check=False,
                timeout=bound,
                env=env,
                # Closed, not inherited. See `_ENV_FORCE`: a prompt on an inherited stdin is
                # invisible behind `capture_output` and outlasts nothing.
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return Completed(TIMED_OUT, "", f"{argv[0]} did not finish within {bound}s")
        except (OSError, subprocess.SubprocessError) as exc:
            return Completed(NOT_FOUND, "", f"{argv[0]} could not be run: {exc}")
        return Completed(done.returncode, done.stdout, done.stderr)


def subprocess_runner() -> Runner:
    """The real one. List form, never `shell=True`, and every repository- or argument-derived
    value passed after a `--` so a name shaped like an option cannot become one (principle 5)."""
    return _SubprocessRunner()
