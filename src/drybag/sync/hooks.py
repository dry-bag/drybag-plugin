"""The note sync's triggers: one handler on each of `SessionStart`, `Stop` and `SessionEnd`.

`Stop` fires on every turn of every session, so with the sync off, as every machine is until its
machine file says `[sync] enabled = true`, a handler reads the switch from the session's resolved
configuration (`Owner.sync`, the machine file the session read once) and returns: no git, no
spawn. A hook process whose environment carries Codex's `PLUGIN_ROOT` (`harnesses.CODEX`, the
environment half of its detection) starts nothing either: Codex reads the same `hooks.json`, and a
repository that sets the variable in a Claude Code session switches that session's triggers off,
never on. Only past both does it import what decides (`sync.trigger`), which loads the note layout
and the job. Every import sits inside the handler body, so discovery stays light
(`tests/boundaries/test_discovery.py`). A handler returns an empty `HookResult` on every path and
lets nothing escape: what it prints carries no `decision`, which on `Stop` would keep the turn
from ending.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

from drybag.hooks.api import Handler, HookEvent, HookResult

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig


def _sync(event: HookEvent, session: SessionConfig) -> HookResult:
    with contextlib.suppress(Exception):
        from drybag.harnesses import CODEX

        if CODEX.detects is not None and CODEX.detects(os.environ, None):
            return HookResult()
        if session.owner.sync.enabled:
            from drybag.sync.trigger import trigger

            trigger(event)
    return HookResult()


def register() -> list[Handler]:
    return [
        Handler(name="note-sync-start", event="SessionStart", run=_sync),
        Handler(name="note-sync-stop", event="Stop", run=_sync),
        Handler(name="note-sync-end", event="SessionEnd", run=_sync),
    ]
