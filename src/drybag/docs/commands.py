"""The `docs` and `plan` groups."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from drybag import fsops
from drybag.areas import SubParsers
from drybag.command import CHECK_HELP, common_flags, root_and_config
from drybag.findings import Finding, labels, listed
from drybag.printed import printable
from drybag.result import Result

_OK = "OK: documentation budgets and link targets"
_PLAN_OK = (
    "OK: linted {n} plan(s) — references resolve, steps are non-leading, mutation outcomes are "
    "expectations, Scope/Premise are present"
)


def run_docs_check(args: argparse.Namespace) -> Result:
    from drybag.docs.hygiene import check_budgets, check_links, docs_gate

    root, config = root_and_config(args)
    # No flag runs exactly the enforced set the success line names, which is the `docs` gate
    # itself.
    problems: list[Finding] = []
    if not (args.budgets or args.links):
        problems = docs_gate(root, config)
    if args.budgets:
        problems.extend(check_budgets(root, config))
    if args.links:
        problems.extend(check_links(root, config))
    data = {"findings": [asdict(p) for p in problems]}
    if problems:
        return Result(
            f"FAIL: {len(problems)} documentation problem(s): {labels(problems)}",
            data,
            exit_code=1,
        )
    return Result(_OK, data)


def run_docs_trail(args: argparse.Namespace) -> Result:
    from drybag.config.paths import contained
    from drybag.docs.hygiene import read_document
    from drybag.docs.trail import (
        load_trail,
        rebuild,
        trail_gate,
        undeclared_new_documents,
    )

    root, config = root_and_config(args)
    # The roadmap first, for both: the `trail` gate raises for a missing one, as a gate that
    # cannot judge the tree does, and this command answers it in its own words.
    roadmap = contained(root, config.paths.roadmap)
    if not fsops.is_file(roadmap):
        return Result(f"{config.paths.roadmap} does not exist", {"stale": None}, exit_code=1)
    # `--check` is the `trail` gate itself, answered next, before anything else is read.
    if args.check:
        if not trail_gate(root, config):
            return Result(f"OK: {config.paths.roadmap} trail listing is current", {"stale": False})
        return Result(
            f"{config.paths.roadmap} trail listing is stale; run: drybag docs trail",
            {"stale": True},
            exit_code=1,
        )
    trail = load_trail(root, config)
    current = read_document(roadmap, config.paths.roadmap)
    updated = rebuild(current, root, config, trail)
    written = current != updated
    if written:
        fsops.write_within(root, config.paths.roadmap, updated)
    # After the write, so the listing is never left stale by this report.
    undeclared = undeclared_new_documents(current, updated, trail)
    data = {"written": written, "undeclared": undeclared}
    summary = (
        f"rewrote {config.paths.roadmap}"
        if written
        else f"{config.paths.roadmap} trail listing is current"
    )
    if undeclared:
        # Capped, and the count is every one: `--json`'s `undeclared` names them all.
        return Result(
            f"{summary}; {len(undeclared)} document(s) entered the trail with no declared state "
            "and were listed as delivered — add each to trail.toml and re-run: "
            + listed([printable(row) for row in undeclared]),
            data,
            exit_code=1,
        )
    return Result(summary, data)


def run_plan_check(args: argparse.Namespace) -> Result:
    from drybag.docs.plans import BaseUnresolvable, Lint, lint

    root, config = root_and_config(args)
    try:
        result = lint(root, config, plans=[Path(p).resolve() for p in args.paths], base=args.base)
    except BaseUnresolvable as exc:
        # The command's own output, as documented: a finding, exit 1. A gate run reads the same
        # cause as a gate that could not run.
        result = Lint([Finding("base-unresolvable", "", None, str(exc))], [], [])
    linted = [p.relative_to(root).as_posix() for p in result.linted]
    unlinted = [p.relative_to(root).as_posix() for p in result.unlinted]
    data = {
        "findings": [asdict(f) for f in result.findings],
        "linted": linted,
        "unlinted": unlinted,
    }
    if result.findings:
        return Result(
            f"FAIL: {len(result.findings)} plan problem(s): {labels(result.findings)}",
            data,
            exit_code=1,
        )
    summary = _PLAN_OK.format(n=len(linted))
    if unlinted:
        summary += f"; {len(unlinted)} uncommitted plan(s) not linted — name them as PATH arguments"
    return Result(summary, data)


def register(groups: SubParsers) -> None:
    docs = groups.add_parser("docs", help="documentation budgets, links and the design trail")
    docs_sub = docs.add_subparsers(dest="command", metavar="<command>")
    check = common_flags(docs_sub.add_parser("check", help="budgets and link targets"))
    check.add_argument("--budgets", action="store_true")
    check.add_argument("--links", action="store_true")
    check.set_defaults(func=run_docs_check)
    trail = common_flags(
        docs_sub.add_parser("trail", help="regenerate the design-and-plan trail in the roadmap")
    )
    trail.add_argument("--check", action="store_true", help=CHECK_HELP)
    trail.set_defaults(func=run_docs_trail)

    plan = groups.add_parser("plan", help="implementation-plan lint")
    plan_sub = plan.add_subparsers(dest="command", metavar="<command>")
    lint = common_flags(
        plan_sub.add_parser("check", help="lint the plans a change touches, or the named ones")
    )
    lint.add_argument(
        "--base", default=None, help="base ref (default: refs/remotes/origin/<project.base_branch>)"
    )
    lint.add_argument("paths", nargs="*", help="plans to lint instead of the diff")
    lint.set_defaults(func=run_plan_check)
