"""Handlers this area contributes; the `hook <event>` entries in `hooks/hooks.json` invoke them.

Every import of `drybag.hints.*` and of the configuration layer happens **inside** a
handler body. `tests/boundaries/test_discovery.py` asserts that `discover()` in a clean
interpreter imports neither the configuration layer nor the presets, and discovery imports every
area's `hooks` module — so a module-level import here reddens a test that belongs to no area at
all.

Nothing a repository controls is put into `HookResult.context`. The restore hint is built from
the command's own text — model- or user-authored, never repository bytes — and the hygiene
notice carries a count and fixed sentences, never a path from `drybag.toml`.

The guard judges with any configuration and none: a repository with no `drybag.toml`, or one
that does not load, is judged as one that loads, because the guard reads nothing of it but its
switch. A repository's `[guards]` sets the switch's default, and the owner's layers override it,
the machine file last (`config.session`). The hygiene note is about the project and is silent
without its configuration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drybag.hooks.api import Handler, HookEvent, HookResult

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig

# The matcher both handlers share. Codex reports every shell-routed action as `Bash` too, as the
# spike record (`docs/plans/2026-09-05-agent-harness-p0-spikes.md`) measured in its *Codex plugin
# hooks* trial, so this one name serves both harnesses.
BASH = "Bash"

# The dispatcher owns the once-per-context bookkeeping, so the handler declares the key
# and stays pure. A red test run over a dirty tree is the normal state of TDD — every "run it to
# watch it fail" step would otherwise carry the same paragraph — so the note is worth one
# appearance and no more.
ONCE_TEST_HYGIENE = "test-hygiene"


def bash_command(event: HookEvent) -> str | None:
    """The command of a Bash call, or None for any other tool or a malformed input."""
    if event.tool_name != BASH:
        return None
    command = event.tool_input.get("command")
    return command if isinstance(command, str) else None


def _bg_cleanup(event: HookEvent, session: SessionConfig) -> HookResult:
    """Judge a Bash call whatever the project's configuration, and none at all included: the guard
    reads nothing of it but the switch: the repository's `[guards]` sets its default, and the
    owner's layers, the overlay's files and the machine file, override it (`SessionConfig.guards`).
    """
    if not session.guards.bg_cleanup:
        return HookResult()
    command = bash_command(event)
    if command is None:
        return HookResult()
    from drybag.hints.bgcleanup import judge

    background = event.tool_input.get("run_in_background") is True
    return HookResult(context=judge(command, background=background))


def _pipe_status(event: HookEvent, session: SessionConfig) -> HookResult:
    """Say that a test run piped into `tail`, `head` or `grep` loses its exit status. Reads the
    command alone, so it speaks with any configuration and none, and it has no switch."""
    command = bash_command(event)
    if command is None:
        return HookResult()
    from drybag.hints.pipes import notice_for

    return HookResult(context=notice_for(command))


def _test_hygiene(event: HookEvent, session: SessionConfig) -> HookResult:
    """Name what could have falsified a red test run. Never a decision: the tool has already run,
    and a failure here costs the note and not the call. About the project, so silent without its
    configuration."""
    config = session.config
    if config is None or event.project_root is None:
        return HookResult()
    command = bash_command(event)
    if command is None:
        return HookResult()
    from drybag.hints.hygiene import context_for, red_exit

    if red_exit(event.raw) is None:
        return HookResult()
    return HookResult(context=context_for(command, event.project_root))


def register() -> list[Handler]:
    return [
        Handler(name="bg-cleanup", event="PreToolUse", run=_bg_cleanup),
        # Once per context: the next piped run repeats a sentence the agent has read.
        Handler(name="pipe-status", event="PreToolUse", run=_pipe_status, once_key="pipe-status"),
        # The red-run note, on both events a failed command can arrive on. Claude Code delivers a
        # Bash command that exited non-zero to `PostToolUseFailure`, with `error` beginning
        # `Exit code N`, and never to `PostToolUse`. No measured harness delivers an exit code on
        # `PostToolUse`: Codex delivers a failed command there with none, so the note does not
        # speak (`red_exit` answers `None`). The registration is a placeholder for parity with
        # Codex. One `once_key`, which the dispatcher files per session: at most one
        # note per context, whichever event carries it.
        # Kept ABOVE the rows it annotates, not below them: a later change appending a handler to
        # the tail of this list would otherwise detach the comment from its subject.
        Handler(
            name="test-hygiene",
            event="PostToolUse",
            run=_test_hygiene,
            once_key=ONCE_TEST_HYGIENE,
        ),
        Handler(
            name="test-hygiene",
            event="PostToolUseFailure",
            run=_test_hygiene,
            once_key=ONCE_TEST_HYGIENE,
        ),
    ]
