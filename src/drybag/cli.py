"""The CLI frame: one parser, areas discovered by name, three exit codes."""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Iterable
from typing import Any

import drybag
from drybag.areas import Registrar, SubParsers, area_modules
from drybag.errors import Failure, Refusal
from drybag.result import Result

# The aliases live in the leaf module every area imports; they are re-exported here because
# `from drybag.cli import SubParsers` is the import an area command module already writes.
__all__ = [
    "Registrar",
    "SubParsers",
    "build_parser",
    "discover_registrars",
    "main",
    "run",
    "split_json_flag",
]


def discover_registrars() -> list[Registrar]:
    """Every `drybag.<area>.commands.register`, in name order, with no shared registry."""
    return [module.register for module in area_modules("commands")]


# Named in every `--help` because the frame accepts it before argparse does (`split_json_flag`),
# so no command declares it and no command's own help would otherwise mention it.
JSON_EPILOG = (
    "--json is accepted anywhere on the line before a bare `--` and prints one machine-readable "
    "object instead of one line."
)


def build_parser(registrars: Iterable[Registrar] = ()) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drybag", description=drybag.__doc__, epilog=JSON_EPILOG)
    parser.add_argument("--version", action="version", version=f"drybag {drybag.__version__}")
    groups = parser.add_subparsers(dest="group", metavar="<group>")
    for register in registrars:
        register(groups)
    _finish(groups)
    return parser


def _finish(groups: SubParsers) -> None:
    """Give every command the help text it registered with, and list groups in name order.

    An area registers a command as `add_parser(name, help=...)`, and argparse keeps that
    sentence for the *parent's* listing only: the command's own `--help` opened with `usage:`
    and went straight to the options, with nothing about what the command does. The one
    sentence each area already wrote becomes the command's description here, so no area has to
    spell it twice. The `--json` epilog rides along for the same reason it is on the root.

    The group listing is sorted because discovery is by area and registration within one, so
    `commit` (from `guards`) landed between `doctor` and `hook`; `discover_registrars` says "in
    name order", and that is now true of what the user sees and not only of the modules.

    `_choices_actions` and `_name_parser_map` are argparse's own bookkeeping, unchanged since
    Python 3.2 and declared in typeshed; the alternative is every `add_parser` call passing the
    same string twice.
    """
    groups._choices_actions.sort(key=lambda action: action.dest)
    helps = {action.dest: action.help for action in groups._choices_actions}
    for name, parser in groups._name_parser_map.items():
        if parser.description is None:
            parser.description = helps.get(name)
        if parser.epilog is None:
            parser.epilog = JSON_EPILOG
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                _finish(action)


def split_json_flag(argv: list[str]) -> tuple[list[str], bool]:
    """`--json` is accepted anywhere, so every command carries it without declaring it."""
    return [arg for arg in argv if arg != "--json"], "--json" in argv


def _report(kind: str, message: str, code: int, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"summary": f"{kind}: {message}", "error": kind}, sort_keys=True))
    else:
        print(f"drybag: {kind}: {message}", file=sys.stderr)
    return code


def _emit(outcome: Any, as_json: bool) -> int:
    """Turn what a command returned into output and an exit code.

    Called from inside `run`'s `try`. A `Path`, `datetime`, `set` or `Decimal` in `Result.data`
    is an easy thing for a later area to write, and the plain-text path prints only `summary`
    and never notices — so the machine-readable path, the one CI consumes, is the only one that
    breaks, and it must refuse rather than report an internal error as findings.
    """
    if not isinstance(outcome, Result):
        return int(outcome)
    if as_json:
        print(json.dumps({"summary": outcome.summary, **outcome.data}, indent=2, sort_keys=True))
    else:
        print(outcome.summary)
    return outcome.exit_code


def _printable(stream: object) -> None:
    """Let a strict text stream print a character it has no bytes for as an escape.

    A path git or the disk handed over in bytes that are not UTF-8 is a `str` with a surrogate
    escape per byte, and a summary that names it reaches `print`. Under a UTF-8 locale such as
    `en_US.UTF-8` stdout encodes strictly, so printing it would turn the command's own answer
    into `internal error: UnicodeEncodeError`, exit 2. `backslashreplace` prints `\\udce9`
    instead. Only a strict stream changes: one Python already set to `surrogateescape` (UTF-8
    mode) writes the original byte back, which is the path as it is on disk. Nothing
    machine-readable is affected — `--json` and the hook's own output escape every non-ASCII
    character themselves, and the gate's summary is a file — so this only reaches text that
    would otherwise raise.
    """
    if isinstance(stream, io.TextIOWrapper) and stream.errors == "strict":
        stream.reconfigure(errors="backslashreplace")


def run(argv: list[str] | None, *, parser: argparse.ArgumentParser) -> int:
    """Execute an already-built parser. Building it is `main`'s, so one judge owns that failure.

    An area's `register()` is called while the parser is built, and a broken one is the same
    class of failure as an area that raises at import — `_discovery_failed` judges both.
    """
    _printable(sys.stdout)
    _printable(sys.stderr)
    raw = list(sys.argv[1:] if argv is None else argv)
    args_in, as_json = split_json_flag(raw)
    args = parser.parse_args(args_in)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return _emit(func(args), as_json)
    except Refusal as exc:
        return _report("refused", str(exc), 2, as_json)
    except Failure as exc:
        return _report("failed", str(exc), 1, as_json)
    except BaseException as exc:  # exit 1 is for findings; a bug or a Ctrl-C is not one
        return _report("internal error", f"{type(exc).__name__}: {exc}", 2, as_json)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        registrars = discover_registrars()
        parser = build_parser(registrars)
    except BaseException as exc:  # a broken area must never read as findings, nor escape judging
        return _discovery_failed(raw, exc)
    return run(raw, parser=parser)


def _discovery_failed(raw: list[str], exc: BaseException) -> int:
    """Discovery and the parser build both abort before argparse, so `hook`'s own rule is applied
    here (`docs/cli.md#drybag-hook-event`): one area's bug in its `commands.py` — raised at
    import, or from the `register()` the parser build calls, or as two areas claiming one group
    name — exits 0 on a hook, since a session never dies of drybag's own fault.
    """
    reason = f"drybag: internal error: {type(exc).__name__}: {exc}"
    words = [arg for arg in raw if arg != "--json"]
    if len(words) >= 2 and words[0] == "hook":
        print(f"{reason}; continuing open", file=sys.stderr)
        return 0
    print(reason, file=sys.stderr)
    return 2
