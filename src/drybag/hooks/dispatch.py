"""Run the handlers registered for an event and own the exit code and output shape."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from drybag.gitenv import checkout_root
from drybag.harnesses import Harness, project_root_variables
from drybag.hooks.api import Handler, HookEvent, HookResult, Sink, first_set

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig

TRUNCATION_MARK = "\n[drybag: context truncated to the platform cap]"
# The one event whose answer a person sees as well as the model, and so the one a session's notice
# is rendered on. Measured on Claude Code 2.1.296 (macOS, 2026-10-16): a terminal shows the person a
# `SessionStart` hook's `systemMessage`, and the model reads its `additionalContext` and never its
# `systemMessage`; the desktop app's display was not observed.
NOTICE_EVENT = "SessionStart"


@dataclass(frozen=True)
class Outcome:
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class Recorder:
    """A sink that keeps its records and markers in memory; the tests use it."""

    records: list[dict[str, object]] = field(default_factory=list)
    marks: set[str] = field(default_factory=set)

    def diagnostic(self, record: dict[str, object]) -> None:
        self.records.append(record)

    def seen(self, key: str) -> bool:
        return key in self.marks

    def mark(self, key: str) -> None:
        self.marks.add(key)


def read_event(payload: dict[str, Any], env: Mapping[str, str]) -> HookEvent:
    """The one reading of a hook's stdin, whichever harness sent it, and never told which
    (`harnesses.detect` says why).

    The project root decides which configuration loads, and so what a guard says: it is the
    first non-empty variable among every registered harness's, asked in
    `harnesses.project_root_variables`' order, and without one the checkout `cwd` sits in
    (`gitenv.checkout_root`). Codex names none (measured in the spike record,
    `docs/plans/2026-09-05-agent-harness-p0-spikes.md`, in its *Codex plugin hooks* trial), so
    under Codex an inherited `CLAUDE_PROJECT_DIR` still names the root, as it names the directory
    `hooks/run-hook.sh` enters.
    """
    cwd = Path(str(payload.get("cwd") or "."))
    named = first_set(env, project_root_variables())
    tool_input = payload.get("tool_input") or {}
    session_id = payload.get("session_id")
    agent_id = payload.get("agent_id")
    tool_name = payload.get("tool_name")
    return HookEvent(
        name=str(payload.get("hook_event_name") or "unknown"),
        session_id=session_id if isinstance(session_id, str) else None,
        agent_id=agent_id if isinstance(agent_id, str) else None,
        tool_name=tool_name if isinstance(tool_name, str) else None,
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        cwd=cwd,
        project_root=checkout_root(cwd) if named is None else Path(named),
        raw=dict(payload),
    )


def _clamp(
    render: Callable[[str, str, str], str], event_name: str, context: str, notice: str, cap: int
) -> str:
    """Fit the emitted string, envelope included, inside the platform's per-hook cap.

    Claude Code caps each hook's output string at 10,000 characters — `additionalContext`,
    `systemMessage` and plain stdout alike — and replaces anything longer with a preview and a
    file path. A bundle truncated to the cap and then wrapped in JSON therefore gets replaced
    while the truncation mark claims it was handled. The envelope's width depends on the
    harness that renders it and on the event name, and JSON escaping widens the context itself,
    so the largest prefix that still fits, as `render` (the detected harness's) shapes it, is
    searched for rather than computed. A cap that leaves no room even for an empty answer
    emits nothing: an over-cap string would be replaced by a preview anyway.

    `notice` is rendered whole on every try, so it is counted inside the cap and never cut; the
    context it also opens loses its tail first. A cap too small for the notice drops it.
    """
    best: str | None = None
    low, high = 0, min(len(context), cap)  # a kept character costs at least one of the cap
    while low <= high:
        keep = (low + high) // 2
        stdout = render(event_name, context[:keep] + TRUNCATION_MARK, notice)
        if len(stdout) <= cap:
            best, low = stdout, keep + 1
        else:
            high = keep - 1
    if best is not None:
        return best
    for bare in (render(event_name, "", notice), render(event_name, "", "")):
        if len(bare) <= cap:
            return bare
    return ""


class _UnrecognisedContext(Exception):
    """A `context` `dispatch` cannot join into the emitted payload.

    `HookResult.context` is typed `str | None`, but nothing stops a dynamically built result
    from carrying something else, and the join that builds the payload happens once, after
    every handler has run — outside every per-handler `try`. Raised inside the per-handler
    `try` instead, so a malformed `context` is that handler failing rather than a `TypeError`
    that takes down the whole dispatch.
    """

    def __init__(self, context: object) -> None:
        super().__init__(f"unrecognised context {context!r}")
        self.context = context


def _failure(exc: BaseException) -> tuple[str, dict[str, object]]:
    """The stderr reason and the sink record one handler's failure earns."""
    if isinstance(exc, _UnrecognisedContext):
        return str(exc), {"error": "unrecognised-context", "context": str(exc.context)}
    return f"{type(exc).__name__}: {exc}", {"error": type(exc).__name__}


def dispatch(
    event: HookEvent,
    handlers: list[Handler],
    session: SessionConfig,
    *,
    harness: Harness,
    sink: Sink,
    cap: int | None = None,
) -> Outcome:
    """Every handler's context, joined, and every handler's failure on stderr: exit 0 always.

    `harness` is the value `harnesses.detect` answered, and it shapes the answer. `sink` and
    `harness` carry no default on purpose: a caller that has no durable sink
    must say so with `NullSink()` and inherit its forgetfulness, and one that has not asked which
    harness it runs under must say which it means, rather than acquire either by omission.

    `session` is what every handler is handed, and its notice is what the session's configuration
    could not give it (`SessionConfig.warnings`), rendered on `NOTICE_EVENT` alone, through both
    of the harness's channels: the person's (`systemMessage`) and, ahead of every handler's
    context, the model's. It counts inside `cap` like the context. On every other event it is
    ignored: a notice on each tool call would repeat itself.
    """
    contexts: list[str] = []
    reasons: list[str] = []
    for handler in handlers:
        if handler.event != event.name:
            continue
        if handler.once_key is not None and sink.seen(handler.once_key):
            continue
        try:
            # Purity is contractual and unenforceable, and handlers run in name order, so an
            # earlier one could blank `tool_input["command"]` under a later one's guard. Each
            # handler gets its own deep copy of the mutable views instead. `raw` is copied once
            # and `tool_input` is taken from that same copy, so the view keeps the aliasing
            # `read_event` produces (`ev.tool_input is ev.raw["tool_input"]`) rather than
            # diverging under two independent deep copies.
            raw_view = copy.deepcopy(event.raw)
            raw_tool_input = raw_view.get("tool_input")
            tool_input_view = (
                raw_tool_input
                if isinstance(raw_tool_input, dict)
                else copy.deepcopy(event.tool_input)
            )
            view = replace(event, tool_input=tool_input_view, raw=raw_view)
            result = handler.run(view, session)
            # Consuming the result belongs inside this `try`: reading `.context` off whatever a
            # handler actually returned used to raise out of `dispatch` entirely, so one later
            # handler's malformed return destroyed every earlier handler's context.
            if not isinstance(result, HookResult):
                raise TypeError(f"returned {type(result).__name__}, not HookResult")
            # Validated here and not only at the join below, which runs once after every handler,
            # outside every per-handler `try`, and would take the whole dispatch down.
            if result.context is not None and not isinstance(result.context, str):
                raise _UnrecognisedContext(result.context)
            # Banked past every check that can discard this result: marking above them spent a
            # once-per-context handler's single delivery on a result that then raised, and an
            # empty result has said nothing, so it spends none.
            if handler.once_key is not None and result.context:
                sink.mark(handler.once_key)
            if result.context:
                contexts.append(result.context)
        except BaseException as exc:  # costs this handler alone, never the call
            reason, record = _failure(exc)
            reasons.append(f"{handler.name}: {reason}")
            sink.diagnostic({"event": event.name, "handler": handler.name, **record})
            continue
    stderr = ("drybag: " + "; ".join(reasons) + "\n") if reasons else ""
    shown = session.warnings if event.name == NOTICE_EVENT else ()
    context = "\n\n".join((*shown, *contexts))
    message = "\n".join(shown)
    stdout = harness.render(event.name, context, message)
    if cap is not None and len(stdout) > cap:
        stdout = _clamp(harness.render, event.name, context, message, cap)
        sink.diagnostic({"event": event.name, "handler": "*", "error": "context-truncated"})
    return Outcome(0, stdout, stderr)
