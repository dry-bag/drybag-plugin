"""What every configured command shares: the common flags and the configuration load.

Three areas register commands that take `--root` and `--machine` and then load
`drybag.toml`; at three the convention is code. So is the machine owner's layer every load of a
command runs on (`command_owner`), resolved once a command and said once. Not in
`drybag.areas`: that module is imported by the hook registry, and
`tests/boundaries/test_discovery.py` asserts that discovery in a clean interpreter imports neither
the configuration layer nor the presets — this module imports the loader at module level and is
imported only by `commands.py` modules.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path

from drybag.config.loader import load
from drybag.config.machine import machine_config_path
from drybag.config.owner import Owner, resolve_owner, said_on_stderr
from drybag.config.schema import Config

# One sentence per shared flag. `--root` and `--machine` were spelled by hand in three
# parsers besides this one and had already drifted from these words; `--home` appeared in two
# areas with two sentences, one saying "read" and the other "write". A flag that means the same
# thing across eight commands says the same thing, and `tests/test_command.py` walks the real
# parser and holds every occurrence to the constant.
ROOT_HELP = "project root (default: current directory)"
MACHINE_HELP = "machine configuration file to read"
STORE_HELP = "resolve the memory store at this path"
DRY_RUN_HELP = "report what would change and write nothing"
HOME_HELP = "the home directory to read and write under (default: the real one)"
# `--check` was registered five times with five sentences — "fail if the index is stale", "fail
# if the listing is stale", "report drift instead of writing", "report drift and write nothing",
# "report the binding and the diff, and write nothing" — in the tree that built this machinery
# for the five above. Four of the five mean one thing: read, report, write nothing, and fail if
# anything differs. The fifth is a real exception and is below.
CHECK_HELP = "report drift instead of writing, and fail if there is any"
# The named exceptions: each is one sentence, spelled here and used by exactly one parser,
# and `tests/test_command.py` holds the parser to it by name. An entry is a decision that the
# flag means something other than the shared thing, not a convenience for a sentence someone
# liked better.
OVERLAY_ROOT_HELP = "the overlay root (default: current directory)"
SETUP_ROOT_HELP = "the repository --git-hooks installs into (default: .)"
SETUP_MACHINE_HELP = (
    "the machine configuration file to write (default: ~/.config/drybag/config.toml, the "
    "file every reader reads)"
)
# `attach --check` reports the same way the four above do and **exits differently on purpose**:
# never on a non-empty diff. A diff with allow rules in it is the ordinary state of a first attach,
# which `attach` shows and proceeds with, so a `--check` that failed on it would make reading the
# diff first a failure. It ends with the run's own code where the run would refuse or fail.
ATTACH_CHECK_HELP = "report the binding and the diff, and write nothing"


def grammar(pattern: re.Pattern[str], rule: str) -> Callable[[str], str]:
    """An argparse `type` that refuses, before anything runs, with the rule and never with the
    value: the value is whatever was typed, and a refusal that quoted it would print it back."""

    def check(value: str) -> str:
        if not pattern.match(value):
            raise argparse.ArgumentTypeError(rule)
        return value

    return check


def common_flags(
    parser: argparse.ArgumentParser, *, store: bool = False
) -> argparse.ArgumentParser:
    parser.add_argument("--root", default=".", help=ROOT_HELP)
    parser.add_argument("--machine", default=None, help=MACHINE_HELP)
    if store:
        parser.add_argument("--store", default=None, help=STORE_HELP)
    return parser


def command_owner(machine: Path | None) -> Owner:
    """The machine owner's layer a command runs on, read once from `machine` (`None` is the file
    `config.machine` names) and `strict`, so a file that does not load is refused, with every note
    on what it read past said on stderr here and nowhere else.

    A command hands this to every load it makes (`config.loader.loads`' `owner`), which stands it
    on the preset each document names. Said by the load instead, the notes were said once a
    document, so a command that loads two documents said each note twice.
    """
    return said_on_stderr(
        resolve_owner(machine_config_path() if machine is None else machine, strict=True)
    )


def root_and_config(args: argparse.Namespace, *, owner: Owner | None = None) -> tuple[Path, Config]:
    """The resolved `--root` and its configuration, loaded under `owner`: the command's own when it
    loads again later and so resolved it first, `command_owner`'s otherwise."""
    root = Path(args.root).resolve()
    machine = Path(args.machine) if args.machine else None
    owner = command_owner(machine) if owner is None else owner
    return root, load(root, machine=machine, owner=owner)
