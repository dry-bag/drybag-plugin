"""What a consumer asks for, and what the engine answers.

`Template` is a description, not a file: `overlay/`, which owns the template *content*, builds
these, and this area never reads the plugin's own directory.
That is what keeps the engine testable without shipping any template at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from drybag.scaffold.manifest import Record


class Verb(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP_MODIFIED = "skip_modified"


WRITING = frozenset({Verb.CREATE, Verb.UPDATE})


@dataclass(frozen=True)
class Template:
    id: str
    target: str
    render: Callable[[], str]
    # The shipped file the bytes come from, which the template's record keeps as `Record.template`.
    source: str = ""


@dataclass(frozen=True)
class Action:
    verb: Verb
    artifact_id: str
    target: str
    payload: str | None
    reason: str
    record: Record | None


@dataclass(frozen=True)
class Refused:
    artifact_id: str
    target: str
    reason: str


@dataclass(frozen=True)
class Plan:
    """What `plan` decided, and what `apply` then writes — the same object, unchanged between.

    `frozen=True` with three `list` fields froze the *bindings* and nothing else, so the plan a
    user approved through `render_report` could still be appended to, or have an action's target
    rewritten, before `apply` consumed it. That weakens the one claim this split exists to make:
    "the report a user approves is produced by the same code path that then runs". The fields
    are tuples, so the object really is what it was when it was rendered.

    `plan` builds its three lists as lists and hands them here; the conversion happens once, at
    the boundary, which is where the guarantee begins.
    """

    actions: tuple[Action, ...] = ()
    refusals: tuple[Refused, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def writes(self) -> list[str]:
        return [action.target for action in self.actions if action.verb in WRITING]


@dataclass(frozen=True)
class Applied:
    """What `apply` actually did. Tuples for the same reason `Plan` uses them: it is a report."""

    written: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
