"""The `overlay` group: refresh an overlay after a release.

Creating one is `drybag setup --overlay`'s, and publishing the template it is generated from is
the maintainer's `scripts/publish_template.py`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from drybag.areas import SubParsers
from drybag.command import DRY_RUN_HELP, OVERLAY_ROOT_HELP
from drybag.result import Result
from drybag.scaffold import render_report


def run_overlay_upgrade(args: argparse.Namespace) -> Result:
    from drybag.overlay.upgrade import upgrade

    result = upgrade(Path(args.root).resolve(), dry_run=args.dry_run)
    # `scaffold.render_report` and not a second renderer: the report a user approves has to be
    # the one the same code path then acts on, which is the whole reason the scaffold engine
    # splits plan from apply. The decision list is appended to it rather than folded into it,
    # because it is the one thing the engine has no verb for.
    report = render_report(result.plan)
    if result.decisions:
        report += (
            "\n\nASK FIRST — these can grant a capability, so a matching hash is not consent:\n"
            + "\n".join(f"  {artifact}" for artifact in result.decisions)
        )
    data = {
        "report": report,
        "writes": result.plan.writes,
        "decisions": list(result.decisions),
        "dry_run": args.dry_run,
    }
    # A refused artifact is a finding, not a success: `apply` would raise on the whole plan, and
    # a dry run that printed a REFUSED section under exit 0 would say "nothing to do" about a
    # report that says the opposite.
    return Result(report, data, exit_code=1 if result.plan.refusals else 0)


def register(groups: SubParsers) -> None:
    overlay = groups.add_parser("overlay", help="the owner's private overlay")
    sub = overlay.add_subparsers(dest="command", metavar="<command>")

    upgrade = sub.add_parser("upgrade", help="refresh the files in an overlay nobody has edited")
    upgrade.add_argument("--root", default=".", help=OVERLAY_ROOT_HELP)
    upgrade.add_argument("--dry-run", action="store_true", help=DRY_RUN_HELP)
    upgrade.set_defaults(func=run_overlay_upgrade)
