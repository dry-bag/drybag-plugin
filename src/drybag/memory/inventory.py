"""What a sweep reads (`memory inventory`, `docs/cli.md#drybag-memory-inventory`).

`--json` is the primary output: the `memory-sweep` skill consumes this, and the human
rendering is a convenience. Staleness applies only to the volatile group — a durable note has
no expiry, and flagging one teaches the reader to ignore the flag.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from drybag.config.schema import Config
from drybag.memory.index import Reconciliation, is_volatile
from drybag.memory.notes import Provenance
from drybag.memory.reviewed import REVIEWED, record, status
from drybag.memory.store import Store


@dataclass(frozen=True)
class Entry:
    name: str
    group: str
    words: int
    type: str | None
    startup: int | None
    as_of: str | None
    stale: bool
    provenance: str
    # Whether the owner promoted the note at the bytes it holds now (`memory.reviewed`).
    reviewed: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inventory(reconciled: Reconciliation, config: Config, store: Store) -> list[Entry]:
    today = date.today()
    held = record(store.machine)
    ttl = config.budgets.effective("volatile_ttl_days")
    entries = [
        Entry(
            name=note.name,
            group=note.group_name,
            words=note.words,
            type=note.type.value if note.type else None,
            startup=note.startup,
            as_of=note.as_of.isoformat() if note.as_of else None,
            stale=(
                is_volatile(note.group_name)
                and (note.as_of is None or (today - note.as_of).days > ttl)
            ),
            provenance=note.index_provenance.value,
            reviewed=status(store.machine, note, held) == REVIEWED,
        )
        for note in reconciled.notes
    ]
    entries.sort(key=lambda entry: (-entry.words, entry.name))
    return entries


def totals(entries: list[Entry]) -> dict[str, int]:
    """Headline counts over what `inventory` returned.

    No `config`. The parameter was here and the body opened with `del config` — a dead argument
    baked into the import surface (`memory/api.py`), where it gets more expensive to remove with
    every consumer that writes a call against it. Every number below comes from the entries
    alone, and an entry already carries the budget-derived fact (`stale`) that a config would
    have been consulted for.
    """
    return {
        "notes": len(entries),
        "words": sum(entry.words for entry in entries),
        "provisional": sum(1 for e in entries if e.provenance == Provenance.PROVISIONAL.value),
        "stale": sum(1 for entry in entries if entry.stale),
        "undated": sum(1 for e in entries if is_volatile(e.group) and e.as_of is None),
        "unreviewed": sum(1 for entry in entries if not entry.reviewed),
        # The same three conditions `bundles.blocks` selects a standing rule on, and for the same
        # reasons: a volatile note is a dated, perishable fact, injected by the volatile bundle
        # whatever `startup` it carries, and an unreviewed note is data. Counting every flagged
        # note made this headline number disagree with what actually reaches the model — and this
        # number is what the `memory-sweep` skill reads and quotes.
        "standing": sum(
            1 for e in entries if e.startup is not None and not is_volatile(e.group) and e.reviewed
        ),
    }
