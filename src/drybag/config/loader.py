"""Read drybag.toml, merge it under the preset, validate it.

The machine owner's layer is merged too (`config.owner`), so every command runs on the personal
values and session budgets the machine file sets, as a session does; a session resolves the
overlay's two files between the repository's and the machine file's over what this builds
(`config.session`, `config.overlay_layers`). The owner's layer is handed in, read once by the
command, which says on stderr what it read past (`command.command_owner`), or by a caller that
must go on (`config.session`); or, when the caller hands none in, read here, `strict`, refusing a
machine file that does not load and saying nothing of what it read past in one that does.
`loads` refuses what it cannot use; `loads_noting` reads what it can and says what it ignored or
reset. What the two loaders share is `config.reading`'s.
"""

from __future__ import annotations

import tomllib
from collections.abc import Collection, Mapping
from dataclasses import MISSING, Field, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, TypeVar, cast

from drybag import __version__
from drybag.config.classes import (
    CLASSES,
    REFUSED_LAYER_NOTE,
    KeyClass,
    Layer,
    LayerName,
    resolve,
)
from drybag.config.machine import machine_config_path
from drybag.config.owner import Guards, Owner, rebased, resolve_owner
from drybag.config.paths import PathEscape, contained, validate_path_values, validate_paths
from drybag.config.reading import (
    NOT_UTF8,
    UNPARSEABLE,
    UNREADABLE,
    ConfigError,
    coerced,
    positive,
    schema_types,
    table_types,
    toml_position,
)
from drybag.config.schema import (
    BRANCH_NAME,
    BRANCH_RULE,
    MEMORY_MODES,
    NAME_RULE,
    PROJECT_NAME,
    SECTION_NAME,
    Budgets,
    CommitMessages,
    Config,
    Drybag,
    Memory,
    NativeCaps,
    Paths,
    Personal,
    Project,
)
from drybag.errors import Failure
from drybag.findings import LISTED_LIMIT
from drybag.fsops import TooLarge, read_regular_text, said
from drybag.presets import (
    DEFAULT_PRESET,
    available,
    defaults_of,
    load_preset,
    shipped_presets,
)
from drybag.printed import clipped, quoted

CONFIG_FILE = "drybag.toml"
SECTIONS = (
    "drybag",
    "project",
    "paths",
    "memory",
    "budgets",
    "commit_messages",
    "guards",
)
# The machine owner's tables, which a repository's `drybag.toml` cannot set: refused outright
# by `loads` as unknown sections, and refused key by key by `classes.resolve` under NOTE. Read off
# `classes.CLASSES`, whose section wildcards of the machine-only class they are, so the two never
# name different tables. `[guards]` is not one: a repository sets the guards' default, which the
# owner's layers override (`config.session`), so it is a section `Config` does not carry, read here
# only to be held to its types.
MACHINE_ONLY_SECTIONS = frozenset(
    key.removesuffix(".*")
    for key, cls in CLASSES.items()
    if key.endswith(".*") and cls is KeyClass.MACHINE_ONLY
)
PRESET_KEY = "drybag.preset"
# A preset value that is not a string, which no name of a preset can be.
NOT_A_PRESET_NAME = "drybag.preset must be a string; {available}"
# What the NOTE path says. A name is printed through `printed.clipped`, since a TOML key is any
# quoted text; `reason` is the refusal `loads` would have raised, which names its key. The one
# such refusal that quotes a value the document wrote is `contained()`'s, through `repr`, for a
# `[paths]` value `validate_paths` has already held to `grammar.PATH_VALUE`: ASCII letters,
# digits, `.`, `_` and `-` in `/`-separated segments.
UNKNOWN_NOTE = "{name} is not a setting this drybag reads; it is ignored"
RESET_NOTE = "{key} is left out ({reason}); the preset's value applies"
# `UnusableKey`'s two sentences, in the same words as the notes: a key, or a section holding one,
# that has no default, and a key whose preset value fails too. `reason` is again the refusal
# `loads` would have raised for that key, or, for a preset value, the one that value got.
UNUSABLE_REQUIRED = "{key} cannot be used ({reason}), and it has no default to fall back to"
UNUSABLE_PRESET = (
    "{key} falls back to the preset's value, which does not hold in this repository either "
    "({reason})"
)


T = TypeVar("T")


class MissingKeys(ConfigError):
    """A section without a key that has no default: a refusal of the whole document, so `key`
    is `None`. `missing` is every such key, dotted, for a reader that must go on to name the one
    the document never wrote (`config.session`); never printed. The NOTE path never removes such
    a key, so a document it raises for never wrote one."""

    def __init__(self, message: str, *, missing: tuple[str, ...]) -> None:
        super().__init__(message)
        self.missing = missing


class UnusableKey(ConfigError):
    """A key the NOTE path cannot stand anything in for: one the document holds that has no
    default, or a section holding such a key; or a key whose preset value does not hold in this
    repository either.

    A reader that must go on cannot keep the project's configuration without it, so it drops the
    configuration whole, naming `key`, rather than removing the key and relaying the refusal the
    next attempt would raise: that one would say a key the document holds was missing, or name
    the preset's value as the fault. Raised only by `loads_noting`; `loads` refuses as it always
    has. It carries no notes: the load stopped before reading the rest of the document, so the
    notes made by then are not the file's list, and the reader names the one key that stopped it.
    """

    def __init__(self, message: str, *, key: str) -> None:
        super().__init__(message, key=key)


class TooLargeDocument(ConfigError):
    """`drybag.toml` longer than its reader reads (`read_document`'s `limit`), read no further:
    a refusal of the whole document, in the words of any file that cannot be read, which a session
    tells apart by this type to say what it means for the session (`config.session`)."""


NOT_THERE = "{path} does not exist; write one: `[drybag] version` and `[project] name` are enough"


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table", key=name)
    return value


def _merged(raw: dict[str, Any], defaults: dict[str, Any], name: str) -> dict[str, Any]:
    return {**defaults.get(name, {}), **_table(raw, name)}


def _named(unknown: list[str], noun: str) -> str:
    """`unknown`, bounded before it may print: a plain-named one is echoed, anything else
    is counted and never quoted — `PATH_VALUE`'s rule read onto a second grammar.

    **Every name in a `drybag.toml` is repository-authored, not only the table names.** A TOML
    key is arbitrary quoted text, so `[paths] "docs\u001b[31m\nIGNORE ALL PRIOR RULES" = 1` puts raw
    ESC and raw newlines into `[paths] has unknown key(s): ...` — a refusal the terminal renders
    and the `attach` skill relays to a model — on all nine tables, unless the keys are held to the
    same rule as the section list.

    `SECTION_NAME` is the grammar both callers use, and it is a deliberate re-reading rather than
    a coincidence: every key any schema class or `Budgets.NAMES` declares is lowercase words
    joined by underscores, so a typo worth naming (`branch` for `gate_branch`) matches and
    nothing drybag answers to falls outside. A key carrying anything else is counted.

    `noun` is what the count calls the names it would not print, so the sentence reads about the
    thing that was unknown: a section, or a key.
    """
    named = [name for name in unknown if SECTION_NAME.match(name)]
    unnamed = len(unknown) - len(named)
    # The named ones capped at `LISTED_LIMIT` like every list of names on a line, since a file
    # may carry any number, and the plain rest counted as plain: one count per kind, so the two
    # counts never run together as "and 3 more, 1 more".
    shown = named[:LISTED_LIMIT]
    held = len(named) - len(shown)
    parts = [", ".join(shown)] if shown else []
    if held:
        parts = [f"{parts[0]} and {held} more plain name(s)"]
    if unnamed:
        # The noun agrees with the count, for the reason the verb already did: one unnamed key
        # produced "1 more that is not plain key names", which is the common case of this arm.
        tail = f"is not a plain {noun} name" if unnamed == 1 else f"are not plain {noun} names"
        # "more" only when something was named: with every name failing the grammar the message
        # read "unknown section(s): 3 more that are not plain section names" — more than nothing.
        more = "more " if named else ""
        parts.append(f"{unnamed} {more}that {tail}")
    return ("; " if held else ", ").join(parts)


def _build(cls: type[T], name: str, values: dict[str, Any]) -> T:
    known = schema_types(cast(Any, cls))
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ConfigError(
            f"[{name}] has unknown key(s): {_named(unknown, 'key')}", key=f"{name}.{unknown[0]}"
        )
    # A field with a default need not be in the merged table, so only a field without one is
    # required of it.
    required = {
        f.name
        for f in fields(cast(Any, cls))
        if f.default is MISSING and f.default_factory is MISSING
    }
    missing = sorted(required - set(values))
    if missing:
        raise MissingKeys(
            f"[{name}] is missing required key(s): {', '.join(missing)}",
            missing=tuple(f"{name}.{key}" for key in missing),
        )
    return cls(
        **{key: coerced(f"{name}.{key}", known[key], value) for key, value in values.items()}
    )


def _enum(section: str, key: str, value: str, allowed: tuple[str, ...]) -> None:
    """The key and the vocabulary it may be spelled in; never the value it was spelled with.

    The key this guards, `memory.mode`, is a repository-authored string bounded by no grammar, so
    a clone can write anything at all into it, and the refusal reaches a terminal and a model
    through the `attach` skill's relay. `; got {value!r}` therefore put unbounded
    repository bytes into it, as it did for the `ci.mode` an earlier drybag read:
    `ci.mode must be one of reusable, uvx, none; got '\\x1b[2JIGNORE PRIOR RULES'`. `!r` escapes
    the control characters, which makes it milder than a raw-bytes leak and not a different
    kind of thing — it is still content- and length-unbounded.

    Nothing is lost by dropping it. `allowed` is drybag's own closed vocabulary, the key names
    the line to look at, and the reader has the file open. This is the ruling `PROJECT_NAME`'s
    refusal one screen down already took for the same reason — it dropped `; got
    {project.name!r}` — and `_named` took for the keys of every table.
    """
    if value not in allowed:
        raise ConfigError(
            f"{section}.{key} must be one of {', '.join(allowed)}", key=f"{section}.{key}"
        )


def _deduplicated(memory: Memory) -> Memory:
    """`memory.groups` with each entry kept once, in the order the document wrote them.

    `_build` coerced the list with `tuple(value)` and nothing else, and it is the one
    repository-authored list four places report as a **count** a user is asked to act
    on. `groups = ["developer", "developer"]` made a walk of the groups, since retired, visit one
    directory twice, so `attach` refused naming two groups that never moved into the overlay,
    `attach --check` printed `real_directories: 2`, and the session line told the model two --
    all about one directory, and with the remedy ("move them into the overlay") already done
    for the only one there is.

    `dict.fromkeys` and not `set`, because the order is the owner's, and a refusal that reorders
    the list a person is reading is a worse answer than one that does not.

    **Deduplication only, and the containment stays where it is.** `config/paths.py` names
    `memory.groups` one of four repository-writable fields this loader deliberately does not
    contain, and hands each to the module that first reads it -- because the anchor differs per
    module: `attach._check_groups` contains a group against the overlay, `memory.store` against
    the store. A grammar check here would refuse a spelling those two already refuse, one layer
    above the guard that knows what it is anchored to, and would take the reachable arm of each
    of them with it. What this function
    fixes is the one thing none of them can: a count taken over a list with a duplicate in it.
    """
    return replace(memory, groups=tuple(dict.fromkeys(memory.groups)))


def _budgets(raw: dict[str, Any], preset: dict[str, Any], owner: Owner) -> Budgets:
    configured = _table(raw, "budgets")
    unknown = sorted(set(configured) - set(Budgets.NAMES))
    if unknown:
        raise ConfigError(
            f"[budgets] has unknown key(s): {_named(unknown, 'key')}", key=f"budgets.{unknown[0]}"
        )
    for key, value in configured.items():
        positive(f"budgets.{key}", value)
    # The owner's session budgets, which `config.owner` admitted only for the session's names,
    # are configured values that win over the document's.
    session = owner.session_budgets
    return Budgets(preset=dict(preset["budgets"]), configured={**configured, **session})


def load(root: Path, *, machine: Path | None = None, owner: Owner | None = None) -> Config:
    """Read `drybag.toml` under the preset, and the owner's layer out of the machine file.

    With no `machine`, the machine file is `config.machine.machine_config_path`'s, the one file
    the overlay root's reader and `trust.record.record_path` read too, under `HOME` alone: an
    interactive run with `XDG_CONFIG_HOME` set once read `[personal]` from the file it named and
    `[overlay] root` and `trust.json` from `~/.config/drybag/`, so an owner who wrote one file
    with both tables got `[personal]` honoured and the overlay silently unrecorded. `--machine`
    is the supported way to name another file, honoured by all three readers, because it is a
    path a person typed rather than one an environment chose.

    **One reader of the machine file.** The owner's layer is `config.owner.resolve_owner`'s, read
    `strict` over the document's preset, as a session reads it without (`config.session`): the
    same `[personal]`, `[guards]` and session `[budgets]`, so a command measures what a session
    runs on. It refuses a machine file that does not load, and reads past a key it cannot use, as
    a session does. A command hands in the layer it resolved and said once (`loads`' `owner`).

    **One reader of the file.** Through `read_document`, which goes through `contained()`: a
    `drybag.toml` that is a symlink is refused by every command. Read with a plain `read_text`, a
    clone's link to `/dev/zero` would keep `plan check` reading until the machine ran out of memory.
    """
    text = read_document(root)
    if text is None:
        raise ConfigError(NOT_THERE.format(path=root / CONFIG_FILE))
    return loads(text, root, machine=machine, owner=owner)


def read_document(root: Path, *, limit: int | None = None) -> str | None:
    """`drybag.toml` exactly as it is on disk, or `None` when there is none.

    `newline=""` and not `read_text`: the parser judges the file's own text, so a lone `\\r`, which
    TOML refuses, is not translated into a line break that would load here and nowhere else.
    `contained` first, as for every configured path, so a symlinked `drybag.toml` is a refusal;
    then read to the read cap, a regular file only (`fsops.read_regular_text`), as every reader of a
    committed file reads one: `limit`, or `fsops.REGULAR_READ_LIMIT` where the caller names none.
    """
    try:
        return read_regular_text(contained(root, CONFIG_FILE), newline="", limit=limit)
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        raise ConfigError(NOT_UTF8.format(path=CONFIG_FILE)) from None
    except TooLarge as exc:
        raise TooLargeDocument(UNREADABLE.format(path=CONFIG_FILE, error=said(exc))) from None
    except OSError as exc:
        raise ConfigError(UNREADABLE.format(path=CONFIG_FILE, error=said(exc))) from None


def loads(
    text: str,
    root: Path,
    *,
    machine: Path | None = None,
    owner: Owner | None = None,
) -> Config:
    """Build a `Config` from `text` as `drybag.toml`'s contents, without reading a file.

    `load` is "read the file, then `loads`"; a session has the text already, so the
    parse-and-validate half is this function on its own.
    `owner` is the machine owner's layer (`config.owner.resolve_owner`), read once by the caller
    and stood on the document's preset (`config.owner.rebased`): its `personal` stands in for the
    machine file's, and its session budgets are folded into `[budgets]` as configured values that
    win over the document's; a command resolves it once, saying its notes there
    (`command.command_owner`), and hands it to every load it makes, so a note is said once a command
    and never once a document. Every argument after `root` is keyword-only.
    """
    path = _document(root)
    raw = _parsed(text, path)
    unknown = sorted(set(raw) - set(SECTIONS))
    if unknown:
        raise ConfigError(f"{path} has unknown section(s): {_named(unknown, 'section')}")
    return _built(raw, root, machine=machine, owner=owner)


def _document(root: Path) -> str:
    """What a refusal calls the document: the file at `root` through `printed.quoted`, since the
    root's name is the repository's, and a line break in it would start a line of its own."""
    return quoted(str(root / CONFIG_FILE))


def _parsed(text: str, path: str) -> dict[str, Any]:
    """`text` as TOML, or a refusal of the whole document naming only where the parser stopped."""
    try:
        return tomllib.loads(text)
    except UNPARSEABLE as exc:
        raise ConfigError(f"{path} is not valid TOML {toml_position(exc)}") from None


def _preset(name: object) -> dict[str, Any]:
    """The preset `[drybag] preset` names, or a refusal keyed to `drybag.preset`.

    `load_preset` holds the name to `shipped_presets()` before it reads anything, and refuses
    with a plain `Failure` carrying no key, which the NOTE path could only propagate: a
    `drybag.toml` naming a preset a later release ships would cost a session the whole file.
    So its refusal, and nothing it read, is re-raised keyed. The words are still the preset
    loader's, which never quote the name.
    """
    if not isinstance(name, str):
        raise ConfigError(NOT_A_PRESET_NAME.format(available=available()), key=PRESET_KEY)
    try:
        return load_preset(name)
    except Failure as exc:
        raise ConfigError(str(exc), key=PRESET_KEY) from None


def _built(
    raw: dict[str, Any],
    root: Path,
    *,
    machine: Path | None,
    owner: Owner | None,
) -> Config:
    """The `Config` a parsed document holds, every section read and held under the preset."""
    head = _table(raw, "drybag")
    # Asked before anything spells it: a hex literal of any length converts, and its `str`
    # raises past 4,300 digits.
    preset_name = head.get("preset", DEFAULT_PRESET)
    preset = _preset(preset_name)
    defaults = defaults_of(preset)
    defaults["drybag"] = {**defaults.get("drybag", {}), "preset": preset_name}

    drybag = _build(Drybag, "drybag", _merged(raw, defaults, "drybag"))
    project = _build(Project, "project", _merged(raw, defaults, "project"))
    if not PROJECT_NAME.match(project.name):
        raise ConfigError(f"project.name must be {NAME_RULE}", key="project.name")
    # Named, never quoted: the value becomes a git ref and reaches output lines, and it is the text
    # the grammar refused; git could not have such a branch anyway.
    if not BRANCH_NAME.match(project.base_branch):
        raise ConfigError(
            f"project.base_branch is not a plain branch name: {BRANCH_RULE}",
            key="project.base_branch",
        )
    paths = _build(Paths, "paths", _merged(raw, defaults, "paths"))
    memory = _deduplicated(_build(Memory, "memory", _merged(raw, defaults, "memory")))
    _enum("memory", "mode", memory.mode, MEMORY_MODES)
    commit_messages = _build(
        CommitMessages, "commit_messages", _merged(raw, defaults, "commit_messages")
    )
    caps = _build(NativeCaps, "native_caps", dict(preset["native_caps"]))
    # Held to its types and dropped: a session reads the repository's `[guards]` from the document
    # the build held (`config.session`), and no command reads a guard.
    _build(Guards, "guards", _table(raw, "guards"))
    # The owner's layer a session folds in: one reader of the machine file for every load, so a
    # command and a session never run on two sets of budgets. Handed in, it is stood on this
    # document's preset; otherwise read here, `strict`, which refuses only a file that does not
    # load, and said nowhere: saying it is the command's, once (`command.command_owner`).
    owner = (
        resolve_owner(machine or machine_config_path(), preset=preset_name, strict=True)
        if owner is None
        else rebased(owner, preset_name)
    )
    config = Config(
        drybag=drybag,
        project=project,
        paths=paths,
        memory=memory,
        budgets=_budgets(raw, preset, owner),
        native_caps=caps,
        commit_messages=commit_messages,
        personal=owner.personal,
    )
    validate_paths(config, root)
    return config


@dataclass(frozen=True)
class Noted:
    """What `loads_noting` read, a note for every name it ignored or value it reset, and the
    dotted key each note is about, in the same order. A key is any text the document chose: a
    reader prints one through `drybag.printed`. `document` is the parsed document the config
    was built from: every name it ignored and every key it reset removed."""

    config: Config
    notes: tuple[str, ...]
    keys: tuple[str, ...] = ()
    document: Mapping[str, Any] = field(default_factory=dict)


def loads_noting(
    text: str,
    root: Path,
    *,
    owner: Owner,
    parsed: dict[str, Any] | None = None,
) -> Noted:
    """`loads`, noting rather than refusing: read what the document holds that this drybag can
    use.

    For a reader that must go on, a session above all, where one name a newer release added or
    one value nobody can use would otherwise cost every setting the project made. An unknown
    section or key is removed with `UNKNOWN_NOTE`; the repository's `[personal]` goes through
    `classes.resolve`, which refuses the repository layer for every key in it, and is removed with
    its note. Then the document is built as `loads` builds it, and a refusal carrying
    a `key` — a `ConfigError`, or `validate_paths`' `PathEscape` — is decided before anything is
    removed, from the document and the preset alone:

    - a key the document holds that has a value to fall back to (`_stands_in`) — a preset value
      that holds in this repository, a schema default, a value the loader fills, or an entry that
      may simply be absent — is removed with `RESET_NOTE`, and the build is tried again;
    - a key the document does not hold, whose value was therefore the preset's, raises
      `UnusableKey` with `UNUSABLE_PRESET`;
    - a key the document holds with nothing to fall back to — a required key, a section holding
      one, a preset value that does not hold here either — raises `UnusableKey` at once.

    Nothing is removed that would leave the next attempt a required key to call missing, so no
    refusal is ever read against an earlier removal. Each retry removes one key the document
    holds and builds the whole document again, so there are at most as many retries as the
    schema has keys: `_known_only` removed every other name first. A refusal with no key is the
    whole document's and propagates, as a document that is not TOML does; a required key the
    document never wrote is one (`MissingKeys`). The owner's layer is folded in as `loads` folds it.
    `parsed` is `text` as a caller already parsed it, which is then not parsed again.
    """
    path = _document(root)
    raw = _parsed(text, path) if parsed is None else parsed
    notes: list[str] = []
    keys: list[str] = []
    document = _known_only(raw, notes, keys)
    while True:
        try:
            config = _built(document, root, machine=None, owner=owner)
        except (ConfigError, PathEscape) as exc:
            if exc.key is None:
                raise
            held = _holding(document, exc.key)
            if held is None:
                message = UNUSABLE_PRESET.format(key=clipped(exc.key), reason=exc)
                raise UnusableKey(message, key=exc.key) from None
            unusable = _without_fallback(exc.key, exc, document, root)
            if unusable is not None:
                raise UnusableKey(unusable, key=exc.key) from None
            table, name = held
            del table[name]
            notes.append(RESET_NOTE.format(key=clipped(exc.key), reason=exc))
            keys.append(exc.key)
            continue
        return Noted(config, tuple(notes), tuple(keys), document)


def _without_fallback(
    key: str, refused: Exception, document: dict[str, Any], root: Path
) -> str | None:
    """Why nothing can stand in for `key`, which the document holds and `refused` refused; `None`
    when leaving it out leaves a value to build with. Asked before the key is removed."""
    defaults = defaults_of(load_preset(preset_named(document)))
    if not _stands_in(key, defaults):
        return UNUSABLE_REQUIRED.format(key=clipped(key), reason=refused)
    section, _, name = key.partition(".")
    if section == "paths" and name:
        # The preset's value is a path in this repository, which a committed symlink can break as
        # it breaks the document's own: asked here, so a reset never lands on a value that fails.
        try:
            validate_path_values({name: defaults["paths"][name]}, root)
        except PathEscape as fault:
            return UNUSABLE_PRESET.format(key=clipped(key), reason=fault)
    return None


def preset_named(document: Mapping[str, object]) -> str:
    """The preset the parsed `document` builds on: the one its `[drybag] preset` names when this
    build ships it, and `DEFAULT_PRESET` otherwise, which is what a NOTE load resets an unshipped
    name to and what leaving the key out builds on. Whether the document's name is usable is the
    load's to say, in one note: this answers only what the defaults come from meanwhile, for the
    owner's layer a session resolves (`config.session`) and for a key the load resets."""
    head = document.get("drybag")
    named = head.get("preset", DEFAULT_PRESET) if isinstance(head, dict) else DEFAULT_PRESET
    return named if isinstance(named, str) and named in shipped_presets() else DEFAULT_PRESET


# The keys the loader fills itself when the document leaves them out, where the preset's
# `[defaults]` holds no value: the preset's own name (`DEFAULT_PRESET`).
_FILLED = frozenset({PRESET_KEY})


def _stands_in(dotted: str, defaults: dict[str, Any]) -> bool:
    """Whether a document without the key `dotted` names still builds a value for it.

    A budget may simply be absent. A key of a schema section has a value when
    it has a schema default, a preset value in `defaults` (the preset's `[defaults]`), or one the
    loader fills (`_FILLED`); a section that is not a table has one when every key it holds does.
    A name no schema declares is answered no: `_known_only` removed every such name the document
    wrote before the first build, so one here is a name nothing has vouched for.
    """
    section, _, name = dotted.partition(".")
    if section == "budgets":
        return True
    schema = _SCHEMA_OF.get(section)
    if schema is None:
        return False
    if not name:
        return all(_defaulted(section, key, defaults) for key in fields(schema))
    declared = {key.name: key for key in fields(schema)}
    return name in declared and _defaulted(section, declared[name], defaults)


def _defaulted(section: str, key: Field[Any], defaults: dict[str, Any]) -> bool:
    """Whether `section`'s field `key` has a value when the document gives it none."""
    return (
        key.default is not MISSING
        or key.name in defaults.get(section, {})
        or f"{section}.{key.name}" in _FILLED
    )


_SCHEMA_OF: dict[str, type[Any]] = {
    # Not a table of fields like the rest: `reading.table_types` reads a budget table's keys from
    # `Budgets.NAMES`, and `_stands_in` answers a budget before it asks this map.
    "budgets": Budgets,
    "drybag": Drybag,
    "project": Project,
    "paths": Paths,
    "memory": Memory,
    "commit_messages": CommitMessages,
    "guards": Guards,
}


def _keys_of(section: str) -> Collection[str]:
    """The keys this drybag reads in `section`: `Budgets.NAMES`, or the schema class's fields."""
    return table_types(_SCHEMA_OF[section]).keys()


def _known_only(raw: dict[str, Any], notes: list[str], keys: list[str]) -> dict[str, Any]:
    """`raw` without the names this drybag does not read, noting each one it removed and
    recording the key each note is about."""
    document: dict[str, Any] = {}
    machine_only: dict[str, object] = {}
    for name, value in raw.items():
        if name in MACHINE_ONLY_SECTIONS:
            if isinstance(value, dict) and value:
                machine_only.update({f"{name}.{key}": item for key, item in value.items()})
            else:
                notes.append(REFUSED_LAYER_NOTE.format(key=name, layer=LayerName.REPOSITORY))
                keys.append(name)
        elif name not in SECTIONS:
            notes.append(UNKNOWN_NOTE.format(name=clipped(name)))
            keys.append(name)
        elif isinstance(value, dict):
            document[name] = _known_keys(name, value, _keys_of(name), notes, keys)
        else:
            # Not a table: the section's own reader refuses it, keyed to the section.
            document[name] = value
    notes.extend(resolve([Layer(LayerName.REPOSITORY, machine_only)]).notes)
    # One note per key above: no machine-only key's class admits the repository layer.
    keys.extend(machine_only)
    return document


def _known_keys(
    dotted: str, table: dict[str, Any], known: Collection[str], notes: list[str], keys: list[str]
) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key, value in table.items():
        if key not in known:
            notes.append(UNKNOWN_NOTE.format(name=clipped(f"{dotted}.{key}")))
            keys.append(f"{dotted}.{key}")
        else:
            kept[key] = value
    return kept


def _holding(table: dict[str, Any], dotted: str) -> tuple[dict[str, Any], str] | None:
    """The table that holds the key `dotted` names — a section, or `section.key` — and the key's
    name in it; `None` when `table` holds no such key."""
    if dotted in table:
        return table, dotted
    section, _, name = dotted.partition(".")
    held = table.get(section)
    if isinstance(held, dict) and name in held:
        return held, name
    return None


def preset_defaults(project: str, *, preset: str = DEFAULT_PRESET) -> Config:
    """A `Config` built from a preset's `[defaults.*]` alone, for a directory that has no
    `drybag.toml`: `session.command_config` builds one for a caller that names the project
    itself.

    `drybag.version` is the one value the preset does not carry and `_build` requires, so it
    comes from `drybag.__version__` rather than from a default that would hold an empty string.

    `validate_paths` is deliberately not called. It is about a project root this caller does
    not have, and the `[paths]` values it would check are the preset's own defaults pointing at
    documents an overlay does not carry.
    """
    raw = load_preset(preset)
    defaults = defaults_of(raw)
    head = {**defaults.get("drybag", {}), "preset": preset, "version": __version__}
    project_config = _build(Project, "project", {**defaults.get("project", {}), "name": project})
    return Config(
        drybag=_build(Drybag, "drybag", head),
        project=project_config,
        paths=_build(Paths, "paths", defaults.get("paths", {})),
        memory=_build(Memory, "memory", defaults.get("memory", {})),
        budgets=Budgets(preset=dict(raw.get("budgets", {}))),
        native_caps=_build(NativeCaps, "native_caps", dict(raw.get("native_caps", {}))),
        commit_messages=_build(
            CommitMessages, "commit_messages", defaults.get("commit_messages", {})
        ),
        personal=_build(Personal, "personal", defaults.get("personal", {})),
    )
