"""A `trail.toml` theme's `pattern`: the part of regular-expression syntax a filename filter needs,
matched without backtracking.

A theme's `pattern` is repository-authored, and `re`'s backtracking turns a pattern such as
`(a+)+$` exponential on a name of forty characters, so under `re` `drybag docs trail` would
never finish. The language kept here is a subset of
the regular expressions such files already hold, with the meaning `re.search` gives them on a
name of one line: literal text, `.` for any one character, `.*` for any run of them, `|`
between alternatives, `^` and `$` at an alternative's two ends, and `\\` before a punctuation
character to take it literally.
A name holding a newline is compared without `re`'s newline rules — here `.` matches a newline
and `$` is only the name's very end — and none reaches a theme: the listing refuses a document
whose name is not a single line before it asks which theme the name is in.
The shipped `.*` and patterns such as `widget` and `gadget|gizmo` mean what they meant; anything
else — a group, a class, another quantifier — is refused rather than guessed at.

**Why it cannot backtrack.** An alternative is a run of fixed-length pieces separated by `.*`.
Placing each piece at its leftmost fit after the one before leaves the longest rest for the ones
after it, so the leftmost fit is the only placement worth trying, and a match is one left-to-right
pass: at most the name's length times the pattern's, whatever either holds.

**Why a pattern has a length bound.** That product is still the repository's to choose: a match
costs up to the name's length times the pattern's, so an unbounded pattern could hold the `trail`
gate for seconds per name, and a `trail.toml` of such patterns past its job's time limit.
`PATTERN_MAX_CHARS` bounds each pattern, and `docs.trail` bounds how many themes a file may hold,
since every name is tried against every theme until one matches.
"""

from __future__ import annotations

from dataclasses import dataclass

# The longest theme `pattern`, in characters: a filename filter needs a few dozen, and a match
# costs up to the name's length times this.
PATTERN_MAX_CHARS = 256
TOO_LONG = f"a theme's `pattern` is at most {PATTERN_MAX_CHARS} characters"
RULE = (
    "a theme's `pattern` is literal text with `.` for any one character, `.*` for any run of "
    "characters, `|` between alternatives, `^` and `$` at an alternative's start and end, and "
    "`\\` before a punctuation character to take it literally"
)

Piece = tuple[str | None, ...]  # one character each; `None` is `.`, any one character


@dataclass(frozen=True)
class _Alternative:
    start: bool  # anchored by `^`
    pieces: tuple[Piece, ...]  # the fixed-length runs between one `.*` and the next
    end: bool  # anchored by `$`

    def matches(self, name: str) -> bool:
        first, *rest = self.pieces
        if not rest:
            if self.start and self.end:
                return len(name) == len(first) and _fits(name, 0, first)
            if self.start:
                return _fits(name, 0, first)
            if self.end:
                return _fits(name, len(name) - len(first), first)
            return _find(name, first, 0) >= 0
        *middle, last = rest
        at = 0 if self.start else _find(name, first, 0)
        if at < 0 or not _fits(name, at, first):
            return False
        position = at + len(first)
        for piece in middle:
            at = _find(name, piece, position)
            if at < 0:
                return False
            position = at + len(piece)
        if self.end:
            tail = len(name) - len(last)
            return tail >= position and _fits(name, tail, last)
        return _find(name, last, position) >= 0


def _fits(name: str, at: int, piece: Piece) -> bool:
    if at < 0 or at + len(piece) > len(name):
        return False
    return all(want is None or name[at + k] == want for k, want in enumerate(piece))


def _find(name: str, piece: Piece, start: int) -> int:
    """Where `piece` first fits in `name` at or after `start`, or `-1`."""
    return next(
        (at for at in range(start, len(name) - len(piece) + 1) if _fits(name, at, piece)), -1
    )


@dataclass(frozen=True)
class ThemePattern:
    alternatives: tuple[_Alternative, ...]

    def search(self, name: str) -> bool:
        """Whether `re.search` would find the pattern in `name`, for a pattern in the language and
        a name with no newline in it."""
        return any(alternative.matches(name) for alternative in self.alternatives)


_REFUSED = frozenset("()[]{}+?*")


def compile_theme(pattern: str) -> ThemePattern:
    """`pattern` in the theme language, or `ValueError` naming no part of it: the text is the
    repository's, and `RULE`, or `TOO_LONG` past `PATTERN_MAX_CHARS`, is what the person needs."""
    if len(pattern) > PATTERN_MAX_CHARS:
        raise ValueError(TOO_LONG)
    alternatives: list[_Alternative] = []
    start, end = False, False
    pieces: list[Piece] = []
    piece: list[str | None] = []
    at = 0

    def close() -> None:
        nonlocal start, end, pieces, piece
        alternatives.append(_Alternative(start, (*pieces, tuple(piece)), end))
        start, end, pieces, piece = False, False, [], []

    while at < len(pattern):
        char = pattern[at]
        if end and char != "|":
            raise ValueError(RULE)  # something after `$` in the same alternative
        if char == "\\":
            # Only punctuation: in a regular expression `\d`, `\w` or `\1` is a class or a
            # reference, and read as a literal letter it would mean something else.
            if at + 1 == len(pattern) or pattern[at + 1].isalnum():
                raise ValueError(RULE)
            piece.append(pattern[at + 1])
            at += 2
            continue
        if char == "|":
            close()
        elif char == "^":
            if start or pieces or piece:
                raise ValueError(RULE)
            start = True
        elif char == "$":
            end = True
        elif char == ".":
            if pattern[at + 1 : at + 2] == "*":
                pieces.append(tuple(piece))
                piece = []
                at += 1
            else:
                piece.append(None)
        elif char in _REFUSED:
            raise ValueError(RULE)
        else:
            piece.append(char)
        at += 1
    close()
    return ThemePattern(tuple(alternatives))
