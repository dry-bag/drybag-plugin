"""Where the dispatcher's markers and diagnostics survive between invocations.

Two of the three path segments below are payload-controlled — the marker key a handler chose, and
the session id off the hook's stdin — so both are hashed to a fixed-width hex name, and every write
and removal still goes through `fsops`' `O_NOFOLLOW` walk. Two controls rather than one, because
what runs here is not only a write but a `remove_within` loop over a directory listing: one of the
two removals the enumerated-writes rule (CONTRIBUTING.md#enumerated-writes) lets a listing drive,
and only inside this one directory.

Nothing here ever raises at its caller. A hook runs on every tool call, so an unwritable
`${CLAUDE_PLUGIN_DATA}` must cost a lost marker and never a refused Bash command — which is
the degradation `NullSink`'s own docstring already promises, reached here by returning one.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.fsops import (
    UnsafePath,
    append_at,
    open_within,
    remove_within,
    rmdir_within,
    write_within,
)
from drybag.hooks.api import (
    DIAGNOSTICS,
    DIAGNOSTICS_MAX_BYTES,
    DIRECTORY,
    MARKERS,
    NullSink,
    Sink,
    data_root,
)

# `DIRECTORY` (the one directory drybag owns inside the data root the harness handed it),
# `MARKERS`, `DIAGNOSTICS` and `DIAGNOSTICS_MAX_BYTES` are `hooks.api`'s: `doctor` reports on
# this tree and must name it with the same strings that wrote it.
ROTATED = "diagnostics.1.jsonl"
# Written once, by `sink_for`, to find out whether this data root can be written at all. A
# probe rather than a `try` around the first real write: the first real write is a marker, and
# losing it silently is exactly what the sink exists to stop.
PROBE = ".probe"
# Per FIELD, before serialisation. A record holds a stable reason string, not a payload; 2,000
# characters is generous for that and small enough that a pathological handler cannot fill a
# disk one line at a time. Capping the serialised line instead would cut inside whichever field
# sorts first and leave `doctor` a record it cannot parse.
DIAGNOSTIC_FIELD_CHARS = 2_000
# How many sessions' marker directories survive a prune. A marker only has to outlive its own
# session, so fifty is room for many concurrent and recent sessions while the tree stays bounded
# however long a machine runs. A named cap (CONTRIBUTING.md#named-caps); no shipped file changes
# with it.
MARKER_SESSIONS_KEPT = 50
# A session id the payload did not carry. `read_event` types `session_id` as `str | None`, and
# every such invocation used to share one constant segment -- `sha256("")`, a hex pair anything
# can precompute. That made the unkeyed case the one direction a *read* out of this tree could
# be used in: a data root the environment names, plus a payload with no session id, is enough
# to **plant** a marker at a known path and silence a `once_key` handler before it ever runs.
#
# Per process instead, so nothing can be laid down in advance. The cost is that `once_key`
# degrades from "once per context" to "every invocation" for a payload with no session id --
# which is exactly `NullSink`'s own documented degradation, is the abnormal path on both
# harnesses (each names the session in its payload), and loses a notice rather than a guard.
# Erring the other way would mean trusting a name a repository can write.
UNKEYED_SESSION = f"unkeyed:{os.getpid()}:{os.urandom(16).hex()}"


def _segment(value: str) -> str:
    """One payload-controlled string, as one fixed-width path segment.

    `../../escape` as a filename is a write — and a delete — outside the one directory where the
    enumerated-writes rule lets a listing drive a removal. The hash also fixes the length, so a
    value of any size costs one short name.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class DataSink:
    root: Path
    session: str

    def _target(self, key: str) -> str:
        return f"{MARKERS}/{_segment(self.session)}/{_segment(key)}"

    def seen(self, key: str) -> bool:
        """A marker `mark()` could have written, not merely a name that exists.

        `Path.exists()` followed a symlink and counted a directory, so anything able to write
        the data root could silence a `once_key` handler with one `mkdir` -- no content, no
        permissions, no race. A marker is the regular file `write_within` creates and nothing
        else is one, and `lstat` asks without following the last component.
        """
        try:
            info = os.lstat(self.root / self._target(key))
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode)

    def mark(self, key: str) -> None:
        try:
            write_within(self.root, self._target(key), "")
            self._prune()
        except (OSError, UnsafePath):
            return None

    def _prune(self) -> None:
        """Bound the marker tree, newest first.

        Sessions are unbounded in number and a marker is worthless once its session ends, so
        without this the directory grows for the life of the machine. This session's own
        directory was just written, so it is the newest by this ordering and is never the one
        dropped — which is what makes pruning safe to do on the write path.

        `st_mtime_ns` rather than `st_mtime`: the float carries roughly a quarter-microsecond
        at present-day epochs, which is finer than two directories can be created, but the
        integer costs nothing and cannot be argued about.
        """
        base = self.root / MARKERS
        try:
            sessions = sorted(
                (path for path in base.iterdir() if fsops.is_dir(path)),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return None
        for stale in sessions[MARKER_SESSIONS_KEPT:]:
            for child in stale.iterdir():
                remove_within(self.root, f"{MARKERS}/{stale.name}/{child.name}")
            rmdir_within(self.root, f"{MARKERS}/{stale.name}")

    def diagnostic(self, record: dict[str, object]) -> None:
        # The session is capped with everything else, and not merged in past the cap. It comes off
        # the hook's stdin and `read_event` type-checks it as `str` and nothing more, so it is as
        # payload-controlled as any field a handler supplies. A record holds a reason and never a
        # payload (`DIAGNOSTIC_FIELD_CHARS`), and that covers the key this record is filed under as
        # much as it covers the reason string.
        capped = {
            key: value[:DIAGNOSTIC_FIELD_CHARS] if isinstance(value, str) else value
            for key, value in {"session": self.session, **record}.items()
        }
        line = json.dumps(capped, default=str, sort_keys=True)
        payload = (line + "\n").encode("utf-8")
        try:
            with open_within(self.root, DIAGNOSTICS) as (dir_fd, name):
                self._rotate(dir_fd, name, len(payload))
                append_at(dir_fd, name, payload)
        except (OSError, UnsafePath):
            return None

    def _rotate(self, dir_fd: int, name: str, incoming: int) -> None:
        """Keep one generation, both halves on the descriptor the contained walk opened.

        `os.stat` and `os.rename` relative to `dir_fd`, never by path: re-resolving the name
        between the size check and the rename is the window the walk exists to close.
        """
        try:
            size = os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_size
        except FileNotFoundError:
            return None
        if size + incoming <= DIAGNOSTICS_MAX_BYTES:
            return None
        os.rename(name, ROTATED, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def sink_for(session: str | None, env: Mapping[str, str]) -> Sink:
    """A durable sink under the harness's data root, or `NullSink()` when there is not one.

    The root is `hooks.api.data_root`'s, the one rule `doctor` reads the same tree by, so each
    harness's name for it (`PLUGIN_DATA` is Codex's) is asked here as it is there. The data root
    itself belongs to the harness and is not created here; `drybag/` under it is ours, and is
    created by the probe through `write_within`'s contained walk rather than by a
    `mkdir(parents=True)` that would follow a symlink on the way.
    """
    data = data_root(env)
    if not data:
        return NullSink()
    base = Path(data)
    # Absolute, or no sink. The variable reaches a hook through a committed `env` block, and a
    # relative value anchors `write_within`'s contained walk on the process cwd, which for a
    # hook is the checkout: `CLAUDE_PLUGIN_DATA=.` put `drybag/diagnostics.jsonl` inside the
    # repository. The walk still refused traversal and symlinks; where it was anchored was the
    # repository's choice, and that is the half this line takes back.
    if not base.is_absolute():
        return NullSink()
    try:
        write_within(base, f"{DIRECTORY}/{PROBE}", "")
    except (OSError, UnsafePath):
        return NullSink()
    return DataSink(
        root=base / DIRECTORY, session=session if session is not None else UNKEYED_SESSION
    )
