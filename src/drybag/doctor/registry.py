"""How the report finds the rows the areas contribute, and what an area that cannot gets instead.

Every `drybag.<area>.doctor` is discovered by name (`discover_contributors`), its `register()`
is asked under a guard, and every way an area's contribution can fail costs one red row named
after the area and never the report (`contributions`). None of it is a check: it is the one
failure policy for an area's own code and the seam a test replaces to inject an area, so it is
kept apart from the core's checks, and a test patches `discover_contributors` here, where
`contributions` reads it. It imports nothing of `checks.py`: the core's checks reach it as
`contributions`' argument, whose names no area may repeat, so `checks.py` imports this module
for the run without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import TypeGuard

from drybag.areas import area_imports
from drybag.doctor.model import RED, REPORT_THIS, Context, Contribution, Row


@dataclass(frozen=True)
class Unregistered:
    """The one check an area gets in place of its own when it could not contribute them: a red row
    saying why, named after the area and sitting where its rows would have been.

    It is about drybag's own code, which no configuration changes, so it is red in every
    report, one for a repository with no `drybag.toml` included."""

    detail: str

    def __call__(self, context: Context) -> Row:
        return Row(RED, self.detail, REPORT_THIS)


def _well_formed(contribution: object) -> TypeGuard[Contribution]:
    """Whether `register()` answered a `Contribution` of `(name, check)` pairs, each name text
    and each check callable.

    Asked before any of it is read, because every way an area's own code can get this wrong —
    `None`, the bare pairs, a pair without its check — otherwise fails later, inside the run,
    where it costs the report."""
    if not isinstance(contribution, Contribution) or not isinstance(contribution.checks, tuple):
        return False
    return all(
        isinstance(pair, tuple)
        and len(pair) == 2
        and isinstance(pair[0], str)
        and bool(pair[0])
        and callable(pair[1])
        for pair in contribution.checks
    )


@dataclass(frozen=True)
class _Answer:
    """What one area's `doctor.py` answered: the `Contribution` it hands the report, or, where
    `contribution` is `None`, why it hands none — `refused`, the detail of the one red row the area
    gets in its rows' place. One type for both, so the report's two passes read each area's answer
    the same way whichever it was."""

    qualified: str
    contribution: Contribution | None
    refused: str = ""


def _registered(qualified: str, found: ModuleType | Exception, owners: dict[str, str]) -> _Answer:
    """What the area's `doctor.py` contributes, or why it could not: the import's failure, a
    `register()` that raised or answered something that is not a `Contribution`, or one that
    repeats a name `owners` already maps to who reports it. A contribution let in has its names
    recorded in `owners`.

    The module and its `register()` are an area's code as its checks are, so they get their
    guard, and the reason names the exception's type and never its message, for
    `checks._guarded`'s reason. `qualified` is drybag's own module name and never
    repository-authored, so it prints.
    """
    if isinstance(found, Exception):
        return _Answer(
            qualified, None, f"{qualified} could not be imported: {type(found).__name__}"
        )
    reason = f"{qualified} could not contribute its rows"
    try:
        contribution: object = found.register()
    except Exception as exc:  # an area's own code, guarded as its checks are
        return _Answer(qualified, None, f"{reason}: {type(exc).__name__}")
    if not _well_formed(contribution):
        return _Answer(
            qualified,
            None,
            f"{reason}: its register() did not return a Contribution of (name, check) pairs",
        )
    repeated = _repeated(contribution, owners)
    if repeated is not None:
        return _Answer(qualified, None, f"{reason}: {repeated}")
    owners.update((name, qualified) for name, _ in contribution.checks)
    return _Answer(qualified, contribution)


def _repeated(contribution: Contribution, owners: Mapping[str, str]) -> str | None:
    """Why `contribution` may not join a report whose names `owners` maps to who reports them:
    the first of its names that one of them, or an earlier pair of its own, already has."""
    named = dict(owners)
    for name, _ in contribution.checks:
        if name in named:
            return f"its check {name!r} repeats a name {named[name]} already reports"
        named[name] = "it"
    return None


def _unregistered(answer: _Answer, owners: Mapping[str, str]) -> Contribution:
    """The one red row an area that could not contribute gets, named against `owners`, every name
    the report's checks have.

    The row is named after the area. When the report already has that name — a core check's,
    or a check an area contributed — it is the area's name numbered from 2, `<area> (2)`,
    `<area> (3)` and so on, the first that is free: the row is a name in the report like any
    other, and a name is never in it twice. It is named only once every contribution's names are
    known (`contributions`), because it stands in for rows the area could not give, and a stand-in
    that took a name first would turn away the healthy area whose row it is. No other failure row
    can take the same name: each is named after its own area, and no two areas share one.
    """
    area = answer.qualified.removesuffix(".doctor").rpartition(".")[2]
    name, number = area, 1
    while name in owners:
        number += 1
        name = f"{area} ({number})"
    return Contribution(checks=((name, Unregistered(answer.refused)),))


def discover_contributors() -> list[tuple[str, ModuleType | Exception]]:
    """Every `drybag.<area>.doctor`, in area-name order, with no shared registry, each under its
    qualified name with the module, or with the exception its import raised.

    The seam the report's discovery reads, and the one a test replaces to inject an area, as
    `cli.discover_registrars` is for commands.
    """
    return area_imports("doctor")


def contributions(core: Sequence[tuple[str, Callable[[Context], Row]]]) -> list[Contribution]:
    """What each area's `doctor.py` contributes, in area-name order, or one red row for an area
    that could not contribute. `core` is the core's checks, `checks.CHECKS`, whose rows come first
    in the report.

    **One failure policy.** Every way an area's contribution can fail is a defect in drybag's
    own code, and every one costs the same thing: that area's rows become one red row named
    after it (`_unregistered`), and the rest of the report stands. A `doctor.py` that
    fails to import, a `register()` that raises, one that answers something that is not a
    `Contribution` of `(name, check)` pairs, and one that contributes a name already in the
    report, alike — none ends the report, which is what a user has left when everything else is
    broken.

    A row's name is its only identity — the summary line, `--json` and the skill that relays the
    report all key on it — so a contributed name equal to a core check's, to one an earlier area
    contributed, or to another of the area's own, is that last failure, found here before any
    check is asked. The names are drybag's own code and never repository-authored, so the row
    prints them.

    Each area's `register()` is called once per call of this function, so whatever an area
    resolves lazily for its checks is resolved afresh for each report.
    """
    owners = dict.fromkeys((name for name, _ in core), "the core")
    # Two passes: every area's answer first, which records every name a contribution brings, and
    # only then the failure rows' names, so a stand-in never takes a real row's name.
    answers = [
        _registered(qualified, found, owners) for qualified, found in discover_contributors()
    ]
    return [
        answer.contribution if answer.contribution is not None else _unregistered(answer, owners)
        for answer in answers
    ]
