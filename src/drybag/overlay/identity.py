"""Is this directory an overlay? One answer, for every command that has to ask.

**What an overlay is, for the purpose of recording one.** A tree whose identity record,
`drybag-overlay.toml` at its root (`layout.IDENTITY_RECORD`), parses and names layout 1: the
file the shipped template writes and the one thing every overlay carries, written last by a render
(`layout.OVERLAY_FILES`), so a render cut short is not one. It replaced the plugin manifests an
overlay carried while it was a plugin of its own; an overlay made before the record shipped is
refused with the record to add named in full, since no code migrates one.

It is a *structural* probe and not a security boundary: a repository that wants to look like an
overlay can commit the record. What it buys is that a tree holding something else — a project
directory, the drybag checkout, somebody else's repository, a render cut short — is neither
recorded by `setup` nor written into by `overlay upgrade`. It stops no mistyped path that names
nothing: `setup` renders an overlay there, and says so when the root it records replaces another.

**Nothing a repository authored is quoted back.** The record's bytes come from a directory this
process was pointed at, and a repository is untrusted input (principle 5), so the refusal says
which file failed and what it had to hold, never what it held.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.config.reading import UNPARSEABLE
from drybag.config.schema import PROJECT_NAME
from drybag.errors import Refusal
from drybag.overlay.layout import IDENTITY_RECORD
from drybag.printed import quoted

# One path segment: `config.schema.PROJECT_NAME`, the one name grammar. An owner name and an
# overlay's name become a directory and half a remote path, so each is checked once rather than at
# each of those; the leading class is what keeps a value shaped like an option (`-flag`) out of an
# option's position in an argv.
SEGMENT = PROJECT_NAME
# The one layout this drybag reads. A record naming another was written for a drybag that
# reads a layout this one does not.
LAYOUT = 1
# What the record holds, said in every refusal of a tree without a readable one, so the owner of an
# overlay made before it shipped can add it in one step.
_KEYS = f'`requires = ">=X.Y.Z"` (the drybag it needs) and `layout = {LAYOUT}`'
# `{root}` is the directory judged, through `printed.quoted`.
NO_RECORD = (
    f"{{root}} carries no {IDENTITY_RECORD}, the record that names a tree a drybag overlay; "
    f"add it at the overlay's root, holding {_KEYS}"
)
NOT_A_RECORD = (
    f"{{root}} carries a {IDENTITY_RECORD} that is not a layout-{LAYOUT} record this drybag "
    f"reads; it has to parse as TOML and hold {_KEYS}"
)


def segment(label: str, value: str) -> str:
    """`value` if it is one path segment, or a `Refusal` naming what it was for."""
    if not SEGMENT.match(value):
        raise Refusal(
            f"{label} {value!r} is not one path segment matching {SEGMENT.pattern}; it becomes a "
            "directory name and half a remote path, so it is refused rather than quoted"
        )
    return value


def identity_record(root: Path) -> dict[str, Any] | None:
    """The identity record at `root` as a TOML table, or `None` for one that cannot be read.

    Read through `fsops.read_regular_text`, a regular file only and to the read cap, so a FIFO
    there cannot block `requires_of` on the SessionStart hook path; and parsed with every error a
    valid document can still raise (`config.reading.UNPARSEABLE`) answered as unreadable.
    """
    try:
        return tomllib.loads(fsops.read_regular_text(root / IDENTITY_RECORD))
    except (OSError, UnicodeDecodeError, *UNPARSEABLE):
        return None


def overlay_fault(root: Path) -> str | None:
    """Why `root` is not an overlay root, or `None` when it is one.

    Every string this returns is this module's own around a path the caller typed, printed through
    `printed.quoted`: typed, it can still be resolved through a link a repository commits, which
    spells it in the repository's bytes. The record's own bytes never reach it.
    """
    shown = quoted(str(root))
    if not fsops.is_dir(root):
        return f"{shown} is not a directory"
    if not fsops.is_file(root / IDENTITY_RECORD):
        return NO_RECORD.format(root=shown)
    record = identity_record(root)
    layout = None if record is None else record.get("layout")
    # `type` and not `isinstance`: `True` is an `int` equal to 1, and names no layout.
    if type(layout) is not int or layout != LAYOUT:
        return NOT_A_RECORD.format(root=shown)
    return None


def require_overlay(root: Path, *, because: str) -> None:
    """Refuse unless `root` is an overlay. `because` says what the caller was about to do."""
    fault = overlay_fault(root)
    if fault is not None:
        raise Refusal(f"{fault}; {because}")
