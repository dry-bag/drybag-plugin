"""Every harness drybag serves, as values in one registry.

`settings` names the committed files in which a harness reads hook entries. A harness is a value,
not a class: what this module states about a harness is one more value in `HARNESSES`, and
nothing that reads the registry changes. What it does not state yet is the list below.

The registry is also the adapter the `hooks` area answers through. `drybag hook` asks `detect`
which value it is running under and shapes its stdout with that value's `render`, and nothing
more: `detect` says why. A harness whose payload genuinely differs is a question to answer with
that harness's evidence when it arrives.

A value also states its reach: for each enforcement surface drybag has (`Surface`), the tier at
which that surface holds under this harness (`Tier`), or `None` where no tier is claimed there, and
the evidence each rests on: what was observed, on which version, and where its record is. The
README's table of what each agent enforces is held equal to these values by a test, so a
measurement that moves a tier is one edit here.

A new harness is a value with a positive `detects`, its project-root and plugin-root variables,
its `render`, its settings files and its `reach`. What follows still lives outside the registry,
spelled for Claude Code or Codex, and a harness that differs from both in any of it is an edit
there too. `tests/test_harnesses.py` holds the list to the code: every string in a module of the
package that names a harness is pardoned there, each pardon by one of these items or as prose.

- Each harness loads the plugin through a manifest of its own, `.claude-plugin/plugin.json` and
  `.codex-plugin/plugin.json`, which `scripts/release.py check` holds to one version, so another
  harness is another manifest and another entry in that check's sources.
- `hooks/hooks.json` keys its entries by Claude Code's event names and spells every command as
  `"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.sh"`, so a harness that names its events otherwise, or
  does not set `CLAUDE_PLUGIN_ROOT`, is an edit there: without that variable each command
  expands to `/hooks/run-hook.sh` and exits 127, which Claude Code reads as a non-blocking
  error, so no guard runs and nothing says so.
- `hooks/run-hook.sh` takes the directory it enters from `CLAUDE_PROJECT_DIR`, else from `git`,
  before any Python runs, so another root variable is an edit to its `root=` line.
- `hooks.api.DATA_ROOT_VARIABLES` names the variables the sink and `doctor` find the harness's
  data root by.
- `doctor` runs the wrapper for its `wrapper` row under `CLAUDE`'s two root variables whichever
  harness is in use, with every variable under Claude Code's and Codex's prefixes taken out of its
  environment first; names `CLAUDE_PLUGIN_ROOT` in the remedy of the rows that find no plugin
  root and `${CLAUDE_PLUGIN_DATA}` in the `diagnostics` row's; and names Codex in the `cli-path`
  row's sentence on what it does not substitute.
- `setup` installs the plugin through each harness's own command-line verbs,
  `_MARKETPLACE_ADD` and `_PLUGIN_INSTALL` in `setup/run.py`, keyed by harness name, so another
  harness is another entry in each.
- `setup --restore` installs what the overlay's `machine/claude/settings.json` lists, Claude Code's
  marketplaces and plugins, with Claude Code's verbs, `install_listed` in `setup/restore.py`, so
  another harness is another list and other verbs.
- `attach` renders the overlay's hooks, skills and agents as a Claude Code plugin and installs it
  with Claude Code's verbs, `PLUGIN_MARKETPLACE_ADD_LOCAL` and `PLUGIN_INSTALL_LOCAL` in
  `attach.render`, and `detach` withdraws it with `PLUGIN_UNINSTALL_LOCAL` and
  `PLUGIN_MARKETPLACE_REMOVE` beside them, so another harness is another render and other verbs.
- The overlay spells each harness's half by name: `common/claude` and `common/codex`
  (`overlay.layout`, re-exported by `overlay.api`), and the per-project `claude` and `codex`
  directories, `.codex/rules` and the directories it creates in a project, which `attach` reads
  and writes (`attach.items`, `attach.permissions`, `attach.write`). They are the overlay's layout
  as much as a harness's.
- `memory session-context` writes a bundle to a `SessionStart` entry's stdout as it is, never
  through a `render`, so a harness that reads that event's output in another shape is an edit
  there.
- One hook output cap, `native_caps.hook_output_chars`, is Claude Code's and is applied under
  every harness.
- `commit strip` gives way to Claude Code's `attribution.commit`, read off `CLAUDE.settings` under
  the owner's home (`guards.commit`), and its message names it, so another harness's setting for
  what its commits carry is an edit there.
- The advice `setup --approval-boundary` prints is Claude Code's alone: `trust.boundary` spells
  its managed-settings paths, its sandbox settings and its deny-rule grammar, and names it in the
  one sentence every writer gives a write its sandbox refused. Another harness's sandbox is an
  edit there.
- The hook payload is read in Claude Code's schema, the canonical one (`CANONICAL`): its keys
  (`hook_event_name`, `tool_name`, `tool_input`, `session_id`, `run_in_background`), the tool
  name `Bash` and its event names are spelled across the `hooks` package (`hooks.dispatch`,
  `hooks.api`, `hooks.sink`), in `guards` and in the areas' hook handlers, so a
  harness whose payload differs is an edit to each.

Elsewhere, code that needs a harness fact asks this registry: `doctor` reads the plugin-root
variables off every value; `setup` reads the machine's settings file off `CLAUDE.settings`; and
`attach` merges into the one file `CLAUDE.local_settings` names.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class Surface(StrEnum):
    """Where drybag enforces something, by how it reaches a session."""

    # A hook that adds context and refuses nothing: the background-cleanup hints, the
    # pipe-status hint, the test-hygiene note, the standing rules, the volatile notes.
    NOTICES = "session notices"
    # The documentation, plan, commit and trail checks, run in a project's CI.
    GATES = "repository checks"
    # The skills.
    METHOD = "skills"


class Tier(StrEnum):
    """How strongly a surface holds under a harness."""

    CONTEXT = "context only"
    CI = "CI only"
    INSTRUCTIONS = "instructions only"


@dataclass(frozen=True)
class Reach:
    """One surface under one harness."""

    # `None`: no tier is claimed for the surface under this harness, which the README's table
    # prints as "not verified".
    tier: Tier | None
    # The observation the tier rests on and where its record is: a public record, named, or, for a
    # measurement whose record is not in this repository, the date it was made and that it is not
    # here; or "unmeasured".
    evidence: str
    # The agent and its version the observation was made on, as the README cites it, or `None`
    # where no agent takes part.
    measured_on: str | None = None


@dataclass(frozen=True)
class Harness:
    name: str
    settings: tuple[str, ...]
    # Settings files this harness reads that a repository keeps out of git, which `doctor`
    # walks beside `settings`: they are this machine's.
    local_settings: tuple[str, ...]
    # The variable this harness names the project root in, if any. Every registered value's is
    # asked, in `project_root_variables`' order, whichever harness was detected (`detect`).
    project_dir_env: str | None
    # How the hook's stdout is shaped: (event name, joined context, notice) -> stdout, where the
    # notice is what the person is shown and the context what the model reads. Never a decision:
    # no handler refuses a call.
    render: Callable[[str, str, str], str]
    # Every `Surface`, each with the tier it holds at under this harness. Compared, and left out
    # of the hash: a mapping has none, and the other fields already tell two harnesses apart.
    reach: Mapping[Surface, Reach] = field(hash=False)
    # The variable this harness names the plugin's own root in: where `doctor` looks for the
    # installed plugin when this process cannot name it, and the one it sets when it runs the
    # wrapper. Every harness served names one.
    plugin_root_env: str
    # Positive detection, for every harness but the canonical one, which is the fallback.
    detects: Callable[[Mapping[str, str], Mapping[str, Any] | None], bool] | None = None


def hook_specific_output(event_name: str, context: str, notice: str) -> str:
    """The hook answer: the whole string a hook writes to stdout, which the harness caps.

    A hook with nothing to say answers `{}`, on every event: which events take the envelope is each
    harness's own list, and no copy of one is kept here. `notice` goes out as `systemMessage`, which
    Claude Code shows a person in a terminal on `SessionStart` and never hands the model
    (`hooks.dispatch.NOTICE_EVENT`); the model's copy is the caller's to put into `context`."""
    payload: dict[str, Any] = {}
    if context:
        payload["hookSpecificOutput"] = {"hookEventName": event_name, "additionalContext": context}
    if notice:
        payload["systemMessage"] = notice
    return json.dumps(payload)


def _codex_detects(env: Mapping[str, str], payload: Mapping[str, Any] | None) -> bool:
    """Measured on both harnesses in the *Codex plugin hooks* trial of the spike record
    (`docs/plans/2026-09-05-agent-harness-p0-spikes.md`): Codex sets `PLUGIN_ROOT` and
    `PLUGIN_DATA` and ALSO `CLAUDE_PLUGIN_ROOT`, so Claude Code's names alone identify nothing;
    Codex's `SessionStart` stdin also carries `model` and `permission_mode`, which Claude Code's
    does not. How far either signal can be trusted is `detect`'s to say."""
    if "PLUGIN_ROOT" in env:
        return True
    return payload is not None and {"model", "permission_mode"} <= set(payload)


CLAUDE_DIR = ".claude"
# The gates run in a project's own CI, which runs the same under every agent: no agent takes part.
_IN_CI = Reach(
    Tier.CI, "unmeasured: the checks run in a project's own CI, where no agent takes part"
)
# The records the measurements below are kept in. The delivery spike's plan is not named by its
# path, which carries words this repository keeps out of its code.
_P0_SPIKES = "`docs/plans/2026-09-05-agent-harness-p0-spikes.md`"
_DELIVERY_SPIKE = "the delivery spike of 2026-10-02 in `docs/plans/`"
# What is claimed of Codex's plugin hooks, which both hook surfaces rest on: no tier yet. Codex
# runs a plugin's hooks once they are trusted in `/hooks` (an untrusted one is skipped without a
# message, and `--dangerously-bypass-hook-trust` runs it anyway), and a blocking `PreToolUse`
# hook's reason reached the model; but drybag's own guards and notices have not been verified
# end to end there, so the surfaces stay unclaimed until that is done. Codex's `PostToolUse`
# carries no exit code, so the red-run note is not claimed there either.
_CODEX_HOOKS_UNVERIFIED = Reach(
    None,
    "a plugin hook ran once trusted in `/hooks`, and a blocking `PreToolUse` hook's reason reached "
    "the model (measured on Codex CLI 0.162.0-alpha.17.2 on 2026-10-13; the record of that "
    "measurement is not in this repository); drybag's guards and notices are not verified end "
    "to end there",
    measured_on="Codex CLI 0.162.0-alpha.17.2",
)

CLAUDE = Harness(
    name="claude",
    settings=(f"{CLAUDE_DIR}/settings.json",),
    # The file `attach` merges the overlay's hook entries into.
    local_settings=(f"{CLAUDE_DIR}/settings.local.json",),
    project_dir_env="CLAUDE_PROJECT_DIR",
    render=hook_specific_output,
    reach=MappingProxyType(
        {
            Surface.NOTICES: Reach(
                Tier.CONTEXT,
                "a SessionStart hook's output arrived as context "
                f"({_P0_SPIKES}, the hook output cap trial)",
                measured_on="Claude Code 2.1.261",
            ),
            Surface.GATES: _IN_CI,
            Surface.METHOD: Reach(
                Tier.INSTRUCTIONS,
                f"the plugin's skills were listed ({_DELIVERY_SPIKE})",
                measured_on="Claude Code 2.1.285",
            ),
        }
    ),
    plugin_root_env="CLAUDE_PLUGIN_ROOT",
)
# Codex reads `AGENTS.md` from the root down and no other instruction file, and follows no
# import; `.codex/rules` holds command-execution policy, not instructions. drybag writes no
# instruction file for it.
CODEX = Harness(
    name="codex",
    settings=(".codex/hooks.json",),
    local_settings=(),
    # Codex names the project root in no variable of its own (`read_event` still asks every
    # registered value's, so an inherited `CLAUDE_PROJECT_DIR` names it as it does the wrapper's).
    project_dir_env=None,
    # Claude Code's shape, and unverified under Codex: Codex CLI 0.162.0-alpha.17.2 ran the
    # plugin's hooks once they were trusted (`_CODEX_HOOKS_UNVERIFIED`), but no output this
    # `render` shaped has been checked there end to end. A harness whose output differs is a
    # different value, never a branch inside a shared one, and a `render` of its own needs more
    # than a measured shape: `detect` says what.
    render=hook_specific_output,
    reach=MappingProxyType(
        {
            Surface.NOTICES: _CODEX_HOOKS_UNVERIFIED,
            Surface.GATES: _IN_CI,
            Surface.METHOD: Reach(
                Tier.INSTRUCTIONS,
                "the plugin's skills arrived, and instructions through AGENTS.md "
                f"({_DELIVERY_SPIKE})",
                measured_on="Codex CLI 0.160.0",
            ),
        }
    ),
    detects=_codex_detects,
    # Codex sets Claude Code's name for it too (`_codex_detects`).
    plugin_root_env="PLUGIN_ROOT",
)
# Claude Code's hook schema is the de-facto one, which other harnesses imitate, so it answers
# whatever no other value claims and detects nothing of its own.
CANONICAL = CLAUDE
# The order the README's table and `setup` take the harnesses in.
HARNESSES: tuple[Harness, ...] = (CLAUDE, CODEX)

# The leading UTF-8 byte-order mark Claude Code reads past in its settings files (measured on
# macOS, 2026-10-05, Claude Code 2.1.288, on the project's `.claude/settings.json`).
BYTE_ORDER_MARK = "\ufeff"


def lenient_settings(text: str) -> str:
    """The text of a Claude Code settings file as Claude Code reads it: past a leading UTF-8
    byte-order mark.

    The one reading of that mark for every reader of such a file: `setup`'s merge into the user
    settings file, from the file's bytes as UTF-8, and the commit guard's read of it. Refused, the
    mark stopped `setup` on an owner's own file the harness reads.
    """
    return text.removeprefix(BYTE_ORDER_MARK)


def project_root_variables() -> tuple[str, ...]:
    """The variables a hook's project root is read from, in the order they are asked.

    `CANONICAL`'s first, then every other registered value's in registry order, each once. The
    registry's order is a choice about presentation, so it is not the precedence too: a harness
    registered ahead of Claude Code would otherwise move every Claude Code session's root to a
    variable of its own. Every value's is asked whichever value was detected, as `detect` says.
    """
    ordered = (CANONICAL, *(harness for harness in HARNESSES if harness is not CANONICAL))
    return tuple(dict.fromkeys(h.project_dir_env for h in ordered if h.project_dir_env))


def detect(env: Mapping[str, str], payload: Mapping[str, Any] | None) -> Harness:
    """The first non-canonical harness whose `detects` answers yes, else `CANONICAL`.

    The one answer to "which harness is this process running under": the `hooks` area asks it
    once, in `drybag hook`, and hands the answer to `dispatch` and to nothing else. It always
    answers, because a process no value claims is served the canonical shape.

    **Detection is repository-steerable, so it decides only the shape of stdout.** Neither input
    read here is the harness's alone: a committed `.claude/settings.json` `env` block reaches the
    environment, so it can set `PLUGIN_ROOT`, and the payload pair Codex is told by is fragile,
    because Claude Code's `PreToolUse` stdin already carries `permission_mode` (the spike record
    `_codex_detects` cites, in its `tool_name` trial), so a Claude Code release that adds `model`
    there would be answered as Codex. So what this answers decides nothing a handler sees and
    nothing about whether a call is refused: every payload is read by the one
    `hooks.dispatch.read_event`, into an event that does not say which value was detected; the
    project root, which decides which configuration loads, is the first variable set among every
    registered value's, `CANONICAL`'s first, whichever value was detected
    (`project_root_variables`); and no handler refuses a call under any harness.

    **A harness gets a `render` of its own only together with a detection signal a repository
    cannot steer.** While every value renders through `CANONICAL.render`, what this answers
    changes no byte of stdout either. A value with a `render` of its own would let a repository
    choose the shape its harness receives, and a shape the harness does not read drops every
    notice without a word. So a second shape waits on a signal the wrapper controls, such as its
    argv, and never the environment or stdin; `tests/test_harnesses.py` holds every registered
    value to the canonical `render` until then.
    """
    for harness in HARNESSES:
        if harness.detects is not None and harness.detects(env, payload):
            return harness
    return CANONICAL
