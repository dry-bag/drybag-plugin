"""Write TOML tables, escaping every string, refusing everything it cannot represent.

One serialiser and not one per area. `attach` writes the overlay's `projects/<name>/project.toml`
and `setup` writes the machine configuration; hand-rolled, that is two writers in two areas with no
edge between them and no escaping rule — and the value this was written for is a **git remote URL**,
which is repository-authored (principle 5). A URL carrying a quote and a newline closes its own
string and writes further keys into a record that decides what `attach` trusts.

A leaf module: it imports `drybag.errors` and nothing else, so either caller reaches it
without paying for an area.

One rule, stated as the module's whole contract: every string is emitted as a basic TOML string
with `"`, `\\` and the control characters escaped, and a value this cannot represent raises
rather than being mangled. Silently dropping an unexpected type, or `str()`-ing it, is how a
capability record acquires a value nobody wrote. Keys are held to the bare-key grammar for the
same reason — quoting an unexpected key would let `[project]` hold a name nobody chose to write.

**Every value `tomllib` can parse, this can emit, at any depth.** That is a wider contract than
the first draft's ("two callers and both know their own types"), and a third input made it
necessary: `setup` rewrites `~/.config/drybag/config.toml`, a file `README.md` documents the
owner as writing by hand. The document read back off that file is not a caller's own dict — it is
whatever a person wrote — and refusing a type merely because no caller of ours produces it wedged
the command permanently: `[personal] scale = 1.5` or `[personal.editor] name = "nvim"` made every
future `drybag setup` exit 2 naming a serialiser the owner has never heard of. So floats, the
four TOML date and time types, and tables nested to any depth are emitted, inside arrays as well
as outside them — "at any depth" is the part the first attempt got wrong, and a table inside an
inline table inside an array was the same permanent wedge one level further in. What is left
refused is what no TOML document could have held, which is a caller bug and still worth refusing.

**One exception, and it is about keys rather than values.** `BARE_KEY` is untouched: a key this
cannot write bare is still refused, because quoting one here would record a name nobody chose to
write. `setup.machine` catches that refusal and re-raises it naming the file, the key and the
remedy, so it cannot wedge a command silently either.

`dumps` takes `{table: {key: value}}`, and a dict in a value position becomes a sub-table
header (`[personal.editor]`, after the parent's own keys, which is the order TOML requires). A
dict *inside a list* becomes an inline table, because an array of tables has no header form
that survives being nested in a value. The one table named `""` is written before any header,
because the overlay's `projects/<name>/project.toml` and the machine's binding records
(`drybag.binding`) hold their keys at the top level — their readers read them there, and moving
them under a header would make this serialiser and those readers disagree about one file.

**Comments are not preserved, and cannot be**: `tomllib` discards them on the way in, so a
rewrite of a hand-edited file keeps every table, key and value and loses the prose around them.
`setup.machine`'s docstring says the same thing where the owner's file is actually rewritten.
"""

from __future__ import annotations

import datetime
import re

from drybag.errors import Refusal

BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _key(name: object, what: str) -> str:
    if not isinstance(name, str) or not BARE_KEY.match(name):
        raise Refusal(
            f"{what} {name!r} is not a bare TOML key matching {BARE_KEY.pattern}; a caller that "
            f"means to write one writes one, and quoting it here would record a name nobody chose"
        )
    return name


def _quoted(value: str) -> str:
    """`value` as a basic TOML string, every character it cannot hold raw escaped."""
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif char < "\x20" or char == "\x7f":
            # Every other C0 control and DEL. Legal in a basic string only as an escape, so a
            # raw one is a parse error rather than a mangled value — which is worse, not better.
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _scalar(value: object, where: str) -> str:
    # `bool` before `int`, because `isinstance(True, int)` is True and `1` is not `true`.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _quoted(value)
    if isinstance(value, int):
        return _integer(value, where)
    if isinstance(value, float):
        # `repr` spells the three special values the way TOML does (`inf`, `-inf`, `nan`) and
        # gives a round-tripping decimal, always with a point or an exponent, for the rest.
        return repr(value)
    # `datetime` before `date`, because a `datetime` is a `date`; both spell themselves the way
    # TOML spells an offset date-time, a local date-time, a local date and a local time.
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        # Two shapes `isoformat` spells that TOML cannot read back: a local time carrying an
        # offset (TOML's local time has no offset form), and an offset that is not a whole
        # number of minutes (`+00:30:07`; TOML offsets are `±HH:MM`). Both parse-fail in
        # `tomllib`, which for `setup`'s rewrite-on-every-run file is the permanent wedge the
        # contract above exists to rule out -- so they are refused here rather than written.
        offset = value.utcoffset() if isinstance(value, datetime.datetime | datetime.time) else None
        if isinstance(value, datetime.time) and offset is not None:
            raise Refusal(
                f"{where} holds a time with a UTC offset, which TOML has no spelling for; "
                f"write a naive time, or a datetime"
            )
        if offset is not None and offset.total_seconds() % 60:
            raise Refusal(
                f"{where} holds a UTC offset that is not a whole number of minutes, which TOML "
                f"cannot spell"
            )
        return value.isoformat()
    raise Refusal(
        f"{where} holds a {type(value).__name__}, which this serialiser does not emit; it writes "
        f"every type a TOML document can hold — strings, integers, floats, booleans, dates and "
        f"times, tables, and lists of those — and refuses anything else rather than guessing"
    )


def _integer(value: int, where: str) -> str:
    """`value` as a TOML integer: decimal, or hexadecimal where decimal cannot be spelled.

    `str` refuses an integer past the interpreter's 4,300-digit limit, and `tomllib` converts a
    hex, octal or binary literal of any length, so a document this was handed back to write --
    an adopted `drybag.toml`, the owner's machine file -- could hold one: the rewrite ended in
    `ValueError`, an internal error, before anything had judged the value. `hex` has no such
    limit and TOML reads it back. A negative integer that large has no spelling at all (TOML's
    hexadecimal has no sign) and no document could have held one.
    """
    try:
        return str(value)
    except ValueError:
        if value < 0:
            raise Refusal(
                f"{where} holds an integer too long for decimal and below zero, which TOML "
                f"cannot spell"
            ) from None
        return hex(value)


def _inline(table: dict[str, object], where: str) -> str:
    """A dict in a list position, as an inline table: `{ a = 1 }`.

    An array of tables (`[[x]]`) parses back as a list of dicts, and a header form cannot be
    nested inside a value, so the inline spelling is the one shape that round-trips.
    """
    body = ", ".join(
        f"{_key(key, 'key')} = {_element(value, f'{where}.{key}')}" for key, value in table.items()
    )
    return "{" + body + "}"


def _value(value: object, where: str) -> str:
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_element(item, f"{where}[]") for item in value) + "]"
    return _scalar(value, where)


def _element(value: object, where: str) -> str:
    if isinstance(value, dict):
        return _inline(value, where)
    return _value(value, where)


def _emit(lines: list[str], path: tuple[str, ...], table: dict[str, object]) -> None:
    """One table's own keys, then one sub-table per dict value — the order TOML requires."""
    header = ".".join(_key(part, "table name") for part in path)
    if header:
        lines.append(f"[{header}]")
    children: list[tuple[str, dict[str, object]]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            children.append((_key(key, "key"), value))
            continue
        lines.append(f"{_key(key, 'key')} = {_value(value, f'{header}.{key}')}")
    lines.append("")
    for key, child in children:
        _emit(lines, (*path, key), child)


def dumps(tables: dict[str, dict[str, object]]) -> str:
    """`{table: {key: value}}` as TOML text, in the order given; `""` names the root table.

    The root table is emitted first whatever position it holds in `tables`. `_emit` writes no
    header for it, so a root that followed `[n]` bound its keys to `[n]` -- the misplacement
    this module exists to prevent, arrived at by ordering rather than by escaping. Both callers
    happened to put it first; the serialiser no longer depends on that.
    """
    lines: list[str] = []
    for name, table in sorted(tables.items(), key=lambda item: item[0] != ""):
        if not isinstance(table, dict):
            raise Refusal(
                f"[{name}] is not a table of keys; this file is written one table at a time, "
                f"and a value here has to sit under a table header"
            )
        _emit(lines, (name,) if name else (), table)
    return "\n".join(lines)
