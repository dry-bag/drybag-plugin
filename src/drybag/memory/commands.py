"""The `memory` group. Every command takes `--store PATH`.

`--store` is an override of *where the notes are*, not of the rules about them, and overlay mode
does not read it: there the store is the composed one the binding names. `--machine` exists for
the same reason the resolver takes one — a test that did not thread it would read the developer's
real configuration.

It is threaded exactly twice, into `command_config` (`session_config` for `session-context`,
which reads the file the way a session does) and into `resolve`, and never again: `resolve` puts
it on the `Store` it builds, so every later call takes it from there. It used to be an optional
keyword on about twenty functions, and the only thing keeping a command's overlay and its index
destination in agreement was that each of those call sites remembered to pass it.
"""

from __future__ import annotations

import argparse
import contextlib
from pathlib import Path

from drybag import fsops
from drybag.areas import SubParsers
from drybag.command import CHECK_HELP, command_owner, common_flags
from drybag.config.loader import CONFIG_FILE, NOT_THERE
from drybag.config.reading import ConfigError
from drybag.config.schema import HARNESS_MODE, Config
from drybag.config.session import bound_config, session_config
from drybag.errors import Failure, Refusal
from drybag.findings import listed
from drybag.memory import trust
from drybag.memory.bundles import Bundle, render
from drybag.memory.index import (
    INDEX_NAME,
    IndexCheck,
    Reconciliation,
    check_index,
    reconcile,
    render_index,
    write_index,
)
from drybag.memory.inventory import inventory, totals
from drybag.memory.notes import Note, walk
from drybag.memory.reviewed import promote, unreviewed
from drybag.memory.store import (
    BUILT_BY_ATTACH,
    NO_RECORD,
    STORE_MISSING,
    Store,
    Unresolved,
    resolved,
    shown,
    walked_groups,
)
from drybag.printed import printable, quoted
from drybag.result import Result


def _machine(args: argparse.Namespace) -> Path | None:
    value = getattr(args, "machine", None)
    return Path(value) if value else None


# `refusal_reason`'s own docstring: the string "must never reach model context unwrapped". Its
# only caller put it verbatim into a `Failure` message, and `cli._report` prints that message on
# **stdout** under `--json` — twice, in one object, since the envelope carries it as both
# `summary` and the message. `memory session-context` is a `hooks.json` entry, so the invariant
# was holding only on the expectation that those entries never pass `--json`: an expectation
# owned by a different area, asserted by no test here, and contradicted by `hooks.py` going to
# real lengths to keep this same string out of `HookResult.context`.
#
# So the detail is kept and wrapped, rather than dropped. A person running `memory index` by
# hand needs to know *which* group entry was refused; a model reading the same bytes needs the
# region markers that say the text is data. `trust.wrap` is what `refusal_reason` names as the
# way to have both: the region's nonce is one the text does not hold, so no value can close it.
#
# A reason that has words of drybag's own — which cause, and the way out — says them *before*
# the region: inside it they are data a model is told not to act on, and the way out is the one
# thing a reader has to act on.
_SAID = "no memory store: {said}; the detail below is repository-authored text, shown as data"


def _no_store(reason: Unresolved) -> Failure:
    if reason.detail is None:
        return Failure(f"no memory store: {reason.said}")
    lead = _SAID.format(said=reason.said)
    return Failure(f"{lead}\n{trust.wrap(reason.detail)}")


def _store(args: argparse.Namespace) -> tuple[Store, Config]:
    """The store the configuration names in `--root`, or the refusal that says why there is none."""
    root = Path(args.root).resolve()
    machine = _machine(args)
    owner = command_owner(machine)
    config = bound_config(root, machine=machine, owner=owner)
    if config is None:
        # A checkout with no file that this machine does not bind: `load`'s refusal.
        raise ConfigError(NOT_THERE.format(path=root / CONFIG_FILE))
    # `resolved` and not `resolve` + `refusal_reason`: the pair walked the whole resolution
    # twice, four `git` queries with a five-second timeout apiece each time, so a hanging `git`
    # cost a refused command up to forty seconds.
    found = resolved(root, config, override=args.store, machine=machine)
    if found[0] is None:
        raise _no_store(found[1])
    return found[0], config


# `memory.index_extra` entries are pointers in `drybag.toml`, repository data, held back from an
# index whose destination reaches outside this repository (`index._to_machine`). Said out loud: a
# drop `render_index` makes on its own has no channel back to a person running the command.
_EXTRA_NOT_PUBLISHED = (
    "{names} took no pointer in {index}: `memory.index_extra` lives in this repository's "
    "drybag.toml, while this run's destination reaches outside it"
)


def _with(summary: str, note: str | None) -> str:
    return summary if note is None else f"{summary}; {note}"


def _printed(names: list[str]) -> str:
    """Names the store or `drybag.toml` supplied, joined for a summary line. Each is a file
    name or a `name:` a repository can commit, escape sequences included, so each goes through
    `printable`, and the list through `findings.listed`'s one cap, since a store can hold notes
    by the hundred: the line says how many it left off, and `--json` carries every name,
    whole."""
    return listed([printable(name) for name in names])


def _publish(reconciled: Reconciliation, store: Store) -> str | None:
    """The `memory.index_extra` entries held back, named where a person will read it."""
    if not reconciled.refused_extra:
        return None
    index = shown(store.path / INDEX_NAME)
    return _EXTRA_NOT_PUBLISHED.format(names=_printed(reconciled.refused_extra), index=index)


# A note the store holds and cannot parse is the one failure this store cannot recover from by
# itself: `walk` quarantines it so one bad file does not cost the whole store, and from there
# it is invisible to routing, to the standing rules and to volatile injection. It reached
# `Result.data` and nothing a person reads.
_UNREADABLE_NOTES = (
    "{count} file(s) in the store cannot be read as a note, so they reach neither the index "
    "nor any injection bundle: {paths}"
)


def _findings(report: IndexCheck, config: Config) -> list[str]:
    """Everything `memory index` must both say out loud and exit non-zero for.

    One list, read by the summary and by the exit code, because the two disagreed: the summary
    branched on `drifted` alone while the exit code was `drifted or over_budget`, so a run
    printed "index is current: N words, M lines" and exited 1 in the same breath. `over_caps` —
    the two limits at which the harness truncates `MEMORY.md` — was computed by `check_index`
    and then dropped entirely, absent from the data, the summary and the exit code alike.

    Drift is deliberately not here. It is the one finding whose meaning differs between the two
    callers: `--check` reports it, and the write path has just removed it.
    """
    found: list[str] = []
    if report.over_budget:
        budget = config.budgets.effective("memory_index_words")
        found.append(f"the index is {report.words} words, over its {budget}-word budget")
    if report.over_caps:
        found.append(
            f"the index is past the harness caps it is truncated at "
            f"({', '.join(report.over_caps)}): {report.lines} lines, {report.bytes_} bytes"
        )
    if report.unreadable:
        found.append(
            _UNREADABLE_NOTES.format(
                count=len(report.unreadable),
                paths=_printed(report.unreadable),
            )
        )
    return found


def run_index(args: argparse.Namespace) -> Result:
    store, config = _store(args)
    reconciled = reconcile(store, config, write=not args.check)
    report = check_index(store, config, reconciled)
    findings = _findings(report, config)
    if args.check:
        if report.drifted:
            findings.insert(0, "the index is out of date; run `drybag memory index`")
        summary = (
            "; ".join(findings)
            if findings
            else f"index is current: {report.words} words, {report.lines} lines"
        )
        summary = _with(summary, _publish(reconciled, store))
        return Result(
            summary,
            {
                "drifted": report.drifted,
                "words": report.words,
                "lines": report.lines,
                "bytes": report.bytes_,
                "over_budget": report.over_budget,
                "over_caps": report.over_caps,
                "provisional": report.provisional,
                "refused_extra": reconciled.refused_extra,
                "unreadable": report.unreadable,
            },
            # The same list the summary is built from, so the two can no longer disagree.
            exit_code=1 if findings else 0,
        )
    text = render_index(reconciled, config, store)
    path = write_index(store, config, text)
    # Exit 0: the write succeeded, and `--check` is the mode that fails a build. The findings
    # are still said, because a person running this by hand is who can act on them.
    wrote = "; ".join(
        [f"wrote {shown(path)} ({report.words} words, {len(reconciled.notes)} notes)", *findings]
    )
    wrote = _with(wrote, _publish(reconciled, store))
    return Result(
        wrote,
        {
            "path": shown(path),
            "words": report.words,
            "lines": report.lines,
            "harvested": reconciled.harvested,
            "provisional": reconciled.provisional,
            "refused_extra": reconciled.refused_extra,
            "unreadable": report.unreadable,
            "over_budget": report.over_budget,
            "over_caps": report.over_caps,
        },
    )


# What the standing-rules entry says of a bound checkout whose composed store is gone.
STORE_GONE = (
    "drybag: this checkout's memory store {store} is gone, so no note reaches this session; "
    "run `drybag attach` to build it"
)
_NOTHING_TO_DELIVER = (STORE_MISSING, STORE_MISSING + BUILT_BY_ATTACH, NO_RECORD)


def run_session_context(args: argparse.Namespace) -> Result:
    try:
        bundle = Bundle(args.bundle)
    except ValueError as exc:
        known = ", ".join(b.value for b in Bundle)
        raise Refusal(f"unknown bundle {args.bundle!r}; known: {known}") from exc
    # Read the way the session it feeds reads it (`config.session`): a key a newer release added
    # or a value nobody can use costs that key, not every bundle; a file that does not load at
    # all gives no project configuration, and so no store and nothing to say. The notice about it
    # is the `hook SessionStart` entry's, which runs beside this one.
    root = Path(args.root).resolve()
    machine = _machine(args)
    session = session_config(root, machine=machine)
    # With no file, the configuration of a checkout this machine binds, as a session reads it.
    config = session.acting
    # No project configuration, or memory left to the harness: no store, and nothing to say.
    if config is None or config.memory.mode == HARNESS_MODE:
        return Result("")
    found = resolved(root, config, override=args.store, machine=machine)
    if found[0] is None:
        # A store whose directory nobody has made yet — a `local-only` repository before its
        # first note, an overlay checkout `attach` has not composed — and a checkout this machine
        # has not bound have nothing to deliver, and a session is no place to say so: `doctor`
        # reports the binding and the store, and `hook SessionStart` says the binding once.
        missing = found[1].said == STORE_MISSING + BUILT_BY_ATTACH
        if missing and bundle is Bundle.STANDING_RULES and args.part == 1:
            return Result(STORE_GONE.format(store=quoted(found[1].detail or "")))
        if found[1].said in _NOTHING_TO_DELIVER:
            return Result("")
        raise _no_store(found[1])
    text = render(bundle, found[0], config, part=args.part)
    return Result(text if text is not None else "")


def run_inventory(args: argparse.Namespace) -> Result:
    store, config = _store(args)
    reconciled = reconcile(store, config, write=False)
    entries = inventory(reconciled, config, store)
    counts = totals(entries)
    # A note file that is a link is read nowhere (`notes.walk`), and counted here, where a sweep
    # reads what the store holds.
    skipped = len(reconciled.skipped)
    return Result(
        f"{counts['notes']} notes, {counts['words']} words, "
        f"{counts['provisional']} provisional, {counts['stale']} stale"
        + (f", {skipped} linked note(s) skipped" if skipped else ""),
        {"entries": [entry.as_dict() for entry in entries], **counts, "skipped": skipped},
    )


# What `memory promote` refuses, with nothing recorded: a name that is no note the store walks,
# and a command line that names notes and asks for every unreviewed one at once, or neither.
NOT_A_NOTE = (
    "is not a note of this store, so nothing was promoted; name a note by its path in the store, "
    "<group>/<file>.md, as `drybag memory index` links it"
)
NOTHING_NAMED = (
    "name the notes to promote, or pass --changed for every unreviewed note, and not both; "
    "nothing was promoted"
)


def _relative(note: Note) -> str:
    return f"{note.group_name}/{note.path.name}"


def _named(notes: list[Note], names: list[str]) -> list[Note]:
    """The notes `names` name, by their path in the store or by a path to the file, in order and
    once each; a name that is none of them refuses before anything is recorded."""
    by_name = {_relative(note): note for note in notes}
    by_path: dict[Path, Note] = {}
    for each in notes:
        with contextlib.suppress(OSError):
            by_path[fsops.resolved(each.path)] = each
    chosen: dict[Path, Note] = {}
    for name in names:
        found = by_name.get(name)
        if found is None:
            with contextlib.suppress(OSError):
                found = by_path.get(fsops.resolved(Path(name).expanduser()))
        if found is None:
            raise Refusal(f"{quoted(name)} {NOT_A_NOTE}")
        chosen.setdefault(found.path, found)
    return list(chosen.values())


def run_promote(args: argparse.Namespace) -> Result:
    if bool(args.notes) == args.changed:
        raise Refusal(NOTHING_NAMED)
    store, config = _store(args)
    notes = walk(store.path, walked_groups(store, config)).notes
    chosen = unreviewed(store.machine, notes) if args.changed else _named(notes, args.notes)
    recorded = promote(store.machine, chosen) if chosen else {}
    rows = [{"path": _relative(note), "digest": recorded[note.path]} for note in chosen]
    lines = [f"promoted {printable(row['path'])} {row['digest']}" for row in rows]
    return Result(
        "\n".join(lines) or "every note is reviewed; nothing was promoted", {"promoted": rows}
    )


def register(groups: SubParsers) -> None:
    group = groups.add_parser("memory", help="the working-memory store")
    sub = group.add_subparsers(dest="command", metavar="<command>")

    index = common_flags(
        sub.add_parser("index", help="render MEMORY.md from the notes"), store=True
    )
    index.add_argument("--check", action="store_true", help=CHECK_HELP)
    index.set_defaults(func=run_index)

    context = common_flags(
        sub.add_parser("session-context", help="render one injection bundle"), store=True
    )
    context.add_argument("--bundle", required=True, help=", ".join(b.value for b in Bundle))
    context.add_argument("--part", type=int, default=1, help="which numbered slot to render")
    context.set_defaults(func=run_session_context)

    listing = common_flags(
        sub.add_parser("inventory", help="what a memory sweep reads"), store=True
    )
    listing.set_defaults(func=run_inventory)

    promoting = common_flags(
        sub.add_parser("promote", help="count notes as the owner's, at the bytes they hold now"),
        store=True,
    )
    promoting.add_argument(
        "notes",
        nargs="*",
        metavar="NOTE",
        help="a note by its path in the store, <group>/<file>.md",
    )
    promoting.add_argument("--changed", action="store_true", help="every unreviewed note")
    promoting.set_defaults(func=run_promote)
