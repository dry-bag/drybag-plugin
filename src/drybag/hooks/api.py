"""The hooks area's import surface — and the one that **defines** rather than re-exports.

Every other area's `api.py` is a list of re-exports whose `__all__` equals what it imports.
This one cannot be, and the difference is structural rather than a lapse: the handler
vocabulary below is what `drybag.hooks.registry`, `drybag.hooks.dispatch`,
`drybag.hooks.sink` and every area's `hooks.py` import, so a name defined in one of those
modules and re-exported here would be an import cycle — `sink.py` imports `Sink` and
`NullSink` from this module, and this module would import the sink's layout back out of it.

So the rule this area follows is the other half of the same rule: **a name two areas share is
defined here.** The four names of the sink's on-disk layout live here for exactly that reason,
and `sink.py` imports them from here like everybody else. CONTRIBUTING records the exception.
Which harness a hook runs under is not vocabulary of this kind, and no handler is told it:
`drybag.harnesses.detect` says why.

**Six names below have no importer outside this area**, and each stays for the reason beside it:

- `EVENTS` is the list a handler's event must come from: `registry.discover` refuses any other,
  and an area that needs a new event adds it here, beside the vocabulary its handlers use.
- `HandlerFn` is `Handler.run`'s type. `Handler` is what `hints/hooks.py` and
  `attach/hooks.py` build, and a consumer that holds one before registering it — a table of
  handlers, a decorator, a test double — cannot annotate the callable without this name.
- `Sink` is the protocol `dispatch.py` takes and `sink.py` implements, and `NullSink` is the
  degradation it falls back to when there is no harness data root. `doctor` reports on the tree
  those two write (`DIRECTORY`, `MARKERS`, `DIAGNOSTICS`), so the layout is published and the
  writer's own shape should be nameable beside it.
- `DATA_ROOT_VARIABLES` is what `data_root` asks, which `sink.py` and `doctor` both call: a
  harness that names its data root in a variable of its own is one more entry there.
- `first_set` is the one rule a hook finds a directory in its environment by: `data_root` asks it
  for the data root and `dispatch.read_event` for the project root. It is defined here because
  hook discovery imports this module and the rule needs neither the harness registry nor the
  configuration layer, which discovery imports neither of.

For this area, removing a name from `__all__` is not a trim in any case: `api.py` defines these
and `tests/test_surfaces.py` holds `DEFINES_ITS_OWN` areas to `imported | defined == __all__`,
so a defined name absent from the list reddens the contract. Trimming one is a decision to move
its definition into a private module, which is a different change with a different argument.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig

__all__ = [
    "DATA_ROOT_VARIABLES",
    "DIAGNOSTICS",
    "DIAGNOSTICS_MAX_BYTES",
    "DIRECTORY",
    "EVENTS",
    "MARKERS",
    "Handler",
    "HandlerFn",
    "HookEvent",
    "HookResult",
    "NullSink",
    "Sink",
    "data_root",
    "first_set",
]


# The eight events the dispatcher carries. An area that needs a ninth adds it here deliberately;
# `registry.discover` refuses anything else, so a handler registered for "PreToolUSe" is a
# loud failure at discovery rather than a guard that never fires and tests that never notice.
# `PostToolUseFailure` is where Claude Code delivers a tool call that failed, a Bash command that
# exited non-zero among them, and never on `PostToolUse`. `Stop` ends each turn and `SessionEnd`
# the session; the note sync starts its job from both.
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SessionEnd",
)


@dataclass(frozen=True)
class HookEvent:
    name: str
    session_id: str | None
    agent_id: str | None
    tool_name: str | None
    tool_input: dict[str, Any]
    cwd: Path
    project_root: Path | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class HookResult:
    context: str | None = None


HandlerFn = Callable[[HookEvent, "SessionConfig"], HookResult]


@dataclass(frozen=True)
class Handler:
    name: str
    event: str
    run: HandlerFn
    # Handlers stay pure `(event, session) -> result`, so a handler that must run once
    # per context declares the marker key and the dispatcher owns the bookkeeping. The session is
    # `config.session.SessionConfig`: a handler about the project answers nothing when its
    # `config` is `None`, and a guard judges with any.
    once_key: str | None = None


# The sink's on-disk layout, which is a contract between two areas rather than one area's
# detail: `drybag.hooks.sink` writes this tree and `drybag.doctor` reports on it. Both used
# to reach for `sink.py` directly — doctor by a private cross-area import it wrote a paragraph
# to excuse — because a re-export could not live here without a cycle. Defining them here costs
# `sink.py` one import and gives doctor the published name it was owed.
DIRECTORY = "drybag"
MARKERS = "markers"
DIAGNOSTICS = "diagnostics.jsonl"
DIAGNOSTICS_MAX_BYTES = 256 * 1024

# The variables a harness names its data root for this plugin in, asked in this order.
# `PLUGIN_DATA` is Codex's name for the same directory: the spike record
# (`docs/plans/2026-09-05-agent-harness-p0-spikes.md`) measured Codex's hook launch setting it
# beside `CLAUDE_PLUGIN_DATA` in its *Codex plugin hooks* trial.
DATA_ROOT_VARIABLES = ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA")


def data_root(env: Mapping[str, str]) -> str | None:
    """The harness's data root as `env` names it, or `None` when no variable names one.

    One rule for the two readers of the tree under it: the sink writes there and `doctor` counts
    what it wrote, so a variable one of them asked and the other did not would hide every record.
    The value is returned as given; whether it is usable is each caller's question.
    """
    return first_set(env, DATA_ROOT_VARIABLES)


def first_set(env: Mapping[str, str], names: Iterable[str]) -> str | None:
    """The value of the first of `names` that `env` sets non-empty, in their order, or `None`.

    An empty variable reads as unset, so the next one is asked rather than an empty path taken.
    """
    for name in names:
        if named := env.get(name):
            return named
    return None


class Sink(Protocol):
    """Where the dispatcher records failures and once-per-context markers."""

    def diagnostic(self, record: dict[str, object]) -> None: ...

    def seen(self, key: str) -> bool: ...

    def mark(self, key: str) -> None: ...


class NullSink:
    """A sink that forgets rather than suppresses.

    `seen()` is always False, so `once_key` degrades from "once per context" to "every
    invocation" and every diagnostic is discarded.

    The durable, session-keyed sink now exists — `drybag.hooks.sink.sink_for` — and this is
    what it answers when there is nowhere to write: no harness data root, or one this process
    cannot write to. So this is the *degradation*, not the shipped default, and a handler whose
    work must genuinely happen once still cannot rely on `once_key` alone, because the
    degradation is a state any machine can be in.
    """

    def diagnostic(self, record: dict[str, object]) -> None:
        return None

    def seen(self, key: str) -> bool:
        return False

    def mark(self, key: str) -> None:
        return None
