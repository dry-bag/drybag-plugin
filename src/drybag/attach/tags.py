"""Where two of a project's tags hold one name, as `doctor` says it.

A later plate wins an item name or a value (`config.plates`), so two tags that both hold a rule, a
skill, an agent, a Codex rule file or a setting are settled by their order in the project's record
(`tags` in `projects/<name>/project.toml`), and the owner may not have meant that order to decide.
`collisions` names each such name, the tags that hold it in list order and the one that wins. A tag
and `common` or the project holding one name is precedence and is not named: it is how a plate
overrides the plate beneath it.

**A convenience and not a control.** The overlay and its record are the owner's own, and the
sentences are for the owner, so nothing here refuses and nothing is gated on it; `doctor` warns. The
names come off the overlay's disk and print through `printed.quoted`.
"""

from __future__ import annotations

from collections.abc import Sequence

from drybag.attach.items import KINDS, fold
from drybag.config.classes import ALLOWED, Layer, LayerName, class_of
from drybag.config.plates import Plate, PlateKind
from drybag.printed import quoted

HELD = "the {kind} {name} is held by the tags {tags}: the later, {winner}, wins"


def collisions(held: Sequence[Plate], layers: Sequence[Layer]) -> tuple[str, ...]:
    """One sentence for each item name or dotted key two or more tags of the plates `held` hold: the
    kind, the name, the tags in list order and the later one, which wins. The items' holders are
    the plates' fold (`attach.items`), the one the readers take their winners from; a directory
    that cannot be listed holds none here, which `attach` says itself. `layers` are the overlay's
    value layers (`overlay_layers.read`), of which only the tag layers are asked, and of theirs only
    the keys a tag may set."""
    found: list[str] = []
    for kind in KINDS:
        for name, holders in fold(held, kind).holders.items():
            tags = [plate.name for plate in holders if plate.kind is PlateKind.TAG]
            if len(tags) > 1:
                found.append(_sentence(kind.said, name, tags))
    keys: dict[str, list[str]] = {}
    for layer in layers:
        if layer.name is LayerName.OVERLAY_TAG:
            # Only a key `resolve` admits the tag layer for: of any other no tag wins anything.
            for key in (key for key in layer.values if layer.name in ALLOWED[class_of(key)]):
                keys.setdefault(key, []).append(layer.label)
    found.extend(_sentence("setting", key, tags) for key, tags in keys.items() if len(tags) > 1)
    return tuple(found)


def _sentence(kind: str, name: str, tags: list[str]) -> str:
    return HELD.format(kind=kind, name=quoted(name), tags=", ".join(tags), winner=tags[-1])
