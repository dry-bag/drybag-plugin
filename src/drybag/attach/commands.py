"""The `attach` and `detach` groups: `attach` binds a repository to the overlay, and `detach`
takes back what `attach` wrote and leaves the overlay's record of the binding in place.

Two top-level commands and not one group with two subcommands, because that is how
`docs/cli.md` lists them and the shape the skills already invoke.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from drybag.areas import SubParsers
from drybag.command import ATTACH_CHECK_HELP, command_owner, common_flags, grammar
from drybag.config.schema import NAME_RULE, PROJECT_NAME
from drybag.printed import clipped, quoted
from drybag.result import Result

if TYPE_CHECKING:
    from drybag.attach.write import Attached


def _target(args: argparse.Namespace) -> tuple[Path, Path | None, Path | None]:
    """The root, `--store` where it was given, which is otherwise this project's own directory in
    the overlay the machine records, and the machine file."""
    machine = Path(args.machine) if args.machine else None
    store = Path(args.store) if args.store is not None else None
    return Path(args.root).resolve(), store, machine


def run_attach(args: argparse.Namespace) -> Result:
    from drybag.attach.check import check
    from drybag.attach.write import attach
    from drybag.runner import subprocess_runner

    root, store, machine = _target(args)
    if args.check:
        return check(
            root, store=store, machine=machine, owner=command_owner(machine), project=args.project
        )
    attached = attach(
        root,
        store=store,
        machine=machine,
        runner=subprocess_runner(),
        owner=command_owner(machine),
        project=args.project,
    )
    data = {
        "settings_written": attached.settings_written,
        "rules_written": list(attached.rules_written),
        "binding_recorded": attached.binding_recorded,
        # The binding id's first eight characters, the only form of it ever printed: the whole id
        # is the secret a delivery's names carry.
        "binding": attached.binding,
        "notes": list(attached.notes),
        # The executable diff and the allow rules added, as the screen below names them. Each is
        # the overlay's, the owner's own, so it may be shown; `origin` is the repository's and is
        # not here, as no remote is in any `--json` of this area.
        **{
            f"{kind}_{side}": list(getattr(getattr(attached, side), kind))
            for side in ("added", "removed")
            for kind in ("hooks", "skills", "agents")
        },
        "allow_added": list(attached.allow_added),
        "allow_withdrawn": list(attached.allow_withdrawn),
        "rules_left": list(attached.rules_left),
    }
    summary = "; ".join(
        (
            f"attached: {len(attached.rules_written)} Codex rule file(s)",
            "settings merged" if attached.settings_written else "settings unchanged",
            f"binding {attached.binding} "
            + ("recorded" if attached.binding_recorded else "already recorded"),
            *attached.notes,
        )
    )
    return Result("\n".join((summary, *_screen(attached))), data)


def _screen(attached: Attached) -> list[str]:
    """The binding, then what will run that did not before and what no longer will, then the
    allow rules added and withdrawn and the Codex copies it left: one screen, said as the run
    proceeds, since the overlay is the owner's and what it grants is shown rather than gated. The
    binding line names the project it is recorded under: the owner reading which of their projects
    is delivered here is what the deleted `--trust-remote` and `--yes` were replaced by. Every name
    prints through `printed`: the project and the remote are the repository's, and a hook's command
    or an allow rule holds spaces and may hold anything."""
    origin = "no origin remote" if attached.origin is None else f"origin {quoted(attached.origin)}"
    lines = [f"binding {attached.binding}: project {quoted(attached.project)}, {origin}"]
    for side in ("added", "removed"):
        carried = getattr(attached, side)
        lines += [
            f"hook entry {side}: {quoted(event)} {clipped(command)}"
            for event, command in carried.hooks
        ]
        lines += [f"skill {side}: {quoted(name)}" for name in carried.skills]
        lines += [f"agent {side}: {quoted(name)}" for name in carried.agents]
    lines += [f"allow rule added: {clipped(rule)}" for rule in attached.allow_added]
    lines += [f"allow rule withdrawn: {clipped(rule)}" for rule in attached.allow_withdrawn]
    lines += [
        f"Codex rule copy no longer in the overlay: {quoted(rule)} (keep or delete it; `drybag "
        "detach` removes it only while it is untracked and holds the bytes attach wrote)"
        for rule in attached.rules_left
    ]
    return lines


def run_detach(args: argparse.Namespace) -> Result:
    from drybag.attach.write import detach
    from drybag.runner import subprocess_runner

    removed = detach(Path(args.root).resolve(), runner=subprocess_runner())
    data = {
        # Counts and not the strings, which is the same ruling `attach.check` makes about
        # `already_present` and this module makes about `project.name` two functions up — and it
        # has to be, or the three are inconsistent again. Every one of these three lists is read
        # out of the ledger or out of `.claude/settings.local.json`, and the settings file is a
        # path a clone can commit: `.gitignore` does not untrack a committed file. So they are
        # treated as repository-authored, `--json` is what `skills/attach/SKILL.md` relays to the
        # user, and a marker id **bounded by a grammar** was already refused here on exactly this
        # reasoning. An allow rule and a settings key are less bounded than that, not more.
        "allow_removed": len(removed.allow_removed),
        "rules_removed": len(removed.rules_removed),
        "settings_keys_removed": len(removed.settings_keys_removed),
        # A count of `CREATED_DIRS`, drybag's own closed vocabulary.
        "directories_removed": len(removed.directories_removed),
        # Named as `attach` names a copy: the overlay's own file names.
        "rules_left": list(removed.rules_left),
        # A boolean this command computed, so it prints.
        "exclude_block_removed": removed.exclude_block_removed,
        # A boolean this command computed, so it prints.
        "delivery_removed": removed.delivery_removed,
        # A boolean this command computed, so it prints.
        "store_kept": removed.store_kept,
    }
    withdrawn = "; the overlay's plugin was withdrawn" if removed.delivery_removed else ""
    if removed.store_kept:
        withdrawn += (
            "; the composed memory store under this machine's data directory was kept, its links "
            "taken, since it holds files the harness wrote"
        )
    line = (
        f"detached: {len(removed.allow_removed)} allow rule(s), "
        f"{len(removed.rules_removed)} Codex rule file(s), "
        f"{len(removed.directories_removed)} directory(ies){withdrawn}; "
        f"the binding record was left in place"
    )
    left = (
        f"Codex rule copy left: {quoted(rule)} (it does not hold the bytes attach wrote)"
        for rule in removed.rules_left
    )
    return Result("\n".join((line, *left)), data)


def register(groups: SubParsers) -> None:
    attach = common_flags(
        groups.add_parser(
            "attach", help="bind this repository to the overlay and point the harness at its notes"
        ),
        store=True,
    )
    attach.add_argument("--check", action="store_true", help=ATTACH_CHECK_HELP)
    # Held to `[project] name`'s rule before anything runs: the name becomes
    # a directory under the overlay's `projects/`, and the refusal states the rule, never the value.
    attach.add_argument(
        "--project",
        type=grammar(PROJECT_NAME, f"not {NAME_RULE}"),
        default=None,
        help="the overlay project to attach a repository with no drybag.toml to",
    )
    attach.set_defaults(func=run_attach)

    # `--machine` is taken as every project command takes it (docs/cli.md, "Shared flags"), and
    # read by nothing here: every path `detach` withdraws is the ledger's or derived from the id.
    detach = common_flags(
        groups.add_parser("detach", help="remove what attach added, and leave the binding")
    )
    detach.set_defaults(func=run_detach)
