"""What each `SessionStart` entry injects, and how much of it fits.

One entry per bundle, each rendered by `memory session-context --bundle <name> --part <n>`,
and **no dispatcher handler**. That is not a style choice. Foundation's `hook <event>` takes
an event name and runs every handler registered for it, joining their contexts and clamping
the join to one platform cap — so the six numbered slots registered as handlers would concatenate
back into a single 10,000-character budget and be truncated, which is precisely the defect
a cap per `SessionStart` slot exists to remove. Invoked as separate `hooks.json` entries, each
slot gets its own cap, and the text is emitted raw rather than through a JSON envelope, so the
margin below is genuinely additive instead of fighting `ensure_ascii`'s six characters per
non-ASCII point.

Standing rules are never truncated, only flagged: a standing rule that does not arrive is a
standing rule that gets broken, and the set is hand-curated by a flag, so its size is
somebody's decision rather than an accident.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from drybag.config.schema import Config
from drybag.memory import trust
from drybag.memory.index import is_volatile
from drybag.memory.notes import Note, walk
from drybag.memory.reviewed import REVIEWED, UNREVIEWED_LABEL, record, status
from drybag.memory.store import Store, walked_groups


class Bundle(StrEnum):
    STANDING_RULES = "standing-rules"
    VOLATILE_NOTES = "volatile-notes"


# How many numbered entries `hooks/hooks.json` declares for each bundle. Raising one edits
# that shipped file, which the hooks area owns; `doctor` compares these against what a store
# actually needs and reports a bundle that does not fit.
SLOTS: dict[Bundle, int] = {
    Bundle.STANDING_RULES: 3,
    Bundle.VOLATILE_NOTES: 3,
}

# How much of `native_caps.hook_output_chars` this area keeps back. `_cap` subtracts it, so a
# part packed to `_cap` and emitted raw is the bundle plus one trailing newline: measured at
# `hook_output_chars = 10000`, 9,985 characters emitted with 15 to spare.
#
# **A named cap (CONTRIBUTING.md#named-caps), and no shipped file changes with it.** There is no
# such file for this one and there should not be: it is the headroom between a cap this area does
# not own and the way this area emits text, and a project that could widen it would be a project
# that could make its own bundles overrun the platform truncation silently. The cap it is subtracted
# from, `hook_output_chars`, is a `[native_caps]` key, which is where a change to the platform's
# number belongs.
#
# **For `hooks/hooks.json`, whose slot count `doctor` checks `SLOTS` against: its entries must
# not pass `--json`.** The margin is additive only because `memory session-context` prints
# the text raw. Through `cli._emit`'s `json.dumps({"summary": ...}, indent=2)` the envelope and
# the escaping both count against the same platform cap, and a cap-length standing bundle no
# longer fits — measured at 10,009 characters for the single-block bundle
# `test_the_margin_is_additive_because_the_text_is_emitted_raw` builds, and 10,146 for a
# realistic multi-note one, whose extra newlines each escape to two characters. Widening
# `CAP_MARGIN` is not the fix; not wrapping the output is.
CAP_MARGIN = 16

STANDING_LEAD = (
    "## Standing rules for this session, injected in full\n\n"
    "These hold for the whole session whatever it turns out to be about, which is exactly why "
    "routing them fails: there is no moment at which you would think to look one up. Each "
    "block below is the note itself, not a summary of it."
)
VOLATILE_LEAD = (
    "## Volatile working memory, injected in full\n\n"
    "Dated, perishable facts about what is currently broken, blocked or half-shipped. They "
    "were true when written: verify any path, flag or date before acting on one, and delete a "
    "note once it is resolved."
)


@dataclass(frozen=True)
class Fit:
    parts: int
    slots: int
    oversized: int

    @property
    def overflow(self) -> int:
        return max(0, self.parts - self.slots)

    @property
    def fits(self) -> bool:
        return self.overflow == 0 and self.oversized == 0


def _cap(config: Config) -> int:
    return max(1, config.native_caps.hook_output_chars - CAP_MARGIN)


def _notes(store: Store, config: Config) -> list[Note]:
    return walk(store.path, walked_groups(store, config)).notes


def _flag(note: Note, today: date, ttl: int) -> str:
    if not is_volatile(note.group_name):
        return ""
    if note.as_of is None:
        return "  [as_of MISSING — add it or delete the note]"
    age = (today - note.as_of).days
    return f"  [{age} days old — verify or delete before relying on it]" if age > ttl else ""


def _standing(notes: list[Note], config: Config) -> list[str]:
    ranked = [
        note for note in notes if note.startup is not None and not is_volatile(note.group_name)
    ]
    if not ranked:
        return []
    ranked.sort(key=lambda note: (note.startup or 0, note.name))
    blocks = [STANDING_LEAD] + [f"### {note.name}\n\n{note.body}" for note in ranked]
    total = sum(len(block.split()) for block in blocks)
    budget = config.budgets.effective("startup_rules_words")
    if total > budget:
        blocks.append(
            f"_The standing set has grown to {total} words (> {budget}). Nothing was dropped; "
            "prune the `startup` flags before adding another._"
        )
    return blocks


def _volatile(notes: list[Note], reviewed: set[Path], config: Config) -> list[str]:
    """The volatile notes, and every unreviewed note a standing rank would otherwise have made a
    standing rule; each unreviewed one labelled and inside the data region."""
    notes = [
        note
        for note in notes
        if is_volatile(note.group_name) or (note.startup is not None and note.path not in reviewed)
    ]
    if not notes:
        return []
    today = date.today()
    ttl = config.budgets.effective("volatile_ttl_days")
    # Undated notes first — an `as_of` a writer never added is the one that most needs looking
    # at — then the dated ones, newest first.
    #
    # One sort, where there were two. The second sorted on `as_of is not None`, which is
    # *exactly* the first element of the first sort's key, so that element decided nothing: the
    # second pass re-partitioned on the same predicate and only the date ordering inside each
    # partition survived. Verified over 2,000 random orderings that the one key below is
    # identical to the pair. Writing it as one sort is also what makes the ordering readable —
    # "undated first, then newest first" is not a claim anybody could check against two
    # `reverse=True` passes that partly undo each other.
    notes.sort(key=lambda note: (note.as_of is not None, -(note.as_of or date.min).toordinal()))

    def label(note: Note) -> str:
        return "" if note.path in reviewed else UNREVIEWED_LABEL

    def data(note: Note, text: str) -> str:
        return text if note.path in reviewed else trust.wrap(text)

    full = [
        f"### {note.name}"
        + (f" (as_of {note.as_of})" if note.as_of else "")
        + _flag(note, today, ttl)
        + label(note)
        + f"\n\n{note.body}"
        for note in notes
    ]
    if sum(len(block.split()) for block in full) <= config.budgets.effective(
        "volatile_notes_words"
    ):
        return [
            VOLATILE_LEAD,
            *(data(note, block) for note, block in zip(notes, full, strict=True)),
        ]
    lines = [
        (note, f"- {note.name}{_flag(note, today, ttl)}{label(note)} — {note.description}")
        for note in notes
    ]
    owned = "\n".join(line for note, line in lines if note.path in reviewed)
    others = "\n".join(line for note, line in lines if note.path not in reviewed)
    return [
        VOLATILE_LEAD,
        "Volatile memory has outgrown its budget; listing descriptions only — open any note "
        "that matters:",
        *([owned] if owned else []),
        *([trust.wrap(others)] if others else []),
    ]


def blocks(bundle: Bundle, store: Store, config: Config) -> list[str]:
    # Every bundle is built from the store's notes, and one question decides what each may carry:
    # whether the owner promoted the note at the bytes it holds now (`memory.reviewed`). A note
    # that is not reviewed — one the repository shipped, one an agent or the harness's writer
    # wrote, one edited since it was promoted — reaches a session only as data, inside the region
    # `trust.wrap` marks, and never as a standing rule, whatever it says about itself.
    notes = _notes(store, config)
    held = record(store.machine)
    reviewed = {note.path for note in notes if status(store.machine, note, held) == REVIEWED}
    if bundle is Bundle.STANDING_RULES:
        return _standing([note for note in notes if note.path in reviewed], config)
    return _volatile(notes, reviewed, config)


def split(parts: Sequence[str], cap: int) -> list[str]:
    """Pack blocks into parts of at most `cap` characters, truncating none of them."""
    packed: list[str] = []
    current: list[str] = []
    size = 0
    for block in parts:
        length = len(block) + (2 if current else 0)
        if current and size + length > cap:
            packed.append("\n\n".join(current))
            current, size = [], 0
            length = len(block)
        current.append(block)
        size += length
    if current:
        packed.append("\n\n".join(current))
    return packed


def fit(bundle: Bundle, store: Store, config: Config) -> Fit:
    cap = _cap(config)
    parts = split(blocks(bundle, store, config), cap)
    return Fit(
        parts=len(parts),
        slots=SLOTS[bundle],
        oversized=sum(1 for part in parts if len(part) > cap),
    )


# What an oversized part is replaced with. drybag's own words and two values drybag chose —
# the bundle name and the slot number — so nothing repository-authored rides out through a
# channel that has just been established not to be able to carry it safely.
OVERSIZED_NOTICE = (
    "drybag: the {bundle} bundle's part {part} is a single block larger than one "
    "session-start slot, so it was withheld rather than delivered cut in half. Nothing here is "
    "missing from the store; run `drybag memory inventory` to see which note is largest, and "
    "split or shorten it."
)


def render(bundle: Bundle, store: Store, config: Config, *, part: int = 1) -> str | None:
    """One numbered slot's text, or `None` when there is no such part.

    **An oversized part is a notice, not the part.** `split` never breaks a block, so a single
    block larger than the cap becomes a part larger than the cap — `fit` reports that as
    `Fit.oversized`, and `render` used to hand it out anyway and exit 0. Nothing on the hook
    path runs `fit`, so the only reader was `doctor`, and what actually happened was that the
    platform truncated it. Measured, at `hook_output_chars = 10000`: a standing bundle with one
    20,288-character part arrived with **one** of its two region markers — the model got an
    opening delimiter, the lead sentence "It ends at the matching end marker and nowhere else",
    and no end marker, which is the one thing `trust.wrap`'s nonce region exists to make
    impossible. Handing out a region known to arrive unterminated is worse than handing out
    nothing, and saying so is better than either.

    The notice carries no repository content — the bundle name and the slot number are
    drybag's own — for the obvious reason that the channel has just been established not to
    be able to carry any safely. It is emitted whatever the configured cap, because a truncated
    notice still reads as a notice while a truncated region does not.
    """
    cap = _cap(config)
    parts = split(blocks(bundle, store, config), cap)
    if part < 1 or part > len(parts):
        return None
    text = parts[part - 1]
    if len(text) > cap:
        return OVERSIZED_NOTICE.format(bundle=bundle.value, part=part)
    return text
