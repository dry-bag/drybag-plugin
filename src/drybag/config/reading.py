"""What every reader of a configuration file shares: the refusals, the words for a document that
does not parse, the machine file's reader, and the checks of a value both layers apply.

Two loaders read configuration: `config.loader`, a repository's `drybag.toml` under its preset,
and `config.owner`, the machine owner's layer out of the machine file. The project loader builds
on the owner's layer, so the two meet here, below both, rather than one importing the other's
private names: a refusal is one type (`ConfigError`, and `MachineConfigError` for the machine
file), a document that does not parse is said in one set of words (`toml_position`), the machine
file is read once (`read_machine_toml`), and a value is held to its schema type by one rule
(`coerced`, `positive`, `schema_types`) whichever layer wrote it, against one answer for the keys a
table holds (`table_types`).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import fields
from functools import cache
from pathlib import Path
from typing import Any, cast, get_origin, get_type_hints

from drybag import fsops
from drybag.config.schema import Budgets
from drybag.errors import Failure
from drybag.fsops import said

# Where `tomllib` stopped, and nothing else it had to say. Every `TOMLDecodeError` this
# package can raise is about a document somebody else wrote — a project's `drybag.toml`, the
# machine file `--machine` named — and `tomllib` builds its message as `f"{msg} (at line N,
# column M)"`, where `msg` embeds the source for at least five of its own faults: `Cannot
# declare ('x',) twice`, `Duplicate inline table key 'k'`, `Cannot redefine namespace k`,
# `Found invalid character 'c'`, `Cannot overwrite a value`. A TOML key is arbitrary quoted
# text, so interpolating the exception whole puts unbounded repository bytes into a refusal a
# skill is instructed to relay to a model. The position is the actionable half and it is
# drybag-shaped: two integers, or the parser's end-of-document form. Measured against CPython
# 3.11 and 3.12; the suffix is what `tomllib` appends, not what its `msg` says, so a `msg`
# reworded upstream does not move it.
_TOML_POSITION = re.compile(r"\((?:at line \d+, column \d+|at end of document)\)\Z")
NO_POSITION = "(at a position tomllib did not report)"
# `tomllib` reads nested arrays and inline tables by recursion, so a document nested a few
# thousand levels deep — `a = [[[…]]]` — raises `RecursionError` and not `TOMLDecodeError`.
# Uncaught, that is an internal error, the class drybag keeps for its own defects. And an
# integer literal longer than the interpreter converts (4,300 digits by default,
# `sys.get_int_max_str_digits`) is valid TOML that `tomllib` answers with a plain `ValueError`,
# which is not a `TOMLDecodeError`: one line of a committed file ended `doctor` the
# same way. Every reader of a document somebody else wrote catches
# all three. `ValueError` is caught whole, `TOMLDecodeError` being one: no reader's `try` holds
# anything else that raises it but a decode, whose `UnicodeDecodeError` it catches first or
# answers as it answers a record that does not parse.
UNPARSEABLE = (tomllib.TOMLDecodeError, RecursionError, ValueError)
TOO_DEEP = "(nested deeper than the parser reads)"
TOO_LONG = "(holds a number longer than the parser converts)"

# A named cap: every integer a configuration key holds is below it. `tomllib` converts a hex, octal
# or binary literal of any length (a power-of-two base is exempt from the interpreter's 4,300-digit
# limit), and a decimal of 401 digits is inside that limit, so a key checked only for `<= 0` handed
# its reader a number no `str` could print or no `float` could hold: a timeout key an earlier
# drybag read ended the command that read it in an internal error. 2**31 is what a
# signed 32-bit count holds, far above any budget, cap or timeout a project writes (the preset's
# largest integer is `memory_index_bytes = 25600`), and below what every reader of one handles:
# `str`, `float`, a comparison. No shipped file states it.
INTEGER_LIMIT = 2**31


class ConfigError(Failure):
    """A drybag.toml that cannot be trusted as written.

    `key` is the dotted key a refusal of one key is about — `memory.mode`, `paths.specs`, a
    section's name for a section that is not a table — and `None` for a refusal of the whole
    document. It is the key the NOTE path decides about, removing it or naming it
    (`loads_noting`); it is never printed, so it may be any text the document chose.
    """

    def __init__(self, message: str, *, key: str | None = None) -> None:
        super().__init__(message)
        self.key = key


# Fixed text and a path drybag chose or the owner typed. Not the decoder's message: it is only
# a byte and an offset, but the one sentence says what to do about every such file.
NOT_UTF8 = "{path} is not UTF-8 text; drybag reads it only as UTF-8"
# What the error says, in `fsops.said`'s words, and not its message, which repeats the path and
# adds nothing to act on; never its class's name, which for a reader's own refusal is a name of
# drybag's internals (`fsops.TooLarge`, `fsops.NotRegularFile`) and names no condition.
UNREADABLE = "{path} cannot be read ({error})"


class MachineConfigError(ConfigError):
    """The **machine** file could not be read, which is not `drybag.toml`'s doing.

    A subclass and not a message, because the one caller that has to tell them apart must not
    do it by reading the text: `doctor` deliberately never quotes a loader message — the loader
    builds it out of the file's own keys and values — so its only way to say which of the two
    files is broken is the type. Without it, a `~/.config/drybag/config.toml` with a stray
    bracket in it would read as `drybag.toml is here and does not load`, sending the owner to
    edit a file with nothing wrong with it.
    """


def toml_position(exc: tomllib.TOMLDecodeError | RecursionError | ValueError) -> str:
    """The `(at line N, column M)` suffix `tomllib` appends, with its message text dropped.

    One extractor for every caller in this package that reports a document it did not write,
    so that "what may print out of a parse failure" is one decision rather than one per site.
    See `_TOML_POSITION` for which of `tomllib`'s own messages embed the source and why that
    makes the whole exception unprintable.

    A suffix this cannot find is reported as absent rather than as the message: a `tomllib` that
    stopped appending a position would otherwise take this guard with it silently, which is the
    shape every other bounded value in this file refuses. A document nested past the parser's
    recursion has no position, and says so; so does one holding a number past the conversion
    limit, whose own message would tell the reader to raise a process-wide limit rather than fix a
    file somebody else wrote.
    """
    if isinstance(exc, RecursionError):
        return TOO_DEEP
    if not isinstance(exc, tomllib.TOMLDecodeError):
        return TOO_LONG
    found = _TOML_POSITION.search(str(exc))
    return found.group(0) if found is not None else NO_POSITION


def read_fault(
    shown: str,
    exc: OSError | tomllib.TOMLDecodeError | RecursionError | ValueError,
    effect: str,
) -> str:
    """What a file the owner wrote and a reader could not give says: `shown` (already printed),
    that it is not UTF-8, cannot be read (`fsops.said`) or is not TOML (`toml_position`), then
    `effect`, what that means to the reader, e.g. "so the project lists no tags"."""
    if isinstance(exc, UnicodeDecodeError):
        what = "is not UTF-8 text"
    elif isinstance(exc, OSError):
        what = f"cannot be read ({said(exc)})"
    else:
        what = f"is not valid TOML {toml_position(exc)}"
    return f"{shown} {what}, {effect}"


def read_machine_toml(path: Path) -> dict[str, Any] | None:
    """The machine file at `path` as `tomllib` parses it, or `None` when no file is there.

    One reader for every reader of the file — `[personal]` here, `[overlay] root` in
    `config.overlay` — so a file that cannot be read, is not UTF-8 or is not TOML is one failure
    in one set of words: `MachineConfigError`, the type `doctor` tells it from a broken
    `drybag.toml` by, naming the error's class or the parser's position and never the parser's
    message, which quotes the file's own text. An absent file is the ordinary state before
    `drybag setup` has run, and each caller says what it means.
    """
    if not fsops.is_file(path):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise MachineConfigError(NOT_UTF8.format(path=path)) from None
    except OSError as exc:
        raise MachineConfigError(UNREADABLE.format(path=path, error=said(exc))) from None
    try:
        return tomllib.loads(text)
    except UNPARSEABLE as exc:
        raise MachineConfigError(f"{path} is not valid TOML {toml_position(exc)}") from None


@cache
def schema_types(cls: type[Any]) -> dict[str, Any]:
    """Real type objects for a schema class, resolved once per process.

    `get_type_hints` is what makes them real: under `from __future__ import annotations`
    `field.type` is only the source string, and dispatching on that string would make every type
    the branches do not spell — `float`, `int | None`, an alias — silently "must be a string", so
    a valid config would be refused with a wrong reason. Resolving costs an `eval` per annotation,
    which is why a load does not pay it nine times.
    """
    hints = get_type_hints(cls)
    return {f.name: hints[f.name] for f in fields(cls)}


def table_types(cls: type[Any]) -> dict[str, Any]:
    """The keys a table of the schema class `cls` holds and the type `coerced` holds each value to:
    `Budgets` is a table of named integers (`Budgets.NAMES`), and every other class's keys are its
    fields. One answer for both layers, so a budget the project file may set and one the machine
    file may set are the same names."""
    if cls is Budgets:
        return dict.fromkeys(Budgets.NAMES, int)
    return schema_types(cast(Any, cls))


# What `coerced` says of a value its schema type does not take. Each names the key and never the
# value, which is the layer's text.
NOT_A_LIST = "{key} must be a list of strings"
NOT_A_SWITCH = "{key} must be true or false"
NOT_A_STRING = "{key} must be a string"
UNSUPPORTED_TYPE = (
    "{key} has an unsupported schema type: {label}; the loader coerces tuple[str, ...], bool, int "
    "and str"
)


def coerced(key: str, annotation: Any, value: object) -> Any:
    """`value` as the schema type `annotation` holds it; a `ConfigError` naming `key` otherwise.

    Keyed on the type `schema_types` resolved, never on the table the key sits in: the machine
    file's reader once checked `[guards]` for a bool and everything else for a string, so an `int`
    added to `Personal` would have been "must be a string" in the machine file while the project
    loader took it. An annotation none of the four branches spells is drybag's own defect, and
    its refusal is the whole document's, so it carries no key.
    """
    if get_origin(annotation) is tuple:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(NOT_A_LIST.format(key=key), key=key)
        return tuple(value)
    if annotation is bool:
        if not isinstance(value, bool):
            raise ConfigError(NOT_A_SWITCH.format(key=key), key=key)
        return value
    if annotation is int:
        return positive(key, value)
    if annotation is str:
        if not isinstance(value, str):
            raise ConfigError(NOT_A_STRING.format(key=key), key=key)
        return value
    label = getattr(annotation, "__name__", None) or str(annotation)
    raise ConfigError(UNSUPPORTED_TYPE.format(key=key, label=label))


def positive(key: str, value: object) -> int:
    """`value` when it is a positive integer below `INTEGER_LIMIT`; a `ConfigError` naming `key`
    otherwise. Never the value: a number that large is the one no message could print."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be a positive integer", key=key)
    if value >= INTEGER_LIMIT:
        raise ConfigError(f"{key} must be a positive integer below {INTEGER_LIMIT:,}", key=key)
    return value
