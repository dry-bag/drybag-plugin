"""The store's hooks: the index each machine renders for itself, and the unreviewed-notes count.

`index-render` renders the index of a checkout bound in overlay mode, on `SessionStart` and on
`SessionEnd`, as `drybag memory index` does: the note sync moves no index, so each machine
renders its own. At `SessionEnd` it harvests the lines the session appended outside the region
into their notes before the sync's handler starts the job (areas run in name order), so those
notes travel in that job; at `SessionStart` it renders what the jobs brought in since. It reads
the session's one configuration (`SessionConfig.acting`), as `unreviewed-notes` does, and says
nothing; a failure costs this handler alone.

`unreviewed-notes` counts the notes the owner has not promoted at their current bytes
(`memory.reviewed`) and says so on `SessionStart` only when that set differs from the one the last
session over the same store was told about, so a store nobody changed costs no line. The set is
remembered as one digest of the notes' resolved paths, in `<data>/unreviewed/`, a file per store
named by a hash of the store's path; it holds no path and no name. The line is fixed text with a
count interpolated, so nothing a note or a repository wrote reaches it.

No `once_key`: a compacted or cleared context asks again, and hears nothing unless the set moved.
Every import below the vocabulary is inside the handler body, for discovery's reason
(`attach/hooks.py` says it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drybag.hooks.api import Handler, HookEvent, HookResult

if TYPE_CHECKING:
    from drybag.config.session import SessionConfig

# Read by the model as well as the person, so it names the owner as the one who promotes: a line
# telling a session to run `promote` would have the agent turn unreviewed notes into standing rules.
UNREVIEWED_COUNT = (
    "drybag: {count} notes are unreviewed; promoting them is the owner's call "
    "(`drybag memory promote`)"
)
# Under the machine's data directory: the digest of the unreviewed set each store last announced.
SEEN = "unreviewed"


def _segment(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _unreviewed(event: HookEvent, session: SessionConfig) -> HookResult:
    from drybag.config.schema import HARNESS_MODE

    if event.project_root is None:
        return HookResult()
    config = session.acting
    if config is None or config.memory.mode == HARNESS_MODE:
        return HookResult()
    try:
        from drybag import fsops
        from drybag.config.paths import data_directory
        from drybag.memory.notes import walk
        from drybag.memory.reviewed import subject, unreviewed
        from drybag.memory.store import resolved, walked_groups

        store = resolved(event.project_root, config, machine=None)[0]
        if store is None:
            return HookResult()
        pending = unreviewed(store.machine, walk(store.path, walked_groups(store, config)).notes)
        now = _segment("\n".join(sorted(subject(note.path) for note in pending)))
        base, name = data_directory() / SEEN, _segment(str(store.path.resolve()))
        try:
            before = fsops.read_regular_text(base / name)
        except (OSError, UnicodeDecodeError):
            before = None
        if before == now:
            return HookResult()
        # The plain-path form for the directory itself, as `attach.rules` writes its own records
        # under the data directory; the record goes through the walk that refuses a symlink. A
        # record that cannot be written costs a repeated line, never the line.
        try:
            base.mkdir(parents=True, exist_ok=True)
            fsops.write_within(base, name, now)
        except (OSError, fsops.UnsafePath):
            pass
        return HookResult(context=UNREVIEWED_COUNT.format(count=len(pending)) if pending else None)
    # An open handler never costs a session.
    except Exception:
        return HookResult()


def _render(event: HookEvent, session: SessionConfig) -> HookResult:
    from drybag.config.schema import OVERLAY_MODE

    config = session.acting
    if event.project_root is None or config is None or config.memory.mode != OVERLAY_MODE:
        return HookResult()
    from drybag.memory.index import render_current
    from drybag.memory.store import resolved

    store = resolved(event.project_root, config, machine=None)[0]
    if store is not None:
        render_current(store, config)
    return HookResult()


def register() -> list[Handler]:
    return [
        Handler(name="index-render", event="SessionStart", run=_render),
        Handler(name="index-render", event="SessionEnd", run=_render),
        Handler(name="unreviewed-notes", event="SessionStart", run=_unreviewed),
    ]
