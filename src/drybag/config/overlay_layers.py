"""The overlay's layers of a session's preferences: `<overlay>/common/drybag.toml` for every
checkout, and for a checkout this machine binds, `<overlay>/groups/<tag>/drybag.toml` for each
tag the project's record lists and `<overlay>/projects/<project>/drybag.toml`, in the order of
`config.plates` (a later layer wins).

**The project comes from the binding record, never from the repository.** `binding.bound_project`
reads the id in the checkout's git common directory and this machine's record of it, so a clone
that commits the `[project] name` of one of the owner's projects has no project layer and no tag
layer, and only the common file applies to it. The overlay is the one `[overlay] root` of the
machine file names (`config.overlay.overlay_root`).

**Read the way a session reads `drybag.toml`, noting rather than refusing.** A name this
drybag does not read is ignored, and a value it cannot use is left out so the layer beneath
applies, each with a note naming the file and the key; a key whose class does not admit the
overlay's layers (`classes.ALLOWED`: a gate's input, a fact about the repository) is handed to
`classes.resolve`, which refuses it with its own note. A missing file, an overlay the machine
file does not record, a machine file `overlay_root` cannot read, and a git that cannot be asked
for the binding are each no layer and one note, never an exception: a session never dies of its
configuration (`config.session`). Every name and path a note carries is printed through
`drybag.printed`, since the overlay and the checkout wrote them.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.config.classes import ALLOWED, Layer, LayerName, class_of, said
from drybag.config.overlay import overlay_root
from drybag.config.owner import Guards
from drybag.config.plates import COMMON, Plate, PlateKind, plates
from drybag.config.reading import (
    UNPARSEABLE,
    ConfigError,
    MachineConfigError,
    coerced,
    read_fault,
    table_types,
)
from drybag.config.schema import (
    MEMORY_MODES,
    Budgets,
    CommitMessages,
    Drybag,
    Memory,
    Paths,
    Personal,
    Project,
)
from drybag.gitenv import GitUnavailable
from drybag.printed import clipped, quoted

CONFIG_FILE = "drybag.toml"
# A named cap: the most bytes of an overlay file a session reads, the bound it reads the
# repository's `drybag.toml` to (`session.SESSION_READ_LIMIT`, which says why), for the same
# hook's ten seconds. No shipped file states it.
READ_LIMIT = 256 * 1024

NO_OVERLAY = "the machine file records no overlay root, so no overlay layer applies"
# `error` is `read_machine_toml`'s refusal, which names the machine file and never its text.
MACHINE_UNREAD = "{error}, so no overlay layer applies"
NOT_THERE = "{path} is not there, so the {layer} layer sets nothing"
# `cause` is `GitUnavailable`'s own sentence, which names the checkout through `printed`.
NO_GIT = "{cause}, so the overlay project layer sets nothing"
UNKNOWN = "{path}: {name} is not a setting this drybag reads; it is ignored"
NOT_A_TABLE = "{path}: [{table}] must be a table; it is ignored"
UNUSABLE = "{path}: {problem}; the value beneath it applies"

_NAME_OF = {
    PlateKind.COMMON: LayerName.OVERLAY_COMMON,
    PlateKind.TAG: LayerName.OVERLAY_TAG,
    PlateKind.PROJECT: LayerName.OVERLAY_PROJECT,
}

# The tables a `drybag.toml` holds, and the machine owner's two, each with the schema class
# `reading.table_types` holds its keys to.
_TABLES: dict[str, type[Any]] = {
    "drybag": Drybag,
    "project": Project,
    "paths": Paths,
    "memory": Memory,
    "budgets": Budgets,
    "commit_messages": CommitMessages,
    "personal": Personal,
    "guards": Guards,
}


def read(
    root: Path | None, *, machine: Path | None = None
) -> tuple[tuple[Layer, ...], tuple[str, ...], str | None]:
    """The overlay's layers for the checkout at `root`, lowest first, the notes reading them gave,
    and the project the binding names (`None` where it names none, or where no overlay is
    recorded, which leaves the binding unasked). `machine` is the machine file, as `overlay_root`
    takes it; `root` is `None` for a hook with no project root, which has no binding and gets the
    common layer only, as an unbound checkout does."""
    notes: list[str] = []
    try:
        overlay = overlay_root(machine)
    except MachineConfigError as exc:
        return (), (MACHINE_UNREAD.format(error=exc),), None
    if overlay is None:
        return (), (NO_OVERLAY,), None
    project = _bound(root, notes)
    if project is None:
        found: tuple[Plate, ...] = (Plate(PlateKind.COMMON, COMMON, overlay / COMMON),)
    else:
        found, listed = plates(overlay, project)
        notes.extend(listed)
    layers: list[Layer] = []
    for plate in found:
        name = _NAME_OF[plate.kind]
        label = plate.name if plate.kind is PlateKind.TAG else ""
        layer = _layer(plate.directory / CONFIG_FILE, name, label, notes)
        if layer is not None:
            layers.append(layer)
    return tuple(layers), tuple(notes), project


def _bound(root: Path | None, notes: list[str]) -> str | None:
    """The project this machine binds `root` to, or `None`; a git that cannot be asked is a
    note."""
    if root is None:
        return None
    # Deferred: `binding` reads its data directory and its record through `config`, so a
    # module-level import would make the two packages import each other.
    from drybag.binding import bound_project

    try:
        bound = bound_project(root)
    except GitUnavailable as exc:
        notes.append(NO_GIT.format(cause=exc))
        return None
    return None if bound is None else bound.project


def _layer(path: Path, name: LayerName, label: str, notes: list[str]) -> Layer | None:
    """The file at `path` as the layer `name` (`label` the tag a tag's layer is for), or `None`
    with a note where it cannot be read. A tag with no file sets nothing and says nothing: a tag
    is often only notes."""
    shown = quoted(str(path))
    try:
        raw = tomllib.loads(fsops.read_regular_text(path, limit=READ_LIMIT))
    except FileNotFoundError:
        if name is not LayerName.OVERLAY_TAG:
            notes.append(NOT_THERE.format(path=shown, layer=said(name, label)))
        return None
    except (UnicodeDecodeError, OSError, *UNPARSEABLE) as exc:
        notes.append(read_fault(shown, exc, f"so the {said(name, label)} layer sets nothing"))
        return None
    return Layer(name, _values(raw, shown, name, notes), label)


def _values(
    raw: dict[str, Any], shown: str, name: LayerName, notes: list[str]
) -> dict[str, object]:
    """`raw`'s keys as dotted keys: each one the layer may set held to its schema type, each one
    it may not passed on for `resolve` to refuse, and every other name noted."""
    values: dict[str, object] = {}
    for table, entries in raw.items():
        cls = _TABLES.get(table)
        if cls is None:
            notes.append(UNKNOWN.format(path=shown, name=clipped(table)))
            continue
        if not isinstance(entries, dict):
            notes.append(NOT_A_TABLE.format(path=shown, table=table))
            continue
        types = table_types(cls)
        for key, value in entries.items():
            dotted = f"{table}.{key}"
            if key not in types:
                notes.append(UNKNOWN.format(path=shown, name=clipped(dotted)))
            elif name not in ALLOWED[class_of(dotted)]:
                values[dotted] = value
            else:
                try:
                    values[dotted] = _usable(dotted, types[key], value)
                except ConfigError as exc:
                    notes.append(UNUSABLE.format(path=shown, problem=exc))
    return values


def _usable(dotted: str, annotation: Any, value: object) -> object:
    """`value` as the schema type `annotation` holds it, and `memory.mode` held to the modes the
    loader holds it to; a `ConfigError` naming the key otherwise, never the value."""
    held = coerced(dotted, annotation, value)
    if dotted == "memory.mode" and held not in MEMORY_MODES:
        raise ConfigError(f"memory.mode must be one of {', '.join(MEMORY_MODES)}", key=dotted)
    return held
