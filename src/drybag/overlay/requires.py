"""What an overlay declares it needs, and whether a running drybag meets it.

The declaration is read in the one form the template ships, `>=X.Y.Z`, and no other: a caller
that wants a different comparison has a different question, and answering it here would grow
a parser nothing has asked for yet. `requires_of`'s return value is the normalised string a
caller may print — it is what the overlay itself declared, stripped, not a value this module
built out of parts.
"""

from __future__ import annotations

import re
from pathlib import Path

from drybag.overlay.identity import identity_record
from drybag.semver import COMPONENT, VERSION, pre_release

# A floor is built from `semver`'s component, so a declared floor and a running version bound each
# component alike; `semver.COMPONENT` says why the bound is what it is.
_FLOOR = re.compile(rf"^>={COMPONENT}\.{COMPONENT}\.{COMPONENT}$")


def requires_of(root: Path) -> str | None:
    """The `requires` string an overlay's identity record declares, stripped.

    `None` when the record is absent, unreadable, or does not carry a non-empty string —
    every one of those is "nothing declared", and none of them is this reader's business to
    tell apart. Read through `identity.identity_record`, a regular file only, so a FIFO there
    cannot block the SessionStart hook this is asked on.
    """
    record = identity_record(root)
    value = None if record is None else record.get("requires")
    return value.strip() if isinstance(value, str) and value.strip() else None


def satisfies(spec: str, version: str) -> bool | None:
    """`True`/`False` for `>=X.Y.Z` against an `X.Y.Z[...]` version: met by a later triple, and by
    the floor's own unless the version is a pre-release of it (`semver.pre_release`).

    So a pre-release of the floor (`1.0.0rc1`, `1.0.0rc1.post2`, `1.0.0.dev3+g1234abc` against
    `>=1.0.0`) does not meet it, as `semver.later` orders the release after the first, and any
    other suffix on the floor's triple (`.post1`, `+local`) meets it, which is the answer such a
    build always had.

    `None` for any other spec shape or a version this reader cannot parse — a caller that gets
    `None` back has an unreadable declaration, not a false one.

    **It answers, for every string.** `None` is the whole of "this reader cannot read it", and
    the two grammars, `_FLOOR` and `semver.VERSION`, are what make that true: every component
    they admit is an integer `int()` converts, so no input reaches this function's arithmetic
    that could raise out of it.
    Both callers rest on that — `doctor`'s row turns an exception into "this check could not
    run", and the session handler's backstop turns one into silence for the whole line — so a
    reader that raises for an input a repository or an owner can write is a reader that deletes
    the lines around it.
    """
    floor = _FLOOR.match(spec)
    running = VERSION.match(version)
    if floor is None or running is None:
        return None
    ours = tuple(int(part) for part in running.groups())
    wanted = tuple(int(part) for part in floor.groups())
    return ours > wanted or (ours == wanted and not pre_release(version))
