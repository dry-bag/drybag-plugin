"""What a session hears about the overlay it is bound to, or not bound to, and the overlay's rules.

`overlay-rules` and `overlay-path-rules` deliver the overlay's rules (`attach.rules`): the standing
ones on every `SessionStart`, and a path-scoped one on the `PostToolUse` the rendered plugin's
entry sends for a `Read`, `Edit` or `Write`. Both answer from the machine's binding record, never
from the repository's configuration, and say nothing for an unbound checkout. The rest of this
docstring is about `overlay-status`.

This area registers it because the question is this area's: `binding_for` says whether a
repository is bound.

Every line is fixed text with at most a count interpolated, but two: the overlay's declared floor
is the one owner-authored string that prints, normalised the way it was validated, and a bound
checkout whose `origin` moved hears both remotes, each through `printed.quoted`
(`memory.store.origin_note`). A repository chooses `project.name`, `memory.groups` and
`paths.memory`, and none of the three appears here — a repository is untrusted input (principle
5), which is also why a store's refusal reason is not used: its text is built out of those same
fields.

**At most once per session (`once_key`), and what it actually costs.** The matcher fires on
`startup`, `resume`, `clear`, `compact` and `fork`, inside an entry whose `timeout` is ten
seconds.

`hooks/dispatch.py` banks a `once_key` only for a result with a context, so the marker is laid
down exactly when this handler had something to say. A session that hears a line hears it once;
a **healthy** repository -- bound, pushed, satisfied -- returns an empty result, banks
nothing, and is asked again on every one of those events.

And it is the healthy path that pays for the `git`, which is the opposite of what this docstring
said for three rounds. `binding_for` asks `origin_remote` on every run, at `gitenv`'s five-second
cap; `overlay_sync`'s `git status` and `git rev-list` are two seconds each and sit under `if not
lines:`, so they are reached exactly when nothing above them found anything wrong. Nine seconds
against the entry's ten, on the repository with nothing to report. The skip is real but it is
the other way round: a repository with a finding pays five seconds and not nine, because the
finding short-circuits the sync.

**So the sync is gated here, per context, rather than by changing what `once_key` means.**
`CONTINUED` says which invocations are one context asking again: on a `compact` the two extra
`git` calls are not paid and the handler answers on what the lines above already know, so a
healthy repository costs five seconds there instead of nine. Every other `source` pays --
`startup`, `resume`, `fork`, `clear`, and a payload carrying none -- so an invocation this module
cannot place in a context is treated as a new one and the nudge is never lost by default.

`resume` was in the set, and it is the case the nudge exists for. `claude --resume` and
`--continue` are a fresh launch of the harness, often days after the conversation they continue:
notes written to the overlay in that conversation are exactly what is uncommitted or unpushed by
then, and a `startup` that found the repository healthy banked no marker to say otherwise. A
`compact` is the same running process asking again within minutes, and it stays gated.
`dispatch.py`'s `once_key` semantics belong to every area's handlers and are untouched; the
five-second `origin_remote` is not gated either, because it is what decides the moved-`origin`
line above, and skipping it would drop that line rather than defer it.

The cost, stated rather than hidden: an overlay that becomes unpushed *during* a session whose
`startup` found nothing to say is not reported on that session's later `compact`; the next
`resume` or `startup` reports it.
`drybag doctor` answers on demand, and the alternative is a pair of `git` calls re-run on every
compaction of a repository with nothing wrong with it.

Every import below the vocabulary is inside the handler body: `tests/boundaries/test_discovery.py`
asserts that discovery in a clean interpreter leaves `drybag.config`, `drybag.presets` and
`drybag.release` out of `sys.modules`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drybag.attach import ATTACH_COMMAND
from drybag.hooks.api import Handler, HookEvent, HookResult

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig

# `SessionStart.source` values that are one context asking again, and the only thing this module
# reads the payload for.
#
# **What the gate rests on, and how much of it is established.** The `once_key` marker is filed
# under the session id -- `hooks/commands.py` builds the sink from `event.session_id` -- so "this
# context has already been asked" is true exactly when the invocation carries the id the marker was
# filed under. `compact` is in the set because it names the running harness continuing one
# conversation; `startup` and `fork` begin one and are out of it; `resume` is out of it because it
# is a new launch, often days later, over an overlay the conversation it continues may have left
# dirty; `clear` is out of it because a fresh conversation may carry a session id this handler has
# never answered for.
#
# **Those last two are assumptions about the harness, and this repository establishes neither.**
# `session_id` is read and passed through untouched (`hooks/dispatch.py` types it `str | None` and
# interprets nothing), no fixture here drives a real `/clear` or a real resume, and no field
# documented to this code says how ids are allocated. What they cost if they are wrong is not
# symmetric, which is why the set is drawn this way: if a `compact` arrived under a *new* id, the
# gate would skip the sync for a context that had never been asked and would lose the nudge; if a
# `clear` or a `resume` keeps the id, the whole cost is two `git` calls re-paid. So the set
# is as small as the saving allows, and a value not in it -- including a payload with no `source` at
# all -- pays: losing four seconds is recoverable and losing the one nudge a context gets is not.
# Whoever can measure the harness should replace this paragraph with the answer.
#
# The value is the harness's, it is compared against this constant, and it is never printed
# -- so nothing here touches the rule about what may reach `additionalContext`.
CONTINUED = frozenset({"compact"})
NO_OVERLAY = (
    "drybag: memory.mode is overlay and this machine records no overlay; "
    "run `drybag setup --preset recommended --overlay <path>`"
)
NOT_ASKABLE = "drybag: the overlay binding could not be checked on this machine"
NOT_ATTACHED = (
    "drybag: this repository is not attached to the overlay this machine records; "
    f"run `{ATTACH_COMMAND} --check`"
)
# What prefixes a bound checkout's moved-`origin` note (`memory.api.origin_note`), the one line
# here that names repository-authored bytes, each through `printed.quoted`.
PREFIX = "drybag: "
REQUIRES_UNREADABLE = (
    "drybag: the overlay's `requires` is not a form this drybag reads; `drybag doctor` says which"
)
REQUIRES = (
    "drybag: the overlay requires drybag {spec} and {running} is running; "
    "install a drybag that satisfies it"
)
# "Could not be counted" rather than "has no upstream": `ahead` is `None` for any non-zero or
# non-numeric answer from `rev-list @{upstream}..HEAD`, which is also a detached HEAD and a git
# that ran out of time. The missing upstream is named as the likely cause, not as the finding.
NO_UPSTREAM = (
    "drybag: the overlay's unpushed commits could not be counted — most often its branch has "
    "no upstream, and then nothing backs it up; it has {dirty} uncommitted change(s). Push it "
    "with -u"
)
UNPUSHED = (
    "drybag: the overlay has {ahead} unpushed commit(s) and {dirty} uncommitted change(s); "
    "push it so the other machine sees them"
)
# The two lines above are mutually exclusive, and that is the fix rather than a tidying.
# `Sync.ahead` is `None` when `git rev-list @{upstream}..HEAD` could not be answered, which for a
# branch with no upstream means *every* commit on it is unpushed -- the count is unknown, not
# zero. The sink read `if sync.ahead is None` and then, on the next `if`, formatted
# `ahead=sync.ahead or 0`, so a session with no upstream and a dirty tree heard "nothing backs it
# up" and "0 unpushed commit(s)" in one breath: the second sentence says the opposite of the
# first and is the one carrying a number. `NO_UPSTREAM` carries the dirty count itself so the
# no-upstream case still reports the half that is knowable, and `UNPUSHED` is reached only with
# a real `ahead` to print.


def _asked_before(event: HookEvent) -> bool:
    """Whether this invocation is a context that has already been asked (see `CONTINUED`).

    A module-level function and not an expression inside the handler, so that the one thing this
    module reads off the raw payload is in one place and named.
    """
    source = event.raw.get("source")
    return isinstance(source, str) and source in CONTINUED


def _overlay_status(event: HookEvent, session: SessionConfig) -> HookResult:
    from drybag.config.schema import OVERLAY_MODE

    if event.project_root is None:
        return HookResult()
    config = session.acting
    if config is None or config.memory.mode != OVERLAY_MODE:
        return HookResult()
    try:
        import drybag
        from drybag.attach.binding import binding_for
        from drybag.config.overlay import overlay_root
        from drybag.errors import Failure, Refusal
        from drybag.memory.api import UNBOUND
        from drybag.overlay.api import overlay_sync, requires_of, satisfies

        root = event.project_root
        try:
            overlay = overlay_root(None)
        except Failure:
            return HookResult(context=NOT_ASKABLE)
        if overlay is None:
            return HookResult(context=NO_OVERLAY)
        lines: list[str] = []
        try:
            binding = binding_for(root, config, machine=None)
        except (Failure, Refusal):
            return HookResult(context=NOT_ASKABLE)
        if binding.state == UNBOUND:
            lines.append(NOT_ATTACHED)
        elif binding.note is not None:
            lines.append(PREFIX + binding.note)
        spec = requires_of(overlay)
        if spec is not None:
            verdict = satisfies(spec, drybag.__version__)
            if verdict is None:
                lines.append(REQUIRES_UNREADABLE)
            elif not verdict:
                lines.append(REQUIRES.format(spec=spec, running=drybag.__version__))
        if not lines and not _asked_before(event):
            sync = overlay_sync(overlay)
            if sync.asked:
                if sync.ahead is None:
                    lines.append(NO_UPSTREAM.format(dirty=sync.dirty))
                elif sync.dirty > 0 or sync.ahead > 0:
                    lines.append(UNPUSHED.format(ahead=sync.ahead, dirty=sync.dirty))
        return HookResult(context="\n".join(lines)) if lines else HookResult()
    # An open handler never costs a session.
    except Exception:
        return HookResult()


# The tools whose `PostToolUse` carries a path-scoped rule: the three the rendered plugin's entry
# matches (`attach.render.SCOPED_MATCHER`), each naming its file in `tool_input.file_path`.
SCOPED_TOOLS = frozenset({"Read", "Edit", "Write"})


def _standing_rules(event: HookEvent, session: SessionConfig) -> HookResult:
    """The bound overlay's standing rules (`attach.rules`), on every `SessionStart`: a compacted or
    cleared context has lost them, so there is no `once_key`. Read from the binding record and
    never from the repository's configuration, so neither `memory.mode` nor a configuration that
    did not load decides it."""
    if event.project_root is None:
        return HookResult()
    try:
        from drybag.attach.rules import standing

        return HookResult(context=standing(event.project_root) or None)
    # An open handler never costs a session, as `_overlay_status` says.
    except Exception:
        return HookResult()


def _scoped_rules(event: HookEvent, session: SessionConfig) -> HookResult:
    """The bound overlay's path-scoped rules for the file a `Read`, `Edit` or `Write` touched,
    once per file per session (`attach.rules`); nothing on any other tool."""
    path = event.tool_input.get("file_path")
    if (
        event.project_root is None
        or event.tool_name not in SCOPED_TOOLS
        or not isinstance(path, str)
    ):
        return HookResult()
    try:
        from drybag.attach.rules import scoped_for

        return HookResult(context=scoped_for(event.project_root, path, event.session_id) or None)
    except Exception:
        return HookResult()


def _owner_files(event: HookEvent, session: SessionConfig) -> HookResult:
    """The owner-held files a new linked worktree lacks (`attach.checkout`), placed on every
    `SessionStart` and said when something was placed. Anchored on the binding and never on a
    configuration: the worktree this serves has no `drybag.toml` until this places it, so
    `session.config` and `memory.mode` decide nothing here."""
    if event.project_root is None:
        return HookResult()
    try:
        from drybag.attach.checkout import place, said

        return HookResult(context=said(*place(event.project_root, machine=None)) or None)
    # An open handler never costs a session, as `_overlay_status` says.
    except Exception:
        return HookResult()


def register() -> list[Handler]:
    return [
        Handler(name="owner-files", event="SessionStart", run=_owner_files),
        Handler(
            name="overlay-status",
            event="SessionStart",
            run=_overlay_status,
            once_key="overlay-status",
        ),
        Handler(name="overlay-rules", event="SessionStart", run=_standing_rules),
        Handler(name="overlay-path-rules", event="PostToolUse", run=_scoped_rules),
    ]
