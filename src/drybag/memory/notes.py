"""One note, read and written without losing what this reader does not understand.

The store has two writers: the harness's native memory writer and drybag. That is workable
only because neither rewrites the other's keys — and "does not rewrite" has to mean *bytes*,
not *meaning*. A reader that parses `description: 'tis a note` and renders it back as
`description: "'tis a note"` has changed a file it was asked to leave alone; do that to
seventy notes and the first `memory index` is a diff nobody can review, followed by a
ping-pong with the native writer over quoting.

So the frontmatter is kept as the lines it arrived as, and `render_note` rewrites **only the
keys whose value this run actually changed**. Byte-identity for an untouched note is then a
property of the design rather than a property of the quoting rules, and bit-compatibility with
the native writer holds for keys this module has never heard of.

A YAML library would be the obvious parser and is not available: the runtime is stdlib-only
so that a hook works before any environment exists. The grammar below is the smallest one the
real corpus needs — flat `key: value`, plus exactly one two-space block under `metadata:` —
and it refuses anything else loudly, with a line number, rather than guessing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from drybag import fsops
from drybag.errors import Failure
from drybag.fsops import read_regular_text, said, utf_8_name, write_atomically

# Sort sentinel for a note whose `startup` metadata could not be parsed as an int. Not a named cap
# (CONTRIBUTING.md#named-caps): it bounds nothing, and only needs to sort after every real startup
# rank the corpus can hold.
UNRANKED = 10_000
FENCE = "---"
_KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<rest>.*)$")
_NOT_A_RULE = frozenset({"false", "no", "off"})
DECLARED = ("name", "description", "index", "index_provenance", "group", "group_order")
# Why `walk` quarantines a note by its file's name alone: the index links to every note by that
# name, verbatim, and `MEMORY.md` is UTF-8.
NAME_NOT_UTF_8 = "the file's name is not UTF-8, so the index cannot name it; rename the file"


class NoteError(Failure):
    """A note whose frontmatter cannot be read without guessing."""


class NoteType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


class Provenance(StrEnum):
    CURATED = "curated"
    NATIVE = "native"
    PROVISIONAL = "provisional"


def _unescape(value: str) -> str:
    """Undo exactly the two sequences `_quote` writes, and nothing else.

    Deliberately not a general YAML unescaper: `\\n` inside a double-quoted scalar the *native*
    writer produced is left as the two characters it already read as, because turning it into a
    newline would hand this module a value it has no line-based way to write back.
    """
    out: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value) and value[index + 1] in '\\"':
            out.append(value[index + 1])
            index += 2
            continue
        out.append(value[index])
        index += 1
    return "".join(out)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        # Only the double-quoted form is unescaped, because only that form is one `_quote`
        # could have written. A single-quoted scalar escapes a quote by doubling it, never
        # with a backslash, so unescaping one would corrupt the native writer's own output.
        return _unescape(inner) if value[0] == '"' else inner
    return value


def is_one_line(value: str) -> bool:
    """Whether a value occupies exactly one line and carries no line break of its own.

    `str.splitlines()` is the oracle, not a scan for `"\\n"`, because `_split` below finds the
    frontmatter fence with `splitlines()` — so the characters that decide where a note's
    frontmatter ends are exactly the ones it breaks on: `\\n`, `\\r`, `\\x0b`, `\\x0c`, `\\x1c`,
    `\\x1d`, `\\x1e`, `\\x85`, U+2028 and U+2029. A guard that named its own two (`"\\n"` and
    `"\\r"`, the two `read_text`'s universal newlines and `_ENTRY` had already made
    unreachable) both over- and under-shot; asking the same function the parser asks is what
    keeps them from drifting apart again. `index._extra` asks this too, for the same value
    rendered into the same one-line frontmatter by another route.

    `value.splitlines() == [value]` rather than `len(value.splitlines()) > 1`: a value that
    merely *ends* in a break — `"one line\\n"` — still splits into one element while plainly
    carrying one. The empty string is one line, because a note without `description:` is
    ordinary and `read_note` fills in `""` for it.
    """
    return not value or value.splitlines() == [value]


def _quote(value: str, path: Path) -> str:
    """Only ever applied to a value this run is writing for the first time.

    The inverse of `_unquote`, and that has to be literally true: the escaping here and the
    unescaping there are one pair, and a reader that escaped without unescaping turned
    `he said "no"` into a stored `he said \\"no\\"` that then rendered into `MEMORY.md` with the
    backslashes visible.

    A line break is refused rather than represented. This grammar is flat `key: value` lines
    and there is no line-based frontmatter form of one — written bare (which is what such a
    value gets, holding none of `: # " '` and neither leading nor trailing whitespace, since
    `str.strip()` leaves an interior U+2028 alone) the remainder spills past the key's line and
    the note stops parsing on the next read. Refusing is the honest answer, and `NoteError` is
    a `Failure`: exit 1, with the path in the message.
    """
    if not is_one_line(value):
        raise NoteError(f"{path}: a frontmatter value must be one line: {value!r}")
    if value and not any(ch in value for ch in ":#\"'") and value.strip() == value:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_int(value: str | None) -> int | None:
    """`group_order` parsed the way `startup`/`as_of` already are: a value that is not an
    int is `None`, never a crash. `"--5".lstrip("-").isdigit()` is `True` while `int("--5")`
    still raises, and `walk` only catches `NoteError` — a bare `ValueError` here would cost
    the whole store walk, which is exactly what this module exists to not do.
    """
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Note:
    path: Path
    name: str
    description: str
    body: str
    index: str | None = None
    index_provenance: Provenance = Provenance.CURATED
    group: str | None = None
    group_order: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    # The frontmatter exactly as it was read, and the values parsed out of it. `render_note`
    # rewrites a line only where the two disagree, so an unchanged note round-trips byte for
    # byte and a key this module does not model is never touched.
    raw: tuple[str, ...] = ()
    original: dict[str, str] = field(default_factory=dict)
    # Everything after the closing fence, exactly as it was on disk, and the line ending the
    # frontmatter used. `body` above is the normalised reading every consumer wants; these two
    # are what `render_note` writes back, so a note this run did not change comes back byte for
    # byte — the body included, which it did not.
    #
    # `None`, not `""`, for a `Note` built in code rather than read from a file: a note whose
    # body is genuinely empty has `verbatim == ""` and must still round-trip to a file with
    # nothing after the fence, which is not what `body` alone would render.
    verbatim: str | None = None
    newline: str = "\n"
    # The `memory.groups` entry this note was walked under, exactly as configured. `walk` sets
    # it; a `Note` read on its own has no way to know, and `group_name` falls back to the
    # folder's name for that case.
    store_group: str | None = None

    @property
    def type(self) -> NoteType | None:
        try:
            return NoteType(self.metadata.get("type", ""))
        except ValueError:
            return None

    @property
    def startup(self) -> int | None:
        raw = self.metadata.get("startup")
        if raw is None or raw.strip().lower() in _NOT_A_RULE:
            return None
        try:
            return int(raw)
        except ValueError:
            return UNRANKED

    @property
    def as_of(self) -> date | None:
        raw = self.metadata.get("as_of")
        if not raw:
            return None
        try:
            return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    @property
    def words(self) -> int:
        return len(self.body.split())

    @property
    def group_name(self) -> str:
        """The store group a note belongs to, as `memory.groups` spells it. `group` is a
        sub-heading *inside* that section.

        `path.parent.name` was the whole answer, and it is only the right one while every
        configured group is a single path segment — which nothing checks. `contained()` admits
        `"team/project-stable"` and `walk` finds the notes under it, but the folder's name is
        then `"project-stable"` while `render_index` looks each group up by its configured
        string: the section matched nothing and vanished from `MEMORY.md` entirely, with
        `memory index --check` reporting "index is current" and exiting 0.
        """
        return self.store_group or self.path.parent.name


def _newline_of(text: str) -> str:
    """The line ending the frontmatter uses, so a rewritten key keeps the file's own."""
    return "\r\n" if "\r\n" in text.split("\n", 1)[0] + "\n" else "\n"


def _split(text: str, path: Path) -> tuple[list[str], str, str]:
    """`(frontmatter lines, the body, the body exactly as it was on disk)`.

    Two forms of the body, because two things want it. `body` is normalised — line endings to
    `\n`, leading and trailing blank lines gone — and is what word counts, bundles and every
    reader are written against. `verbatim` is the bytes after the closing fence, untouched, and
    is what `render_note` writes back when nothing changed it.

    Without the second, `Note.raw`'s own claim that "an unchanged note round-trips byte for
    byte" was true of the frontmatter and false of the body: a CRLF note, a note with two blank
    lines before its first paragraph, or one with no trailing newline came back renormalised.
    The first `memory index` over a store somebody else's tool wrote therefore produced exactly
    the unreviewable diff this module exists to prevent — for *some* notes, which is harder to
    notice than for all of them.
    """
    kept = text.splitlines(keepends=True)
    lines = [line.rstrip("\r\n") for line in kept]
    if not lines or lines[0].strip() != FENCE:
        raise NoteError(f"{path}: no frontmatter; a note opens with '---'")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == FENCE)
    except StopIteration:
        raise NoteError(f"{path}: frontmatter is never closed") from None
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return lines[1:end], body, "".join(kept[end + 1 :])


def _parse(lines: list[str], path: Path) -> tuple[dict[str, str], dict[str, str]]:
    top: dict[str, str] = {}
    meta: dict[str, str] = {}
    in_metadata = False
    for offset, line in enumerate(lines, start=2):
        if not line.strip():
            continue
        match = _KEY.match(line)
        if match is None:
            raise NoteError(f"{path}: line {offset} is not 'key: value'")
        indent, key, rest = match.group("indent"), match.group("key"), match.group("rest")
        value = rest.strip()
        if indent == "":
            if key == "metadata":
                if value:
                    raise NoteError(f"{path}: line {offset}: metadata must open a block")
                in_metadata = True
                continue
            in_metadata = False
            if not value:
                raise NoteError(f"{path}: line {offset}: {key!r} has no value on its own line")
            if key in top:
                raise NoteError(f"{path}: line {offset}: duplicate key {key!r}")
            top[key] = _unquote(value)
            continue
        if not in_metadata or len(indent) != 2:
            raise NoteError(f"{path}: line {offset}: only 'metadata:' may nest, two spaces deep")
        if key in meta:
            raise NoteError(f"{path}: line {offset}: duplicate key 'metadata.{key}'")
        meta[key] = _unquote(value)
    return top, meta


def read_note(path: Path) -> Note:
    try:
        # `newline=""` and not `read_text`: universal-newline translation turns every `\r\n`
        # into `\n` before this module ever sees it, which makes the byte-for-byte round trip
        # below impossible to keep for a CRLF note no matter how carefully the rewrite is done.
        # `scaffold.engine._read` opens the same way, for the same reason.
        # A regular file only, followed through the group links `attach` makes in overlay mode
        # (`fsops.open_regular` says what a link to a device did); a refusal is an `OSError`,
        # and the note is quarantined as unreadable.
        text = read_regular_text(path, newline="")
    except OSError as exc:
        raise NoteError(f"{path} cannot be read ({said(exc)})") from exc
    # `UnicodeDecodeError` is not an `OSError`. Without it one latin-1 byte in a note left this
    # walk as `internal error: UnicodeDecodeError` and exit 2 — a repository's malformed input
    # reading as this tool being broken. It is a note this reader cannot read, exactly like the
    # `OSError` above, so `walk` quarantines it onto `unreadable` and the command reports it.
    except UnicodeDecodeError as exc:
        raise NoteError(f"{path} is not valid UTF-8 ({exc.reason})") from None
    lines, body, verbatim = _split(text, path)
    top, meta = _parse(lines, path)
    try:
        provenance = Provenance(top.get("index_provenance", Provenance.CURATED))
    except ValueError as exc:
        raise NoteError(f"{path}: {exc}") from exc
    return Note(
        path=path,
        name=top.get("name", path.stem),
        description=top.get("description", ""),
        body=body,
        index=top.get("index"),
        index_provenance=provenance,
        group=top.get("group"),
        group_order=_parse_int(top.get("group_order")),
        metadata=meta,
        raw=tuple(lines),
        original=dict(top),
        verbatim=verbatim,
        newline=_newline_of(text),
    )


def _wanted(note: Note) -> dict[str, str | None]:
    """The declared keys this module owns, as they should read after this run."""
    return {
        "name": note.name,
        "description": note.description,
        "index": note.index,
        # Only a harvested line is ever written with its provenance: a provisional one is the
        # index's rendering and is written into no note (`index.reconcile`).
        "index_provenance": (
            note.index_provenance.value if note.index_provenance is Provenance.NATIVE else None
        ),
        "group": note.group,
        "group_order": None if note.group_order is None else str(note.group_order),
    }


def _still_the_read_time_default(key: str, value: str | None, note: Note) -> bool:
    """True when a declared key absent from the file carries only the harmless default
    `read_note` fills in for convenience (`""` for `description`, the file stem for `name`)
    rather than a value this run actually decided to write. Writing it in would invent a
    line the file never had — the exact defect a real note without `description:` exposed.
    """
    if key in note.original:
        return False
    if key == "description":
        return value == ""
    if key == "name":
        return value == note.path.stem
    return False


def render_note(note: Note) -> str:
    wanted = _wanted(note)
    # A key this module could not parse — `group_order: 2b` — has a wanted value of `None`
    # while the file plainly has a line. That is not a deletion, it is a value this reader
    # does not understand, and nothing in this area deletes a declared key, so an absent
    # wanted value never overrides a present original.
    changed = {
        key: value
        for key, value in wanted.items()
        if value != note.original.get(key)
        and not (value is None and key in note.original)
        and not _still_the_read_time_default(key, value, note)
    }
    lines: list[str] = [FENCE]
    seen: set[str] = set()
    for line in note.raw:
        match = _KEY.match(line)
        key = match.group("key") if match and match.group("indent") == "" else None
        if key is None or key not in changed:
            lines.append(line)
            continue
        seen.add(key)
        value = changed[key]
        if value is not None:
            lines.append(f"{key}: {_quote(value, note.path)}")
    # A key this run introduced goes in declared order, before `metadata:`.
    fresh = [k for k in DECLARED if k in changed and k not in seen and changed[k] is not None]
    if fresh:
        cut = next(
            (i for i, line in enumerate(lines) if line.strip() == "metadata:"),
            len(lines),
        )
        insert = [f"{key}: {_quote(str(changed[key]), note.path)}" for key in fresh]
        lines = lines[:cut] + insert + lines[cut:]
    lines.append(FENCE)
    frontmatter = note.newline.join(lines) + note.newline
    # The body as it was, when there is one to preserve; the normalised form otherwise. Nothing
    # in this area edits a body — `with_index` changes the `index:` line and nothing else — so
    # the first branch is the one every note read from disk takes.
    if note.verbatim is not None:
        return frontmatter + note.verbatim
    return frontmatter + note.newline + note.body + note.newline


def with_index(note: Note, line: str, provenance: Provenance) -> Note:
    return replace(note, index=line, index_provenance=provenance)


def write_note(note: Note) -> None:
    write_atomically(note.path, render_note(note))


@dataclass(frozen=True)
class Walk:
    """Notes that parsed, the files that did not — never one at the cost of the other — and the
    note files that are links, which are skipped."""

    notes: list[Note]
    unreadable: list[tuple[Path, str]]
    skipped: list[Path] = field(default_factory=list)


def walk(store: Path, groups: Sequence[str]) -> Walk:
    """Every `*.md` note under the named groups, quarantining the files that will not parse.

    A non-note file in a store is ignored here and flagged by `doctor`, and a store is a place
    humans put things: one superseded design document with no frontmatter must not cost the
    whole store, which is exactly what a raising walk does inside a handler's `except`.

    **A note file that is a link is skipped, in every store**: neither read into a bundle nor
    digested for the reviewed record, only counted. A repository chooses its memory mode and
    commits its notes, so a linked note is a read it aims — at a file outside the repository, or
    at a note the owner promoted elsewhere, whose digest would make it a standing rule here. A
    group directory that is a link, as each of the composed store's is, is still followed: only
    the last component is asked.
    """
    found: list[Note] = []
    unreadable: list[tuple[Path, str]] = []
    skipped: list[Path] = []
    for group in groups:
        directory = store / group
        if not fsops.is_dir(directory):
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.startswith((".", "_")):
                continue
            if fsops.is_symlink(path):
                skipped.append(path)
                continue
            # The index names a note by its file's name, and `MEMORY.md` is UTF-8: a name the
            # disk holds in other bytes cannot be written into it, so the note is unreadable.
            if not utf_8_name(path.name):
                unreadable.append((path, f"{path}: {NAME_NOT_UTF_8}"))
                continue
            try:
                found.append(replace(read_note(path), store_group=group))
            except NoteError as exc:
                unreadable.append((path, str(exc)))
    return Walk(notes=found, unreadable=unreadable, skipped=skipped)
