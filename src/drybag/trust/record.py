"""The machine's one trust record: `trust.json`, beside the machine file, as a versioned document.

A version-1 record is a JSON object with two keys, `version` and `approvals`, and each approval
names its kind, its subject, the digest approved and when it was recorded:

    {"approvals": [{"approved_at": "2026-10-16T09:30:00+00:00", "digest": "…",
                    "kind": "memory", "subject": "/home/me/project/.drybag/memory"}],
     "version": 1}

**Absent and unreadable are not the same answer.** An absent file is the ordinary state of a fresh
machine and holds no approval. A file that is there and is not a version-1 record — not JSON, not
UTF-8, a later version, no version, an approval missing a field — refuses with
`UnreadableTrust`, and nothing here overwrites it: it may hold every project's approval on the
machine, and reading it as empty would let the next write persist the loss.

Every write replaces the whole file through `fsops` in one atomic step. No lock is taken: two
writers at once each replace the file whole, the last one wins, and neither leaves it torn.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.config.machine import machine_config_path
from drybag.errors import Failure, Refusal
from drybag.jsonobject import json_object
from drybag.trust.boundary import RUN_OUTSIDE, SANDBOX_REFUSED, sandbox_refused

RECORD = "trust.json"
VERSION = 1
NO_HOME = (
    "HOME names no home directory, so there is no trust record to write; pass --machine PATH to "
    "keep one beside that file, which no hook reads"
)
# The close of the refusals of a record that is there and does not parse.
_HOLDS = "it holds every project's approval on this machine, so nothing here will overwrite it"
_KEPT = f"{_HOLDS} — repair or delete it"
# A record a later drybag wrote is met after a downgrade, where deleting it would destroy the
# later release's approvals: the remedy is the later release.
LATER_VERSION = (
    "{path} is a trust record of a version this drybag does not read, which a later drybag "
    f"wrote; {_HOLDS} — update drybag to read it"
)
NOT_A_RECORD = (
    "{path} is not a version-1 trust record, and nothing here will overwrite it; repair it if it "
    "holds approvals you want to keep, or delete it to start a new record"
)
# A write to the record that was refused the way an agent's sandbox refuses one (`boundary`'s
# docstring): run from an agent's shell, nothing it asks of the record can be written.
SANDBOXED = SANDBOX_REFUSED.format(
    outcome="the trust record was not written",
    directory="its directory",
    remedy=RUN_OUTSIDE.format(command="this command"),
)


@dataclass(frozen=True)
class Approval:
    """One approval: what was approved (`kind`, `subject`), the digest it held, and when it was
    recorded, in ISO 8601."""

    kind: str
    subject: str
    digest: str
    approved_at: str


class SandboxedWrite(Refusal):
    """A write to the record refused as an agent's sandbox refuses one: `SANDBOXED`, exit 2, and
    nothing written, since the write is one atomic replacement."""


class UnreadableTrust(Refusal):
    """`trust.json` exists and is not a version-1 record.

    A refusal and not a `Failure`: a reader answers a record it cannot read as holding no
    approval, and a broken file must never be answered that way twice — once by reporting the
    subject unapproved, and again by the next write overwriting what could not be read. Exit 2 is
    the code a caller may not read as permission.
    """


_FIELDS = frozenset(field.name for field in fields(Approval))


def record_path(machine: Path | None) -> Path | None:
    """`trust.json` beside the machine file `machine`, or beside the one `config.machine` names.

    That one is under `HOME`, never `XDG_CONFIG_HOME`: a file the clone commits can set it for
    direnv, mise or a devcontainer, and a record it chose would be one the repository wrote.
    `None` for a machine with no home: it holds no record, so nothing is approved, and a write
    refuses to invent a place for one.
    """
    base = machine_config_path() if machine is None else machine
    return None if base is None else base.parent / RECORD


def recorded(machine: Path | None) -> dict[tuple[str, str], Approval]:
    """Every approval on this machine by `(kind, subject)`, or `UnreadableTrust` when the record
    is there and cannot be read."""
    path = record_path(machine)
    if path is None or not fsops.is_file(path):
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UnreadableTrust(
            f"{path} cannot be read ({fsops.said(exc)}); refusing to answer about trust or to "
            "overwrite it"
        ) from exc
    except UnicodeDecodeError:
        raise UnreadableTrust(f"{path} is not UTF-8 text; {_KEPT}") from None
    # Through `jsonobject`, whose parser-limit arms a bare `json.loads` lacks: a record nested
    # past the parser or holding an integer longer than it converts is valid JSON that
    # `json.loads` meets with no `JSONDecodeError`. Each sentence is this record's refusal.
    raw = json_object(text, str(path), error=lambda reason: UnreadableTrust(f"{reason}; {_KEPT}"))
    return _approvals(raw, path)


def _approvals(raw: dict[str, Any], path: Path) -> dict[tuple[str, str], Approval]:
    """A version-1 record's approvals, or the refusal for one this drybag cannot read whole."""
    version = raw.get("version")
    # Only an integer above this one is a later drybag's: `0`, `-1`, `"1"` or `true` is no
    # version any release writes, and sending its reader to upgrade would send them nowhere.
    if type(version) is int and version > VERSION:
        raise UnreadableTrust(LATER_VERSION.format(path=path))
    entries = raw.get("approvals")
    if type(version) is not int or version != VERSION:
        raise UnreadableTrust(NOT_A_RECORD.format(path=path))
    if set(raw) != {"version", "approvals"} or not isinstance(entries, list):
        raise UnreadableTrust(NOT_A_RECORD.format(path=path))
    found: dict[tuple[str, str], Approval] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != _FIELDS
            or not all(isinstance(value, str) for value in entry.values())
        ):
            raise UnreadableTrust(NOT_A_RECORD.format(path=path))
        approval = Approval(**entry)
        key = (approval.kind, approval.subject)
        if key in found:
            raise UnreadableTrust(NOT_A_RECORD.format(path=path))
        found[key] = approval
    return found


def approve_all(
    machine: Path | None, kind: str, digests: Mapping[str, str], *, now: datetime
) -> None:
    """Record every subject of `kind` in `digests` as approved at its digest, replacing any
    approval each had, in one write of the record.

    One write, so a kind with several subjects is approved whole or not at all: a write that fails
    leaves the record as it was. Nothing is written for no subject."""
    approvals = recorded(machine)
    if not digests:
        return
    when = now.isoformat(timespec="seconds")
    for subject, digest in digests.items():
        approvals[(kind, subject)] = Approval(kind, subject, digest, when)
    _write(machine, approvals)


def _write(machine: Path | None, approvals: dict[tuple[str, str], Approval]) -> None:
    """Replace the record with `approvals` as version 1, and fail naming its directory when that
    cannot be written.

    There is no second place to put it that a hook would read, so the failure says which
    directory it was rather than ending as an internal error about a temporary file.
    """
    path = record_path(machine)
    if path is None:
        raise Failure(NO_HOME)
    document = {
        "version": VERSION,
        "approvals": [asdict(approvals[key]) for key in sorted(approvals)],
    }
    try:
        # Through a link at `path` (a dotfiles manager's), as `recorded` reads it, so the link
        # stays; a loop there is the `OSError` answered below.
        fsops.write_atomically(
            fsops.resolved(path), json.dumps(document, indent=2, sort_keys=True) + "\n"
        )
    except OSError as exc:
        if sandbox_refused(exc):
            raise SandboxedWrite(SANDBOXED) from None
        linked = fsops.linked_directory(path)
        if linked is not None:
            raise Failure(
                f"{linked} cannot be written ({fsops.said(exc)}), so the trust record cannot be "
                f"kept there, where the link at {path} leads"
            ) from None
        where = (
            "it is under HOME, which is the only place a hook reads a trust record"
            if machine is None
            else "it is beside the machine configuration file --machine names"
        )
        raise Failure(
            f"{path.parent} cannot be written ({fsops.said(exc)}), so the trust record cannot be "
            f"kept there; {where}"
        ) from None
