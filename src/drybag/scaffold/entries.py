"""The reader and the writer of a settings file a person and several tools share, and the marker
that claims a command as drybag's.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from drybag.errors import Refusal
from drybag.jsonobject import json_object, json_text

ENTRY_MARKER = "# drybag:"
_MARKER = re.compile(r"#\s*drybag:([A-Za-z0-9][A-Za-z0-9._-]*)\s*$")
# What every refusal of a settings document calls it: the engine is handed text, never a path.
_DOCUMENT = "settings document"


class EntriesError(Refusal):
    """A settings document the engine cannot rewrite without guessing."""


class ParserLimitError(EntriesError):
    """A settings document that is valid JSON and passes a limit of this interpreter's parser.

    Its own kind, because it is not malformed: a harness whose parser has no such limit reads it,
    and may run the hooks in it. A reader that reports on such a file cannot say "it would not
    load anyway", which it may say of one that does not parse.
    """


def marker_id(command: str) -> str | None:
    match = _MARKER.search(command)
    return match.group(1) if match else None


def mark(command: str, entry_id: str) -> str:
    if marker_id(command) == entry_id:
        return command
    return f"{command}  {ENTRY_MARKER}{entry_id}"


def _past_parser(label: str) -> Callable[[str], ParserLimitError]:
    """The refusal for a settings document past a limit of the parser, named by `label`: a kind of
    its own, for the reason `ParserLimitError` gives."""
    return lambda clause: ParserLimitError(f"{label} {clause}")


def settings_object(document: str, label: str = _DOCUMENT) -> dict[str, Any]:
    """The document as an object, and empty text as an empty one. `label` names it in every refusal.

    Public because `attach` reads the settings file it merges into, and the overlay's files it
    merges from, by this rule: one reader, so a document the engine would refuse is one `attach`
    refuses too.
    """
    if not document.strip():
        return {}
    # A settings document may be one a clone committed, so valid JSON past the parser's reach is
    # refused rather than left to escape the callers that catch the refusal, and refused as a
    # limit and not as a shape: see `ParserLimitError`.
    return json_object(document, label, error=EntriesError, limit=_past_parser(label))


def settings_text(raw: dict[str, Any], label: str = _DOCUMENT) -> str:
    """A settings document `settings_object` read, as the indented text written back, or the
    refusal `settings_object` gives a document nested past the parser: see `jsonobject.json_text`
    for why one the parser read can still be too deep to write. Public for `attach`, which writes
    the settings file it merges into by this rule too."""
    return json_text(
        raw,
        label,
        error=EntriesError,
        limit=_past_parser(label),
        indent=2,
    )
