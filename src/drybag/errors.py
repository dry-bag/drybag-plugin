"""Exit-code-bearing exceptions shared by every command, 1 findings and 2 a refusal, and the one
rule that reads a resolution failing with one of them as no answer."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


class DrybagError(Exception):
    """Base class; never raised directly."""


class Failure(DrybagError):
    """Findings or a failed operation: exit code 1."""


class Refusal(DrybagError):
    """A refusal or an internal error a caller must never read as permission: exit code 2."""


def resolved_or_none(ask: Callable[[], _T]) -> _T | None:
    """`ask()`, or `None` when it fails the way a resolution may: a `Failure`, a `Refusal` or an
    `OSError`.

    One rule for every answer a `doctor` row reads and does not own — the overlay root the
    machine file records, the note store a project resolves — so the report reads a machine file
    that names an overlay nothing can read, and a `drybag.toml` that makes the store refuse,
    the same way: as nothing to look at, which is a row's skip, and never "this check could not
    run", which is red and gates the exit code for something the repository did not do wrong.
    """
    try:
        return ask()
    except (Failure, Refusal, OSError):
        return None
