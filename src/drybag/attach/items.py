"""The overlay's named items, and the one fold over the plates that settles a name two plates hold.

An item is a rule (a `*.md` regular file in `claude/rules/`), a Codex rule file (a regular file in
`codex/`), a skill or an agent (an entry directly in `claude/skills/` or `claude/agents/`), named by
its file or entry name; a dot-name is none. Over the plates in their order (`config.plates`) **the
later plate wins the whole item, which takes the winning plate's place**. The readers of items
(`attach.rules`, `permissions.codex_rules`, `render.read_parts`) take the winners, and
`attach.tags` the holders. Hook entries and allow rules are no items: they union.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.config.plates import Plate
from drybag.printed import quoted

# The per-harness subdirectories of a plate.
PROJECT_CLAUDE = "claude"
PROJECT_CODEX = "codex"


@dataclass(frozen=True)
class Kind:
    """A kind of item, as a sentence says it, where it stands in a plate (`""`: in `harness`
    itself), the suffix its name ends in and whether only a regular file is one."""

    said: str
    harness: str
    directory: str
    suffix: str
    files: bool


RULE = Kind("rule", PROJECT_CLAUDE, "rules", ".md", files=True)
CODEX_RULE = Kind("Codex rule", PROJECT_CODEX, "", "", files=True)
SKILL = Kind("skill", PROJECT_CLAUDE, "skills", "", files=False)
AGENT = Kind("agent", PROJECT_CLAUDE, "agents", "", files=False)
KINDS = (RULE, CODEX_RULE, SKILL, AGENT)


@dataclass(frozen=True)
class Folded:
    """Each name's path in the plate that wins it, in fold order, and the plates holding it."""

    winners: dict[str, Path]
    holders: dict[str, tuple[Plate, ...]]


def fold(
    plates: Sequence[Plate], kind: Kind, refusal: Callable[[str], Exception] | None = None
) -> Folded:
    """`kind`'s items over `plates`, each plate's in name order. A directory that cannot be listed,
    or whose entries cannot be asked about, raises `refusal` of `fsops.unreadable`'s sentence, as
    `fsops.reading` does, or holds none without one."""
    winners: dict[str, Path] = {}
    holders: dict[str, list[Plate]] = {}
    for plate in plates:
        directory = plate.directory / kind.harness / kind.directory
        for name in _names(directory, kind, refusal):
            winners.pop(name, None)
            winners[name] = directory / name
            holders.setdefault(name, []).append(plate)
    return Folded(winners, {name: tuple(held) for name, held in holders.items()})


def _names(directory: Path, kind: Kind, refusal: Callable[[str], Exception] | None) -> list[str]:
    try:
        if not fsops.is_dir(directory):
            return []
        return [
            name
            for name in sorted(os.listdir(directory))
            if not name.startswith(".")
            and name.endswith(kind.suffix)
            and (not kind.files or fsops.is_file(directory / name))
        ]
    except OSError as exc:
        if refusal is None:
            return []
        raise refusal(fsops.unreadable(quoted(str(directory)), exc)) from exc
