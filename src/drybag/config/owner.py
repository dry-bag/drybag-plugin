"""The machine owner's layer: personal values, the guards' switches, the session budgets and the
note sync's switch.

The preset's `[defaults.personal]`, `Guards()`, no session budget and `Sync()`, overridden by the
machine file's `[personal]`, `[guards]`, `[budgets]` and `[sync]`, resolved through
`classes.resolve` as the layers `PRESET` and `MACHINE_FILE`. The machine file is the owner's, never
a repository's (`config.machine` says where it is), so it is the last word on a guard: a
repository's `[guards]` sets the default, the overlay's files move it, and this file's `[guards]`
wins over all of them in the session's one resolution (`config.session`), where this layer is the
top one.

Two readers ask, and they differ only over a file that does not load. A command the owner runs
refuses it (`strict`, which every load of a command asks for), as `config.overlay.overlay_root`
refuses it; a session must go on, and the preset's values stand in, with `MACHINE_FILE_NOTE`, and
the refusal is kept (`Owner.unloaded`), so `doctor` can say that every command refuses the file
the session read past. In a file that loads, an unknown key, a gate's budget or an unusable value
costs that key alone, with a note, for both: what the owner sets is honoured, and what this
drybag cannot use is said and never a block. Refused, a key a later release's `setup` writes
would stop every older command on the machine — the CLI and the plugin's hooks are easily two
versions — over a key none of them reads, and no boundary rests on the refusal: no repository
writes this file. A command says the notes on stderr (`said_on_stderr`), once, where it resolves
the layer, and hands that layer to every load it makes (`rebased` stands it on the preset each
document names), so the machine file is read once a command and no note is said twice. Every
sentence names the file and the key — the key through `printed.clipped`, since a TOML key is any
quoted text — and never a value.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from drybag.config.classes import GATE_BUDGETS, Layer, LayerName, resolve
from drybag.config.reading import (
    ConfigError,
    MachineConfigError,
    coerced,
    read_machine_toml,
    table_types,
)
from drybag.config.schema import Budgets, Personal
from drybag.presets import DEFAULT_PRESET, defaults_of, load_preset
from drybag.printed import clipped


@dataclass(frozen=True)
class Guards:
    """The session guards' switches. On unless a layer switches one off: the repository's
    `drybag.toml` sets the default, and the overlay's files and the machine file override it."""

    bg_cleanup: bool = True


@dataclass(frozen=True)
class Sync:
    """The note sync's switch, on for the machine file's bool `true` alone: read from this layer,
    never the session's resolution, where the overlay's files could move it."""

    enabled: bool = False


@dataclass(frozen=True)
class Owner:
    """The owner's resolved values, where each came from, and what could not be used."""

    personal: Personal
    guards: Guards
    # The session budgets the machine file set, by budget name: what the owner's layer adds to a
    # project's `[budgets]`, and nothing where the file says nothing.
    session_budgets: Mapping[str, int]
    provenance: Mapping[str, LayerName]
    notes: tuple[str, ...]
    # The preset the values stand on, and what the machine file set that this drybag can use,
    # as dotted keys: what `rebased` stands on another preset without reading the file again.
    preset: str = DEFAULT_PRESET
    # A factory, since Python 3.11 refuses an unhashable default on a dataclass field.
    layer: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    # `read_machine_toml`'s refusal of a machine file that does not load, which names the file and
    # the parser's position or the error's class and never the file's text; `None` for a file
    # that loads or is not there. Only a reader that is not `strict` gets an `Owner` with one.
    unloaded: str | None = None
    sync: Sync = Sync()


# A machine file that does not load. `error` is `read_machine_toml`'s refusal, which names the file
# and the parser's position or the error's class, and never the file's text.
MACHINE_FILE_NOTE = (
    "{error}, so the preset's personal values and guards stand in for the machine file's"
)
# What one table or key of a readable machine file got wrong, in the note that says what happened
# to the value.
UNKNOWN_KEY = "[{table}] has a key this drybag does not read: {key}"
GATE_BUDGET = (
    "budgets.{name} is a gate's budget, which only a repository's drybag.toml sets; the "
    "machine file sets only a session's"
)
NOT_A_TABLE = "[{table}] must be a table"
IGNORED_NOTE = "{path}: {problem}; it is ignored"
MACHINE_RESET_NOTE = "{path}: {problem}; the preset's value applies"


def resolve_owner(
    machine: Path | None,
    *,
    preset: str = DEFAULT_PRESET,
    strict: bool = False,
) -> Owner:
    """The owner's layer over the preset named `preset`.

    `strict` is a command's: a machine file that does not load raises `MachineConfigError`.
    Otherwise, a session's, it is read as no file, with `MACHINE_FILE_NOTE` and `Owner.unloaded`.
    In a file that loads, both read past what they cannot use alike, with a note.

    `preset` is a preset this build ships, and every caller holds it to that before it asks: the
    project loader hands the name its own `_preset` has already loaded, and a session the
    document's name only when it is shipped, `DEFAULT_PRESET` otherwise (`loader.preset_named`). The
    repository's value is the loader's to judge, so it is refused or reset once, in the loader's
    words. `machine` is the machine file; `None`, a machine with no home to hold one
    (`machine_config_path`), is read as no file, as an absent file is.
    """
    notes: list[str] = []
    unloaded: str | None = None
    try:
        raw = None if machine is None else read_machine_toml(machine)
    except MachineConfigError as exc:
        if strict:
            raise
        raw, unloaded = None, str(exc)
        notes.append(MACHINE_FILE_NOTE.format(error=exc))
    upper: dict[str, object] = {}
    if raw is not None and machine is not None:
        upper = _machine_values(raw, machine, notes)
    owner = _over(upper, preset, unloaded)
    return replace(owner, notes=(*notes, *owner.notes))


def rebased(owner: Owner, preset: str) -> Owner:
    """`owner` stood on the preset named `preset`: the machine file's values it holds over that
    preset's defaults, and its own notes, the file not read again.

    How a command that resolved the owner's layer once, before it read a document, loads a
    document naming another preset: the personal values a preset sets fall back to that preset's,
    as they would had the layer been resolved over it, and nothing is said twice. `preset` is one
    this build ships, as `resolve_owner`'s is.
    """
    if preset == owner.preset:
        return owner
    return replace(_over(owner.layer, preset, owner.unloaded), notes=owner.notes)


def _over(upper: Mapping[str, object], preset: str, unloaded: str | None) -> Owner:
    """The machine file's usable values `upper` over the preset named `preset`, with the notes
    `classes.resolve` gives them."""
    shipped = load_preset(preset)
    lower: dict[str, object] = {
        f"personal.{key}": value for key, value in defaults_of(shipped).get("personal", {}).items()
    }
    lower.update({f"guards.{item.name}": item.default for item in fields(Guards)})
    lower.update({f"sync.{item.name}": item.default for item in fields(Sync)})
    layers = [Layer(LayerName.PRESET, lower), Layer(LayerName.MACHINE_FILE, dict(upper))]
    resolution = resolve(layers)
    values = resolution.values
    personal = Personal(**_section(values, "personal"))
    guards = Guards(**_section(values, "guards"))
    # `_machine_values` admitted only a positive integer under `budgets.`, and the preset layer
    # carries none.
    session_budgets = {
        key.removeprefix("budgets."): cast(int, value)
        for key, value in values.items()
        if key.startswith("budgets.") and resolution.provenance[key] is LayerName.MACHINE_FILE
    }
    return Owner(
        personal=personal,
        guards=guards,
        session_budgets=MappingProxyType(session_budgets),
        provenance=resolution.provenance,
        notes=resolution.notes,
        preset=preset,
        layer=MappingProxyType(dict(upper)),
        unloaded=unloaded,
        sync=Sync(**_section(values, "sync")),
    )


def said_on_stderr(owner: Owner) -> Owner:
    """`owner`, after its notes are said on stderr, one line each: how a command tells its reader
    what it read past in the machine file, beside an answer on stdout that `--json` keeps whole.
    Called by the command layer, once a command (`command.command_owner`), and never by a load."""
    for note in owner.notes:
        print(f"drybag: {note}", file=sys.stderr)
    return owner


def _section(values: Mapping[str, object], section: str) -> dict[str, Any]:
    prefix = f"{section}."
    return {
        key.removeprefix(prefix): value for key, value in values.items() if key.startswith(prefix)
    }


def _machine_values(raw: dict[str, Any], machine: Path, notes: list[str]) -> dict[str, object]:
    """The machine file's `[personal]`, `[guards]`, session `[budgets]` and `[sync]` as dotted
    keys.

    Every other table is another reader's (`[overlay]`, `[machine]`) or nobody's, and is not this
    function's to judge. A problem is a note, and costs only its own key, or its own table when the
    table is not one. A value is held to its key's
    schema type by `coerced`, the project loader's rule, and kept as `coerced` gives it.
    """

    def problem(sentence: str, *, ignored: bool) -> None:
        template = IGNORED_NOTE if ignored else MACHINE_RESET_NOTE
        notes.append(template.format(path=machine, problem=sentence))

    found: dict[str, object] = {}
    # Each table this layer reads, with the schema class `reading.table_types` holds its keys to.
    tables: dict[str, type[Any]] = {
        "personal": Personal,
        "guards": Guards,
        "budgets": Budgets,
        "sync": Sync,
    }
    for table, cls in tables.items():
        if table not in raw:
            continue
        values = raw[table]
        if not isinstance(values, dict):
            problem(NOT_A_TABLE.format(table=table), ignored=False)
            continue
        types = table_types(cls)
        for key, value in values.items():
            if key not in types:
                problem(UNKNOWN_KEY.format(table=table, key=clipped(key)), ignored=True)
            elif table == "budgets" and key in GATE_BUDGETS:
                # Before `resolve`, which would refuse it too, so the note says which budgets the
                # owner does set rather than only that this layer may not.
                problem(GATE_BUDGET.format(name=key), ignored=True)
            else:
                try:
                    found[f"{table}.{key}"] = coerced(f"{table}.{key}", types[key], value)
                except ConfigError as exc:
                    problem(str(exc), ignored=False)
    return found
