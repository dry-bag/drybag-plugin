"""This area's rows in `drybag doctor`: `bundles` and `memory`.

Both read the note store, and the store is this area's to resolve. `doctor`'s core
discovers this module by name and asks its rows after its own (CONTRIBUTING.md, "Areas"), so the
core never resolves the store and nothing about it lives in the report's `Context`. The store comes
from the `Answers` (`memory.answers`) this module's `register()` creates, one per report, so both
rows read one answer.

Every import sits inside a function body, as in a `hooks.py`: this module is imported by
discovery, and a module-level import here would be one more thing every `doctor` run loads before
it has asked anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from drybag.config.schema import Config
    from drybag.doctor.api import Context, Contribution, Row
    from drybag.memory.answers import Answers
    from drybag.memory.store import Store

# When a bundle's largest part counts as "reaching the cap", as a fraction of
# `native_caps.hook_output_chars`. `doctor` reports a bundle that does not fit *and* one that
# reaches the cap, and the second needs a threshold that the first does not.
#
# A fraction and not `parts == slots`: what reaches the platform cap is a part, which the fraction
# measures, while a bundle whose last slot holds a short part still has that part's room to grow,
# so that predicate would warn on a correct installation.
# A named cap (CONTRIBUTING.md#named-caps), and the shipped file that changes with it is
# `hooks/hooks.json`, which is where a slot count is raised when this warning turns out to be
# right.
NEARLY_FULL = 0.9


def _bundles(context: Context, config: Config, answers: Answers) -> Row:
    """A bundle whose notes do not fit its slots needs a human, not a wider cap.

    Raising a slot count edits `hooks/hooks.json`, which is a shipped file, so this is reported
    and never repaired. A part already close to the platform cap is the warning before that:
    one more sentence in one note and the bundle needs a slot that does not exist.
    """
    from drybag.doctor.api import OK, RED, SKIP, WARN, Row
    from drybag.findings import listed
    from drybag.memory.bundles import SLOTS, fit, render

    store = answers.store(context)
    if store is None:
        return Row(SKIP, "the note store does not resolve, so no bundle can be built")
    ceiling = config.native_caps.hook_output_chars * NEARLY_FULL
    over: list[str] = []
    full: list[str] = []
    for bundle in SLOTS:
        measured = fit(bundle, store, config)
        if not measured.fits:
            over.append(bundle.value)
            continue
        emitted = [render(bundle, store, config, part=n) for n in range(1, measured.parts + 1)]
        if any(text is not None and len(text) >= ceiling for text in emitted):
            full.append(bundle.value)
    if over:
        return Row(
            RED,
            f"{len(over)} bundle(s) do not fit their session-start slots: {listed(over)}",
            "run `drybag memory inventory`, then shorten or unflag the notes it lists by size",
        )
    if full:
        return Row(
            WARN,
            f"{len(full)} bundle(s) have a part at the platform cap: {listed(full)}",
            "run `drybag memory inventory` to see the notes by size",
        )
    return Row(OK, "every bundle fits its slots")


POINTER = "autoMemoryDirectory"
NO_POINTER = f"no {POINTER} is set, so the harness reads its own default store"
# Information, never a status: a repository can point the harness's memory elsewhere, which is
# the harness's behaviour and not drybag's to stop. The value is never printed: a committed file
# chose it, and the one `attach` writes names the whole binding id.
POINTER_SET = POINTER + " is set by {file}"
DEBRIS = "{count} file(s) in the note store are not notes"
DEBRIS_REMEDY = "run `drybag memory inventory` to see them, and move or delete each one"
WALK_STOPPED = (
    "the walk of the note store stopped after {cap:,} entries, so it cannot say whether the store "
    "holds files that are not notes"
)
WALK_REMEDY = "run `drybag memory inventory` to see what the store holds"


def _pointer(context: Context) -> str:
    """Which settings file sets the harness's `autoMemoryDirectory`, the first in precedence that
    holds the key as text: the checkout's excluded file, the main checkout's (which a linked
    worktree reads too), the committed one, and the machine's, read off the harness registry. A
    file that cannot be read as settings sets nothing here, and the `attached` row says so of the
    one `attach` writes."""
    from drybag import fsops
    from drybag.errors import Failure, Refusal
    from drybag.harnesses import CLAUDE
    from drybag.jsonobject import json_object
    from drybag.memory.store import _registered_worktree

    (local,), (project,) = CLAUDE.local_settings, CLAUDE.settings

    try:
        main = _registered_worktree(context.root)
    except (Failure, Refusal):
        main = None
    files = [(context.root / local, local)]
    if main is not None:
        files.append((main / local, f"the main checkout's {local}"))
    files.append((context.root / project, project))
    if context.home is not None:
        files.append((context.home / project, f"~/{project}"))
    for path, label in files:
        try:
            text = fsops.read_regular_text(path, newline="")
            document = json_object(text, label, error=ValueError)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if isinstance(document.get(POINTER), str):
            return POINTER_SET.format(file=label)
    return NO_POINTER


def _debris(store: Store) -> Row | None:
    """Files in the note store that are not notes, or the walk that stopped, as a warning.

    Counted and not named. A filename in the store is repository-authored in `in-repo` and
    `local-only` mode, so the count is this check's own answer and the remedy names the command
    that lists them. In `in-repo` mode the tree is the repository's too, so the walk lists at most
    `fsops.WALK_ENTRIES` entries, the cap every walk with a "could not tell" answer reads, and past
    it says it could not tell.
    """
    from drybag import fsops
    from drybag.doctor.api import WARN, Row

    found = listed = 0
    for target in store.groups.values():
        for path in target.rglob("*"):
            listed += 1
            if listed > fsops.WALK_ENTRIES:
                return Row(WARN, WALK_STOPPED.format(cap=fsops.WALK_ENTRIES), WALK_REMEDY)
            if fsops.is_file(path) and path.suffix != ".md" and not path.name.startswith("."):
                found += 1
    return Row(WARN, DEBRIS.format(count=found), DEBRIS_REMEDY) if found else None


def _memory(context: Context, answers: Answers) -> Row:
    """The memory this session gets: the active mode and the layer that chose it, the store, the
    settings file that sets the harness's `autoMemoryDirectory`, and the store's unreviewed notes
    and files that are not notes, as counts.

    Runs with no `drybag.toml` too, where memory is the harness's own. Only files that are not
    notes, or a walk that could not finish, warn: every other part is information, a store that
    does not resolve among them, since `memory index` says why and a `local-only` store before its
    first note is an ordinary state.
    """
    from drybag.config.schema import HARNESS_MODE
    from drybag.doctor.api import OK, Row
    from drybag.memory.notes import walk
    from drybag.memory.reviewed import unreviewed
    from drybag.memory.store import walked_groups
    from drybag.printed import printable

    resolution = context.session.resolution
    mode = str(resolution.values.get("memory.mode", HARNESS_MODE))
    setter = (
        f"set by {resolution.said('memory.mode')}"
        if "memory.mode" in resolution.provenance
        else "which no layer sets"
    )
    parts = [f"mode {mode}, {setter}"]
    store = answers.store(context)
    debris = None
    if store is None:
        parts.append(
            "no store: memory is the harness's own"
            if mode == HARNESS_MODE
            else "the store does not resolve; `drybag memory index` says why"
        )
    else:
        try:
            where = printable(store.path.relative_to(context.root).as_posix())
        except ValueError:
            where = "the composed store under the data directory"
        config = context.attach_config
        groups = walked_groups(store, config) if config else []
        pending = len(unreviewed(store.machine, walk(store.path, groups).notes))
        parts.append(f"store {where}, {pending} unreviewed note(s)")
        debris = _debris(store)
    parts.append(_pointer(context))
    if debris is not None:
        return Row(debris.status, "; ".join([*parts, debris.detail]), debris.remedy)
    if store is not None:
        parts.append("the note store holds notes and nothing else")
    return Row(OK, "; ".join(parts))


def register() -> Contribution:
    """This area's two rows, sharing one `Answers`, so the store resolves once for both."""
    from drybag.doctor.api import Contribution, configured
    from drybag.memory.answers import Answers

    answers = Answers()
    return Contribution(
        checks=(
            (
                "bundles",
                configured(lambda context, config: _bundles(context, config, answers), attach=True),
            ),
            ("memory", lambda context: _memory(context, answers)),
        )
    )
