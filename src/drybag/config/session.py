"""What a session runs on: the project's configuration, read the way a session must, and the owner's
layer.

A session never dies of its configuration. A command refuses a `drybag.toml` it cannot use whole,
because a person is about to act on it; a hook has no person to tell, and a `PreToolUse` that
refused because a file did not load would stop every tool call for something no guard decided. So
`session_config` answers for anything the repository or the machine file holds, and says what it
could not use in one warning, which `SessionStart` shows (`hooks.dispatch`):

- no `drybag.toml`: no project configuration, and nothing to say;
- one that is a symbolic link, is not UTF-8, cannot be read or is not TOML: no project
  configuration, and `FALLBACK_WARNING`;
- one longer than a session reads (`SESSION_READ_LIMIT`), read no further: no project
  configuration, and `TOO_LARGE_WARNING`;
- one whose key cannot be used and has nothing to stand in for it — a required key, or a section
  holding one, that the document got wrong or never wrote, or a preset value that does not hold in
  this repository — no project configuration, and `UNUSABLE_WARNING` naming the key;
- one that loads with notes (`loader.loads_noting`): the configuration, and `NEWER_RELEASE_WARNING`
  when its `[drybag] version` is later than the running one, `NOTED_WARNING` otherwise.

The owner's layer is resolved, not `strict`, whatever the project holds, so the guards run on the
preset and the machine file in every one of those cases, and a machine file that could not be used
whole adds `MACHINE_FILE_WARNING`. Every warning is a constant in drybag's own words: it
reaches the model, so the one value of the repository's it carries, a key, is printed through
`printed.printable`.

**One resolution builds the session's preferences.** The keys a layer above the repository may
set (`RESOLVED`: the personal values, the guards' switches, the preferences and the session
budgets) are resolved once, by `classes.resolve`, over the preset, what the repository's file
holds after the NOTE load, the overlay's common and project files (`config.overlay_layers`) and
the machine file, and `Config`'s values for them come from that resolution
(`SessionConfig.resolution`), as do the guards (`SessionConfig.guards`). Its notes are what the
overlay's files could not give and what `resolve` refused; no warning carries them, and
`doctor`'s `preferences` row names them.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from drybag import __version__
from drybag.config import overlay_layers
from drybag.config.classes import (
    CLASSES,
    SESSION_BUDGETS,
    KeyClass,
    Layer,
    LayerName,
    Resolution,
    resolve,
)
from drybag.config.loader import (
    MissingKeys,
    TooLargeDocument,
    UnusableKey,
    load,
    loads,
    loads_noting,
    preset_defaults,
    preset_named,
    read_document,
)
from drybag.config.machine import machine_config_path
from drybag.config.owner import Guards, Owner, rebased, resolve_owner
from drybag.config.paths import PathEscape
from drybag.config.reading import UNPARSEABLE, ConfigError, coerced, table_types
from drybag.config.schema import Budgets, Config, Drybag, Memory, Personal
from drybag.errors import resolved_or_none
from drybag.presets import defaults_of, load_preset
from drybag.printed import printable
from drybag.semver import later

# A named cap: the most bytes of `drybag.toml` a session reads. A hook has the ten seconds
# `hooks/hooks.json` gives each entry, and a `PreToolUse` hook that runs past them loses its guard,
# so what the parse and the NOTE load cost,
# each in proportion to the file, is bounded by the file before either starts: a 2.8 MB file kept a
# hook fourteen seconds. A minimal `drybag.toml` is a few hundred bytes, and one that
# spells out every setting is a few kilobytes; 256 KiB is still dozens of times the second. A
# command reads the file to `fsops.REGULAR_READ_LIMIT`.
SESSION_READ_LIMIT = 256 * 1024
# How every warning opens, and how the two that drop the project configuration close: `doctor`
# shows the words between (`cause`), since it is where that pointer leads.
PREFIX = "drybag: "
DOCTOR_DETAIL = "; run `drybag doctor` for the detail"
FALLBACK_WARNING = (
    f"{PREFIX}drybag.toml does not load — it is a symbolic link, is not UTF-8 text, cannot "
    "be read or is not TOML — so this session runs the guards on the preset and the machine "
    f"owner's layer, without the project's settings{DOCTOR_DETAIL}"
)
TOO_LARGE_WARNING = (
    f"{PREFIX}drybag.toml is larger than the {SESSION_READ_LIMIT:,} bytes a session reads, so "
    "this session runs the guards on the preset and the machine owner's layer, without the "
    f"project's settings{DOCTOR_DETAIL}"
)
UNUSABLE_WARNING = (
    f"{PREFIX}in drybag.toml, {{key}} cannot be used and nothing can stand in for it, so this "
    "session runs the guards on the preset and the machine owner's layer, without the project's "
    f"settings{DOCTOR_DETAIL}"
)
# What `UNUSABLE_WARNING` says for a key `printable` withholds, or for a refusal that named none.
UNNAMED_KEY = "a setting"
NOTED_WARNING = (
    "drybag: drybag.toml loaded with {count} setting(s) ignored or put back to the "
    "preset's; run `drybag doctor` for the list"
)
# The remedy is `doctor`'s `VERSION_AHEAD`'s: the way out of a file a later release wrote is a
# later plugin.
NEWER_RELEASE_WARNING = (
    "drybag: drybag.toml was written by a newer drybag, so {count} setting(s) this one "
    "does not read were ignored or put back to the preset's; update the drybag plugin, and run "
    "`drybag doctor` for the list"
)
# `path` is the machine file that was read, the owner's own path, never the repository's.
MACHINE_FILE_WARNING = (
    "drybag: the machine file, {path}, could not be used whole, so the preset's personal values "
    "and guards stand in for what it could not give; run `drybag doctor` for the detail"
)

# The keys a session resolves over every layer, each one a layer above the repository may set: the
# owner's personal values and guards' switches, the preferences, and the session budgets.
RESOLVED: tuple[str, ...] = (
    *(f"personal.{item.name}" for item in fields(Personal)),
    *(f"guards.{item.name}" for item in fields(Guards)),
    *sorted(
        key
        for key, kind in CLASSES.items()
        if kind is KeyClass.PREFERENCE and not key.endswith(".*")
    ),
    *(f"budgets.{name}" for name in sorted(SESSION_BUDGETS)),
)
# The schema class each section of `RESOLVED` holds its values to.
_SCHEMA: dict[str, type[Any]] = {
    "personal": Personal,
    "guards": Guards,
    "memory": Memory,
    "drybag": Drybag,
    "budgets": Budgets,
}


@dataclass(frozen=True)
class SessionConfig:
    """The project's configuration, or `None` where there is none to use; the owner's layer, which
    there always is; and the warnings `SessionStart` shows, at most one about each file: the
    project file's (`project_warnings`) and the machine file's (`machine_warnings`).

    `noted` is the dotted key of every setting the project's file had ignored or reset, in the
    order of its notes, for `doctor` to list by key alone: a key is text the document chose.
    `document` is the project's file as the NOTE load used it (`loader.Noted.document`), from
    which the resolution's repository layer is read. `resolution` is the one resolution of
    `RESOLVED` over every layer, whose provenance `doctor`'s `preferences` row prints."""

    config: Config | None
    owner: Owner
    project_warnings: tuple[str, ...] = ()
    machine_warnings: tuple[str, ...] = ()
    noted: tuple[str, ...] = ()
    document: Mapping[str, Any] = field(default_factory=dict)
    resolution: Resolution = field(default_factory=lambda: Resolution({}, {}, ()))
    bound: Config | None = None

    @property
    def acting(self) -> Config | None:
        """The configuration the delivery side acts on: the project's file, else the bound one."""
        return self.bound if self.config is None else self.config

    @property
    def warnings(self) -> tuple[str, ...]:
        """Every warning `SessionStart` shows: the project file's, then the machine file's."""
        return (*self.project_warnings, *self.machine_warnings)

    @property
    def guards(self) -> Guards:
        """The guards' switches the resolution settled, each on where no layer set it."""
        return Guards(**_section(self.resolution.values, "guards"))


def session_config(root: Path | None, *, machine: Path | None = None) -> SessionConfig:
    """The configuration a session runs on in `root`, which never raises for anything the
    repository or the machine file holds.

    `machine` is the machine file; `None` is the one `config.machine` names, as `loader.load`
    reads it. `root` is `None` for a hook with no project root, which has
    no project configuration.
    """
    path = machine if machine is not None else machine_config_path()
    overlays, notes, project = overlay_layers.read(root, machine=path)
    read = _read(root, path)
    resolved = _resolved(read, overlays, notes)
    if project is None or read.config is not None or read.project_warnings:
        return resolved
    # No file, and this machine binds the checkout: `bound_config`'s answer, from the layers and
    # the project already read.
    return replace(
        resolved,
        bound=resolved_or_none(
            lambda: (
                _resolved(
                    SessionConfig(preset_defaults(project), read.owner), overlays, notes
                ).config
            )
        ),
    )


def command_config(
    root: Path,
    *,
    machine: Path | None,
    owner: Owner | None = None,
    unnamed: Callable[[], str] | None = None,
) -> Config:
    """The `Config` a command acts on: `loader.loads`', which refuses what it cannot use, with the
    keys `RESOLVED` names taken from the resolution `session_config` builds, overlay layers and
    all, so a command acts on what a session runs on; `doctor` names the layers' notes.

    With no `drybag.toml` it is `load`'s refusal, unless the caller can name the project
    itself (`unnamed`, asked only then: `attach`'s choice): the configuration is then the preset's
    (`loader.preset_defaults`) under the same resolution."""
    text = read_document(root)
    path = machine if machine is not None else machine_config_path()
    if text is not None:
        config = loads(text, root, machine=machine, owner=owner)
        raw = tomllib.loads(text)
    elif unnamed is None:
        return load(root, machine=machine, owner=owner)
    else:
        config, raw = preset_defaults(unnamed()), {}
    preset = preset_named(raw)
    held = resolve_owner(path, preset=preset) if owner is None else rebased(owner, preset)
    overlays, notes, _ = overlay_layers.read(root, machine=path)
    resolved = _resolved(SessionConfig(config, held, document=raw), overlays, notes).config
    return config if resolved is None else resolved


def bound_config(root: Path, *, machine: Path | None, owner: Owner | None) -> Config | None:
    """The `Config` a command acts on in `root`, for a checkout with or without a
    `drybag.toml`: `command_config`'s, as it is, where the file is there; where it is not and
    this machine binds `root`'s repository, the preset's under the overlay's layers for the
    project the binding record names (`command_config`'s `unnamed`); `None` otherwise.

    The anchor is the binding record (the id in the git common directory and the record under the
    data directory), which a clone carries neither of, so a clone with no file gains a
    configuration only on a machine its owner bound it on. `session_config` is not this: the
    hooks of the methodology side answer nothing where the checkout has no file. Raises what
    `command_config` raises, and `Failure` where git cannot be asked for the binding."""
    # Deferred: `binding` reads its data directory and its record through `config`, so a
    # module-level import would make the two packages import each other.
    from drybag.binding import bound_project

    if read_document(root) is not None:
        return command_config(root, machine=machine, owner=owner)
    bound = bound_project(root)
    if bound is None:
        return None
    return command_config(root, machine=machine, owner=owner, unnamed=lambda: bound.project)


def _read(root: Path | None, path: Path | None) -> SessionConfig:
    """The project's configuration and the owner's layer, before the resolution."""
    try:
        text = None if root is None else read_document(root, limit=SESSION_READ_LIMIT)
    except TooLargeDocument:
        return _fallback(path, TOO_LARGE_WARNING)
    except (ConfigError, PathEscape):
        # `read_document` refuses a symlink through `contained()` before it follows it, so a link
        # to a loadable file and one to `/dev/zero` meet the same rule.
        return _fallback(path, FALLBACK_WARNING)
    if root is None or text is None:
        # Nothing of the project's to read, and the machine file is read all the same: a broken one
        # is named here as beside any project file.
        owner = resolve_owner(path)
        return SessionConfig(None, owner, machine_warnings=_owners(owner, path))
    try:
        raw = tomllib.loads(text)
    except UNPARSEABLE:
        return _fallback(path, FALLBACK_WARNING)
    owner = resolve_owner(path, preset=preset_named(raw))
    try:
        # Parsed once, above: the NOTE load reads the same document rather than parsing it again.
        noted = loads_noting(text, root, owner=owner, parsed=raw)
    except UnusableKey as exc:
        return _with_owner(owner, path, _unusable(exc.key))
    except MissingKeys as exc:
        return _with_owner(owner, path, _unusable(exc.missing[0]))
    except (ConfigError, PathEscape) as exc:
        # Every refusal `loads_noting` raises past the two above names a key or is the document
        # not parsing, which `tomllib` already answered; this arm is for one it has not, so that a
        # session still goes on.
        return _with_owner(owner, path, _unusable(exc.key))
    warnings: tuple[str, ...] = ()
    if noted.notes:
        newer = later(noted.config.drybag.version, __version__) is True
        template = NEWER_RELEASE_WARNING if newer else NOTED_WARNING
        warnings = (template.format(count=len(noted.notes)),)
    return SessionConfig(
        noted.config, owner, warnings, _owners(owner, path), noted.keys, noted.document
    )


def _resolved(
    read: SessionConfig, overlays: Sequence[Layer], notes: Sequence[str]
) -> SessionConfig:
    """`read` with `RESOLVED` resolved over the preset, the repository, `overlays` and the machine
    file, and its configuration's values for those keys taken from the resolution."""
    owner = read.owner
    stack = (
        Layer(LayerName.PRESET, _preset_values(owner.preset)),
        Layer(LayerName.REPOSITORY, _held(read.document)),
        *overlays,
        Layer(LayerName.MACHINE_FILE, dict(owner.layer)),
    )
    resolved = resolve(stack)
    resolution = replace(resolved, notes=(*notes, *resolved.notes))
    config = None if read.config is None else _over(read.config, resolution.values)
    return replace(read, config=config, resolution=resolution)


def _typed(key: str, value: object) -> object:
    section, _, name = key.partition(".")
    return coerced(key, table_types(_SCHEMA[section])[name], value)


def _preset_values(preset: str) -> dict[str, object]:
    """The preset's value of each key of `RESOLVED` it carries, and each guard's default. The
    preset sets no session budget: one is configured only where a layer above it sets it."""
    defaults = defaults_of(load_preset(preset))
    values: dict[str, object] = {f"guards.{item.name}": item.default for item in fields(Guards)}
    for key in RESOLVED:
        section, _, name = key.partition(".")
        table = defaults.get(section)
        if isinstance(table, dict) and name in table:
            values[key] = _typed(key, table[name])
    return values


def _held(document: Mapping[str, Any]) -> dict[str, object]:
    """Each key of `RESOLVED` the repository's file holds after the NOTE load, whose build already
    held it to its type."""
    values: dict[str, object] = {}
    for key in RESOLVED:
        section, _, name = key.partition(".")
        table = document.get(section)
        if isinstance(table, dict) and name in table:
            values[key] = _typed(key, table[name])
    return values


def _section(values: Mapping[str, object], section: str) -> dict[str, Any]:
    prefix = f"{section}."
    return {
        key.removeprefix(prefix): value for key, value in values.items() if key.startswith(prefix)
    }


def _over(config: Config, values: Mapping[str, object]) -> Config:
    """`config` with the resolution's `values` for its preferences, personal values and session
    budgets. `memory.groups` keeps each group once, as the loader keeps the repository's
    (`loader._deduplicated`)."""
    memory = _section(values, "memory")
    if "groups" in memory:
        memory["groups"] = tuple(dict.fromkeys(memory["groups"]))
    budgets = config.budgets
    return replace(
        config,
        memory=replace(config.memory, **memory),
        drybag=replace(config.drybag, **_section(values, "drybag")),
        personal=replace(config.personal, **_section(values, "personal")),
        budgets=replace(budgets, configured={**budgets.configured, **_section(values, "budgets")}),
    )


def cause(warning: str) -> str:
    """`warning` without its opening and without the pointer at `doctor`: what `doctor` says of
    the same state, where pointing at itself would say nothing."""
    return warning.removeprefix(PREFIX).removesuffix(DOCTOR_DETAIL)


def _unusable(key: str | None) -> str:
    return UNUSABLE_WARNING.format(key=printable(key, UNNAMED_KEY) if key else UNNAMED_KEY)


def _owners(owner: Owner, machine: Path | None) -> tuple[str, ...]:
    # The owner's layer has notes only for a machine file it read, so `machine` is a path here.
    return (MACHINE_FILE_WARNING.format(path=machine),) if owner.notes else ()


def _with_owner(owner: Owner, machine: Path | None, warning: str) -> SessionConfig:
    return SessionConfig(None, owner, (warning,), _owners(owner, machine))


def _fallback(machine: Path | None, warning: str) -> SessionConfig:
    return _with_owner(resolve_owner(machine), machine, warning)
