"""Whether a note counts as the owner's: promoted, at the bytes it holds now.

A note is reviewed when the sha256 of its current bytes is a `reviewed` entry in the machine's
trust record (`trust.json`, beside the machine file). The digest alone decides: the entry keeps the
note's resolved path only as a label, so identical bytes are the note the owner promoted wherever
they sit — another worktree's copy, a store that moved, a renamed file — and an edit is a note to
review again, while the same bytes written back anywhere are reviewed again. `memory promote`
writes the entry, no command removes one (withdrawing an approval is a hand edit of `trust.json`),
and the record kind is no registered approval kind, so nothing else asks about it.

**What it stops, and what it rests on.** Every writer of a note but the owner's own review: a clone
that commits a note ranked `startup: 1`, the harness's memory writer, an agent writing while it
works, and the owner's own edit after promoting. Each of them writes the note's bytes, and none
writes the record, which is outside every tree a repository or a store can carry, so each reaches a
session unreviewed: as data, labelled, and never as a standing rule. Against the agent in the
session, which can run `memory promote` from its shell, the record is a label and not a gate.

**Closed where it cannot answer.** A note that cannot be read, or a record that cannot be, is
unreviewed: nothing is lost, since an unreviewed note still reaches the session as data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from drybag import fsops
from drybag.memory.notes import Note
from drybag.trust.api import UnreadableTrust, approve_all, recorded

KIND = "reviewed"
REVIEWED: Literal["reviewed"] = "reviewed"
UNREVIEWED: Literal["unreviewed"] = "unreviewed"
Status = Literal["reviewed", "unreviewed"]
# What an unreviewed note carries where it is named: its heading in the volatile bundle, inside the
# region `trust.wrap` marks as data, and its line in the index.
UNREVIEWED_LABEL = " [unreviewed]"
Record = frozenset[str]


def digest(note: Path) -> str:
    """The sha256 of the note's bytes, read as every note is, to the regular-file read cap."""
    return hashlib.sha256(fsops.read_regular_bytes(note)).hexdigest()


def subject(note: Path) -> str:
    """The label the record keeps beside a note's digest: its resolved path, so a note reached
    through a group link (`developer` in every composed store) is named by where its bytes are and
    never by a path naming the binding id. Nothing matches on it."""
    return str(fsops.resolved(note))


def record(machine: Path | None) -> Record:
    """Every digest the machine's trust record holds as reviewed, read once for a caller asking of
    many notes, or none where the record cannot be read."""
    try:
        approvals = recorded(machine)
    except UnreadableTrust:
        return frozenset()
    return frozenset(approval.digest for (kind, _), approval in approvals.items() if kind == KIND)


def status(machine: Path | None, note: Note, digests: Record | None = None) -> Status:
    """`reviewed` iff the note's current digest is one the record holds as reviewed."""
    held = record(machine) if digests is None else digests
    try:
        current = digest(note.path)
    except OSError:
        return UNREVIEWED
    return REVIEWED if current in held else UNREVIEWED


def unreviewed(machine: Path | None, notes: Iterable[Note]) -> list[Note]:
    """The notes of `notes` the record does not hold at their current bytes, in their order."""
    held = record(machine)
    return [note for note in notes if status(machine, note, held) == UNREVIEWED]


def promote(machine: Path | None, notes: Iterable[Note]) -> dict[Path, str]:
    """Record each note at its current digest in one write of the record, and answer each note's
    path and the digest recorded. A record that cannot be read refuses (`UnreadableTrust`) rather
    than being replaced."""
    found = {note.path: digest(note.path) for note in notes}
    approve_all(
        machine,
        KIND,
        {subject(path): value for path, value in found.items()},
        now=datetime.now(UTC),
    )
    return found
