"""`drybag attach --check`: what `attach` would do, read the way `attach` reads it, with nothing
written.

A module of its own, above everything it reads through: the binding and the permission diff
(`binding`, `permissions`), and what the real run asks before its first write, which it asks
through the writer's own planning (`write`). `write` imports `permissions` for the diff it
merges, so the preview cannot live in `permissions` without the two importing each other; here
each import runs one way, and `tests/boundaries/test_layering.py` holds the modules under
`src/drybag/` to a graph without a cycle, imports inside a function included.
"""

from __future__ import annotations

from pathlib import Path

from drybag.attach.binding import Binding, read_binding
from drybag.attach.permissions import PermissionDiff, codex_rules
from drybag.attach.write import configured, gates, main_checkout_of, plan_writes
from drybag.config.owner import Owner
from drybag.printed import printable, quoted
from drybag.result import Result


def check(
    root: Path,
    *,
    store: Path | None,
    machine: Path | None,
    owner: Owner | None = None,
    project: str | None = None,
) -> Result:
    """`attach --check`: the binding and the diff, with nothing written, read in the main checkout
    as `attach` reads it, whichever worktree this runs from.

    A moved `origin` is a note on the line, as the run says it (`Binding.note`), and never a
    finding: the remote binds nothing.

    The `Config` is loaded once and handed to both halves: `read_binding` takes it rather than
    loading a second one. Two loads could disagree, and a `--check` whose two halves read
    different documents is exactly what it exists to rule out. Past the binding it takes the run's
    gates and plans the run's writes (`gates`, `plan_writes`), so a refusal or failure there ends
    this command with the run's code and line: each of them was once left out here, and `--check`
    answered clean over a checkout the run then stopped on. `owner` is the owner's layer the
    command resolved (`config.loader.loads`' own), and `project` is `--project`, which names the
    project of a repository with no `drybag.toml` as the run's does (`write.configured`).
    """
    root = main_checkout_of(root)
    config, source = configured(root, project, machine=machine, owner=owner)
    binding = read_binding(root, store=store, machine=machine, config=config)
    diff = gates(root, config, binding)
    planned = plan_writes(root, config, binding, diff)
    return _report(binding, planned.diff, source)


def _report(binding: Binding, diff: PermissionDiff, source: str) -> Result:
    """The report of a check that met no refusal: the project and where its name came from, the
    binding, the diff and the rule files. The project is a name the repository or the overlay
    chose, so it prints quoted on the line and through `printable` in `--json`."""
    # Named and not merely counted, and on this result rather than in `PermissionDiff`: the
    # diff's fields say what `attach` would add, what is already there and what it would take
    # back, and a file list of another kind would blur what they mean. These names come out of the
    # overlay, so they are the owner's own and may be printed.
    rules = tuple(target for target, _ in codex_rules(binding))
    summary = (
        f"project {quoted(binding.project)} ({source}); "
        f"{binding.state}; "
        f"{len(diff.added_allow)} allow rule(s) would be added, "
        f"{len(diff.already_present)} already present; "
        f"{len(rules)} Codex standing-rule file(s) would be placed"
    )
    if diff.withdrawn_allow:
        summary += f"; {len(diff.withdrawn_allow)} allow rule(s) would be taken back"
    if binding.note is not None:
        summary += f"; {binding.note}"
    if binding.fault is not None:
        summary += f"; {binding.fault}"
    if rules:
        summary += "\n" + "\n".join(f"  {target}" for target in rules)
    data = {
        # The state and the counts, this command's own, and the project with where its name came
        # from (a phrase of `binding`'s own).
        "project": printable(binding.project),
        "project_source": source,
        "state": binding.state,
        "added_allow": list(diff.added_allow),
        # A count and not the strings: this list comes out of the project's own
        # `settings.local.json`, which a clone can commit, so it is repository-authored.
        "already_present": len(diff.already_present),
        "rules_to_write": list(rules),
    }
    return Result(summary, data)
