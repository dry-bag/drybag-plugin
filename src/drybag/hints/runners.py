"""The one table of test-runner program names the session hints look up.

A hint that speaks about a test run (the pipe hint, the red-run note) must first say whether a
command IS one. It does so here, by the command's program name and never by parsing its options:
`RUNNERS` holds, for each common runner, the leading words of its test command, and a command is a
test run when its words begin with a row. The first word is compared by its file name, so
`.venv/bin/pytest` and `./gradlew` read as `pytest` and `gradlew`, and a `python3.<minor>` name
reads as `python3`; every later word of a row is compared as typed.

THE BOUND IS DOCUMENTED, not accidental: no option is read. A word between the program and its
subcommand (`npm --prefix x test`, `go -C dir test`) is no match, a runner this table does not list
is no match, and a program that merely names a runner in its arguments (`echo pytest`) is no match.
Each is a hint that stays silent, which is the cheap error: the hints that ask this table never
refuse, so a missed run costs a note and a mistaken one costs a sentence.

The table names runners only to say that a command is a test run, never to run, plan or advise
one, and it is the one place the hints look this up.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

# Each row is the leading words of one runner's test command. No shipped file changes with it.
# Written as text and split once, so that a row is read as a command and not as code:
# `tests/boundaries/test_delivery.py` reads a bare `"exec"` string constant as the builtin that
# runs text, and `bundle exec rspec` is a command whose second word is that.
_ROWS = (
    "pytest",
    "py.test",
    "python -m pytest",
    "python3 -m pytest",
    "python -m unittest",
    "python3 -m unittest",
    "go test",
    "cargo test",
    "cargo nextest run",
    "npm test",
    "npm t",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
    "yarn run test",
    "bun test",
    "bun run test",
    "npx jest",
    "npx vitest",
    "jest",
    "vitest",
    "deno test",
    "dotnet test",
    "mvn test",
    "mvnw test",
    "gradle test",
    "gradlew test",
    "rspec",
    "bundle exec rspec",
    "phpunit",
    "mix test",
    "tox",
    "nox",
    "ctest",
    "swift test",
    "make test",
)
RUNNERS: tuple[tuple[str, ...], ...] = tuple(tuple(row.split()) for row in _ROWS)

# `python3.13` is the `python3` row's program: a minor version is not a different program.
_VERSIONED_PYTHON = re.compile(r"\Apython3\.[0-9]+\Z")


def _program(word: str) -> str:
    name = Path(word).name
    return "python3" if _VERSIONED_PYTHON.match(name) else name


def names_a_test_run(words: Sequence[str]) -> bool:
    """Whether `words`, one simple command already unwrapped by `bashscan.command_words`, begins
    with a row of `RUNNERS`. `command_words` asks it too, so that `uv run <runner>` unwraps to the
    runner."""
    if not words:
        return False
    program = _program(words[0])
    return any(row[0] == program and tuple(words[1 : len(row)]) == row[1:] for row in RUNNERS)


# Where `uv run` stops and the command it launches starts, for `bashscan.command_words`, here so
# the shared scanner names no stack. None of uv's option grammar is read: the command starts at
# the first word after `run` the table reads as a test run, and every word before it is uv's.
def is_launcher(name: str) -> bool:
    """Whether a command's program, by its bare name, is uv."""
    return name == "uv"


def past_run(segment: Sequence[str], index: int) -> int | None:
    """Where the words after `run` start in `segment`, read from `index`, just past the word `uv`
    (`is_launcher`); `None` where no `run` follows, a uv command that launches nothing."""
    if "run" not in segment[index:]:
        return None
    return segment.index("run", index) + 1
