"""Decide, then write.

`plan` reads and decides; `apply` writes. The split is what makes `--dry-run` honest — the
report a user approves is produced by the same code path that then runs — and it is what makes
the containment rule testable without a filesystem full of traps.

Two rules are easy to state and easy to get backwards, so they are stated here once.

*A file drybag never wrote is somebody's*, and the manifest's record is how a refresh tells
the two apart.

*A refusal is an artifact's, not the plan's.* A file that cannot be read is recorded in
`Plan.refusals` and the remaining templates are still decided. `plan` raises only for input no
per-artifact report could rescue: the manifest itself.

*Containment is not a string check.* `contained()` decides whether a path may be written;
`fsops.open_within` decides what is actually written to, by walking the path with `O_NOFOLLOW`
and handing back a descriptor. A component can still become a symlink after `contained()` has
passed — that is the race this layer exists to survive — but it cannot redirect the write: the
walk fails the open rather than following the link, and the string is never resolved a second
time, not for the write and not for the parent directories, which `fsops.mkdirs_within`
creates one component at a time through the same walk rather than with `Path.mkdir`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from drybag import __version__
from drybag.config.paths import PathEscape, contained
from drybag.errors import Refusal
from drybag.fsops import (
    UnsafePath,
    read_regular_text,
    unreadable,
    write_within,
)
from drybag.printed import quoted
from drybag.scaffold.manifest import KIND, Location, Manifest, Record, digest
from drybag.scaffold.model import Action, Applied, Plan, Refused, Template, Verb


def _read(path: Path, relative: str) -> tuple[str | None, str | None]:
    """`(content, reason)`: a reason is a refusal for this one artifact, never for the plan.

    The reason names the file by `relative`, its path from the project root, and says why in
    words (`fsops.unreadable`), as every other refusal here does: the error's own text carries the
    absolute path, this machine's layout, and `path` is a configured target a clone may choose.

    `newline=""` and not `read_text`: universal-newline translation would turn every `\\r\\n`
    and every lone `\\r` into `\\n`, and the digest is of the bytes on disk.

    To the read cap, a regular file only (`fsops.read_regular_text`): the file is one a clone
    commits, so one past the cap, or a FIFO a local process left there, is this artifact's
    refusal rather than a read to its end or a wait on a writer.
    """
    try:
        return read_regular_text(path, newline=""), None
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, unreadable(quoted(relative), exc)


def plan(root: Path, templates: Sequence[Template]) -> Plan:
    """What applying `templates` would do, decided and not yet written. A record is stamped with
    the running drybag's version."""
    manifest = Manifest.read(root)
    resolved_root = root.resolve()
    actions: list[Action] = []
    refusals: list[Refused] = []
    unchanged: list[str] = []

    for template in templates:
        target = template.target
        try:
            path = contained(root, target, resolved_root=resolved_root)
        except PathEscape as exc:
            refusals.append(Refused(template.id, target, str(exc)))
            continue

        record = manifest.get(template.id)
        current, reason = _read(path, target)
        if reason is not None:
            refusals.append(Refused(template.id, target, reason))
            continue

        body = template.render()
        new_record = Record(
            id=template.id,
            kind=KIND,
            location=Location.REPO,
            target=target,
            template=template.source,
            version=__version__,
            sha256=digest(body),
        )
        if current is None:
            actions.append(Action(Verb.CREATE, template.id, target, body, "new", new_record))
            continue

        if digest(current) == digest(body):
            unchanged.append(template.id)
            continue
        if record is None:
            # A whole file drybag never wrote is somebody's.
            actions.append(
                Action(
                    Verb.SKIP_MODIFIED,
                    template.id,
                    target,
                    None,
                    "exists and drybag did not write it",
                    None,
                )
            )
            continue
        if digest(current) != record.sha256:
            actions.append(
                Action(Verb.SKIP_MODIFIED, template.id, target, None, "hand-edited", None)
            )
            continue
        actions.append(Action(Verb.UPDATE, template.id, target, body, "refreshed", new_record))

    # The conversion to tuples happens here, at the boundary, so the object a user approves
    # through `render_report` is the object `apply` consumes. See `Plan`.
    return Plan(actions=tuple(actions), refusals=tuple(refusals), unchanged=tuple(unchanged))


def apply(root: Path, planned: Plan) -> Applied:
    """Write what `plan` decided, or refuse the whole plan.

    `apply` takes the `Plan` rather than a bare action list: a refusal is a property of the plan.
    A caller that wants the good half of a refused plan re-plans without the offending templates.
    """
    if planned.refusals:
        detail = "; ".join(f"{r.artifact_id}: {r.reason}" for r in planned.refusals)
        raise Refusal(f"{len(planned.refusals)} path(s) refused: {detail}")

    manifest = Manifest.read(root) if any(a.record for a in planned.actions) else None
    resolved_root = root.resolve()
    written: list[str] = []
    skipped: list[str] = []

    # The manifest, where the plan records anything, records what is on disk, so it is persisted
    # for the actions that ran even when a later one refuses. Discarding it would leave a file
    # drybag wrote carrying no record, which every later run reads as somebody else's:
    # `skip_modified` under a false reason. The refusal still propagates; only the loop is
    # wrapped, because the refusal above it has written nothing to record.
    try:
        for action in planned.actions:
            if action.payload is None:
                skipped.append(action.target)
                continue
            # Re-validate the string against the live tree, then stop using the string: the
            # write happens through a descriptor `open_within` walked with O_NOFOLLOW.
            contained(root, action.target, resolved_root=resolved_root)
            _write(root, action.target, action.payload)
            written.append(action.target)
            if manifest is not None and action.record is not None:
                manifest = manifest.with_record(action.record)
    finally:
        if manifest is not None:
            manifest.write(root)

    return Applied(written=tuple(written), skipped=tuple(skipped))


def _write(root: Path, target: str, payload: str) -> None:
    """Create the parents and replace the file, or refuse — never raise a bare `OSError`.

    The walk, the parent creation and the atomic replacement are `fsops.write_within`; what is
    this area's is the translation below. That split is deliberate: `attach`, `setup`,
    `overlay` and the `hooks` area's sink all write files that are not `Template`s, and the half
    they need is the walk, not this area's verdict vocabulary.

    The second clause is what keeps the refusal exit code, 2, reachable. A user who saves a file
    where a directory component belongs while reading the dry-run report, then confirms, would
    otherwise get a traceback instead of a refusal, and no race is needed for that.
    """
    try:
        write_within(root, target, payload)
    except UnsafePath as exc:
        raise Refusal(str(exc)) from exc
    except OSError as exc:
        raise Refusal(f"{target} cannot be written: {exc}") from exc
