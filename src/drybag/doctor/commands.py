"""The `doctor` command: report an installation, and exit non-zero when one is broken.

One command and not a group, the shape `docs/cli.md` documents and the shape the skill already
invokes.

**A `skip` is not a finding.** A check this build cannot answer at all — the hash Codex keys hook
trust on, which no spike has measured — skips on every installation, so an exit code that counted
skips would make `doctor` red on every correct one. Every other skip is a state of the machine or
of the repository, and which of those states is the ordinary one can move with things no code
here decides, so nothing
in this module asserts how many rows skip, or which: that census is `docs/cli.md`'s, and whether a
skip carries a remedy is `model.Check`'s rule. Exit 1, the findings exit code, is reserved for
`red`, and `warn` does not reach it either: a budget lowered below the preset is a correct state
somebody should still see.

**The remedies live in `--json` and never in the summary.** Every command's summary is one
line, and a report's remedies do not fit in one; the skill relays each remedy verbatim from the
report, so a remedy absent from `--json` is a remedy the user never sees. The summary renders
through `findings.listed`, which caps at `LISTED_LIMIT`, because an unbounded list of row names
pushes the repairing command off the end of the line.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from drybag.areas import SubParsers
from drybag.command import HOME_HELP, common_flags
from drybag.doctor.checks import run_checks
from drybag.doctor.model import RED, SKIP, WARN, Check
from drybag.findings import listed
from drybag.result import Result


def summarise(checks: list[Check]) -> str:
    """One line: the counts, and the names of whichever status most needs reading.

    Red first, then warn, and nothing when neither: a reader whose every row is green does not
    need every name to say so, and a reader who has one red does not need the warnings in front
    of it.
    """
    red = [check.name for check in checks if check.status == RED]
    warn = [check.name for check in checks if check.status == WARN]
    skipped = [check.name for check in checks if check.status == SKIP]
    counts = f"{len(checks)} checks: {len(red)} red, {len(warn)} warn, {len(skipped)} skipped"
    if red:
        return f"{counts}; red: {listed(red)}"
    if warn:
        return f"{counts}; warn: {listed(warn)}"
    return counts


def run_doctor(args: argparse.Namespace) -> Result:
    root = Path(args.root).resolve()
    checks = run_checks(
        root,
        # `None` means the machine owner's own, said out loud rather than defaulted, for the reason
        # `attach.write.attach` gives: a resolver without one reads the developer's real `~`.
        home=Path(args.home).expanduser() if args.home else None,
        machine=Path(args.machine) if args.machine else None,
    )
    data = {
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "remedy": check.remedy,
            }
            for check in checks
        ]
    }
    findings = sum(1 for check in checks if check.status == RED)
    return Result(summarise(checks), data, exit_code=1 if findings else 0)


def register(groups: SubParsers) -> None:
    doctor = common_flags(groups.add_parser("doctor", help="report on this installation"))
    doctor.add_argument("--home", default=None, help=HOME_HELP)
    doctor.set_defaults(func=run_doctor)
