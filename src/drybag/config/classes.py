"""One class per configuration key, the layers that may set each class, and `resolve`.

A value reaches a session from one of several layers — the preset, the repository's
`drybag.toml`, the overlay's common and per-project files, and the machine file — and whether
a layer may set a key is a property of the key, not of the layer that happens to write it. So
every key has one class (`CLASSES`), and every class names the layers it admits
(`ALLOWED`). `resolve` applies that over a stack of layers: a layer the class does not admit
contributes nothing and says so, and of the rest the highest in `ORDER` wins, or a `merge` the
caller names folds them lowest first, with the layer each value came from.

`resolve` decides who may set a key, and where a value came from. It builds the owner's layer
(`config.owner`, from the preset and the machine file), refuses a repository's machine-only keys
for the NOTE load (`config.loader`), and builds a session's preferences and session budgets once,
over the preset, the repository, the overlay's files and the machine file
(`config.session`), whose provenance `doctor`'s `preferences` row prints; the rest of the
project's `Config` is built by `config.loader`, from the repository's file over the preset.

The keys are dotted, `section.key`, and a section wildcard `section.*` covers every key of its
section; `tests/config/test_classes.py` holds every field of `Config`'s sections to a row, so a
field added to the schema has a class before anything may set it (`CONTRIBUTING.md`,
"Configuration").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from drybag.printed import clipped


class KeyClass(StrEnum):
    """What a key is, which decides who may set it."""

    # The owner's alone: a repository never sets one, whatever it writes.
    MACHINE_ONLY = "machine-only"
    # A way of working the owner, the overlay or the repository may choose.
    PREFERENCE = "preference"
    # A bound on what a session loads, which the owner may move as the repository may.
    SESSION_PARAMETER = "session-parameter"
    # A gate's input: what a pull request is judged by, so only the base branch's file sets it.
    GATE_PARAMETER = "gate-parameter"
    # A fact about the repository itself, which only its own file states.
    REPOSITORY_ONLY = "repository-only"
    # A limit of the platform the plugin runs on, which only the shipped preset carries.
    PLUGIN_OWNED = "plugin-owned"


class LayerName(StrEnum):
    """Where a value came from. The value is the word a note names the layer by.

    The overlay's scopes are named for the overlay, `OVERLAY_COMMON`, `OVERLAY_TAG` (one layer for
    each tag the project's record lists, told apart by `Layer.label`) and `OVERLAY_PROJECT`, since
    "project" means the repository everywhere else a person reads it (its `drybag.toml`, Claude
    Code's project settings)."""

    PRESET = "preset"
    REPOSITORY = "repository"
    OVERLAY_COMMON = "overlay common"
    OVERLAY_TAG = "overlay tag"
    OVERLAY_PROJECT = "overlay project"
    MACHINE_FILE = "machine file"


# The layers lowest first: a value from a later layer wins over an earlier one's.
ORDER: tuple[LayerName, ...] = (
    LayerName.PRESET,
    LayerName.REPOSITORY,
    LayerName.OVERLAY_COMMON,
    LayerName.OVERLAY_TAG,
    LayerName.OVERLAY_PROJECT,
    LayerName.MACHINE_FILE,
)
_RANK = {name: rank for rank, name in enumerate(ORDER)}

_REPOSITORY_SIDE = frozenset({LayerName.PRESET, LayerName.REPOSITORY})
ALLOWED: Mapping[KeyClass, frozenset[LayerName]] = MappingProxyType(
    {
        KeyClass.MACHINE_ONLY: frozenset(ORDER) - {LayerName.REPOSITORY},
        KeyClass.PREFERENCE: frozenset(ORDER),
        KeyClass.SESSION_PARAMETER: frozenset(ORDER),
        KeyClass.GATE_PARAMETER: _REPOSITORY_SIDE,
        KeyClass.REPOSITORY_ONLY: _REPOSITORY_SIDE,
        KeyClass.PLUGIN_OWNED: frozenset({LayerName.PRESET}),
    }
)

# The two halves of `Budgets.NAMES`: what a gate judges a pull request by, and what bounds the
# notes a session loads. `tests/config/test_classes.py` holds their union to `Budgets.NAMES`.
GATE_BUDGETS: frozenset[str] = frozenset(
    {
        "agents_md_lines",
        "agents_md_words",
        "status_lines",
        "roadmap_prose_lines",
        "roadmap_prose_words",
    }
)
SESSION_BUDGETS: frozenset[str] = frozenset(
    {
        "memory_index_words",
        "startup_rules_words",
        "volatile_notes_words",
        "volatile_ttl_days",
    }
)

CLASSES: Mapping[str, KeyClass] = MappingProxyType(
    {
        "personal.*": KeyClass.MACHINE_ONLY,
        "sync.*": KeyClass.MACHINE_ONLY,
        "guards.*": KeyClass.PREFERENCE,
        "memory.mode": KeyClass.PREFERENCE,
        "memory.groups": KeyClass.PREFERENCE,
        "memory.index_extra": KeyClass.PREFERENCE,
        **{f"budgets.{name}": KeyClass.SESSION_PARAMETER for name in sorted(SESSION_BUDGETS)},
        **{f"budgets.{name}": KeyClass.GATE_PARAMETER for name in sorted(GATE_BUDGETS)},
        "drybag.version": KeyClass.REPOSITORY_ONLY,
        "drybag.preset": KeyClass.REPOSITORY_ONLY,
        "project.*": KeyClass.REPOSITORY_ONLY,
        "paths.*": KeyClass.REPOSITORY_ONLY,
        "commit_messages.*": KeyClass.REPOSITORY_ONLY,
        "native_caps.*": KeyClass.PLUGIN_OWNED,
    }
)

# A layer that wrote a key its class does not admit. The key is printed through
# `printed.clipped`, since a key under a wildcard is any quoted TOML text the layer chose, and the
# value never: it is exactly what the layer was not allowed to say.
REFUSED_LAYER_NOTE = (
    "{key} cannot be set by the {layer}; its value there is ignored and the one beneath it applies"
)


def class_of(key: str) -> KeyClass:
    """`key`'s class: its own row first, then its section's wildcard; `KeyError` for neither.

    A name with no dot is a section, not a key, and has no class even where its wildcard does.
    """
    if key in CLASSES:
        return CLASSES[key]
    section, dot, _ = key.partition(".")
    wildcard = f"{section}.*"
    if not dot or wildcard not in CLASSES:
        raise KeyError(key)
    return CLASSES[wildcard]


def said(name: LayerName, label: str = "") -> str:
    """A layer as a person reads it: `overlay tag ops`, or its name where it has no label."""
    return f"{name} {label}" if label else str(name)


@dataclass(frozen=True)
class Layer:
    """One layer's values, keyed by dotted key. `label` tells apart the layers of one name: the
    tag an `OVERLAY_TAG` layer is for, which the owner wrote and `config.plates` held to `TAG`, so
    it prints as is."""

    name: LayerName
    values: Mapping[str, object]
    label: str = ""


@dataclass(frozen=True)
class Resolution:
    """The winning value of every key some admitted layer set, the layer it came from, and a note
    for every value a layer wrote that its key's class does not admit."""

    values: Mapping[str, object]
    provenance: Mapping[str, LayerName]
    notes: tuple[str, ...]
    # The label of the layer a value came from (empty where its layer has none).
    labels: Mapping[str, str] = field(default_factory=dict)

    def said(self, key: str) -> str:
        """The layer `key`'s value came from, as `said` reads it; `KeyError` where no layer set
        `key`."""
        return said(self.provenance[key], self.labels.get(key, ""))


def resolve(
    stack: Sequence[Layer],
    *,
    merge: Mapping[str, Callable[[object, object], object]] | None = None,
) -> Resolution:
    """Every key any layer in `stack` sets, resolved by its class.

    The layers are ranked by `ORDER`, not by their place in `stack`. A layer `ALLOWED` does not
    admit for a key's class contributes nothing to that key and adds `REFUSED_LAYER_NOTE`; of
    the admitted values the highest layer's wins, or, for a key `merge` names, `merge[key]` folds
    them lowest first. The provenance is the highest admitted layer either way. A key with no
    class raises `KeyError`: the caller removes unknown keys before it resolves.
    """
    folds = merge or {}
    values: dict[str, object] = {}
    provenance: dict[str, LayerName] = {}
    labels: dict[str, str] = {}
    notes: list[str] = []
    for layer in sorted(stack, key=lambda layer: _RANK[layer.name]):
        for key, value in layer.values.items():
            if layer.name not in ALLOWED[class_of(key)]:
                notes.append(
                    REFUSED_LAYER_NOTE.format(key=clipped(key), layer=said(layer.name, layer.label))
                )
                continue
            if key in folds and key in values:
                values[key] = folds[key](values[key], value)
            else:
                values[key] = value
            provenance[key] = layer.name
            labels[key] = layer.label
    return Resolution(
        values=MappingProxyType(values),
        provenance=MappingProxyType(provenance),
        notes=tuple(notes),
        labels=MappingProxyType(labels),
    )
