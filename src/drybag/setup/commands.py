"""The `setup` command: configure this machine from a preset, once.

One command and not a group with subcommands, the same shape `attach`/`detach` take: `setup`
does one of two things depending on which flags are given (the second is the per-repository
git hook `--git-hooks` installs), and neither is a subcommand of the other because a
subcommand would still need `--root` to make sense of `--git-hooks` while the machine-level
flags stay siblings of it rather than children.

`--home` and `--machine` are not test affordances bolted on afterwards: `CONTRIBUTING.md`
forbids a test from touching the developer's real `~/.claude` or `~/.config/drybag/`, and a
command whose only mode writes to the real ones could not be tested at all. Both default to the
real paths, exactly as `--root` defaults to the current directory elsewhere in this CLI.

`--approval-boundary` is a third thing, and the one that writes nothing: it prints the deny rule and
the sandbox settings that keep an agent's shell out of the trust record's directory, which are the
harness's to enforce and the person's to set (`trust.boundary` says why drybag sets none of
them), so it runs alone.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, cast

from drybag import fsops
from drybag.areas import SubParsers
from drybag.command import HOME_HELP, SETUP_MACHINE_HELP, SETUP_ROOT_HELP, grammar
from drybag.config.overlay import overlay_root
from drybag.errors import Failure, Refusal
from drybag.presets import DEFAULT_PRESET
from drybag.printed import quoted
from drybag.result import Result
from drybag.runner import Runner, subprocess_runner

# Imported at module scope, and not deferred into `run_setup` the way `setup.run.setup` still
# is: a test that wants to keep this command away from a real `claude`/`codex` binary has to
# monkeypatch a name it can see, and `drybag.setup.commands.subprocess_runner` is one hop
# rather than two — `run_setup` calling straight into `drybag.runner`'s own attribute
# left the patch depending on an import this module does not own the shape of. The runner was
# the `overlay` area's when that was written; it has since become the leaf module `runner.py`,
# and this sentence follows it. `commands.py` is never imported by the hook registry (only
# `hooks.py` files are), so a module-scope import here does not reach the clean-interpreter
# `discover()` that `tests/boundaries/test_discovery.py` holds `hooks.py` to.

# `--git-hooks` installs a hook into one repository and does nothing of machine setup's, so a flag
# that asks machine setup to do something has no run to belong to beside it: `--preset` was
# refused, and `--overlay` was dropped, exit 0, the hook installed, for a person who asked for
# both. Refused alike. `--home` and `--machine` say only where machine setup
# would write, ask nothing done, and are ignored here, as `docs/cli.md` says.
_HOOKS_ALONE = (
    "--git-hooks installs a hook into one repository and does nothing of machine setup's, so it "
    "takes no --preset, --overlay, --plugin or --restore; run `drybag setup` with those "
    "separately"
)
# `--uninstall` removes the hook `--git-hooks` installed, and means nothing to machine setup: on
# its own it was dropped and the machine setup ran, exit 0, for a person who asked for a hook to be
# removed. Refused before anything is written, naming the flag that gives it a meaning.
_UNINSTALL_ALONE = (
    "--uninstall removes the commit-message hook and is taken only with --git-hooks; run "
    "`drybag setup --git-hooks --uninstall` to remove it, and nothing was written"
)
# `--root` names the repository `--git-hooks` installs into, and machine setup reads no repository:
# it took the flag, read nothing of it and configured the machine, exit 0, for a person who named a
# repository. Refused before anything is written, naming the flag that gives it a meaning.
_ROOT_ALONE = (
    "--root names the repository --git-hooks installs into, and machine setup reads no "
    "repository; run `drybag setup --git-hooks --root PATH` for the hook, or drop --root, "
    "and nothing was written"
)


# A plugin name as `setup --plugin` takes it, joined to the preset's marketplace into one argv
# element: led by a letter or a digit, so it cannot be read as an option, and nothing that could
# reach past the name. The rule in words is the parser's refusal, which never quotes the value.
PLUGIN_NAME = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
PLUGIN_RULE = "not a plugin name: lowercase ASCII letters, digits and `-`, led by a letter or digit"


def _root(given: str) -> Path:
    """`--root`, resolved through `fsops.resolved`: a symlink loop there is a refusal naming it,
    in the words `setup`'s questions before its first write use, on every interpreter, where 3.11
    and 3.12 ended in `internal error`."""
    try:
        return fsops.resolved(Path(given))
    except OSError as exc:
        raise Refusal(
            f"--root {quoted(given)} cannot be checked ({fsops.said(exc)}), so nothing was written"
        ) from exc


def run_git_hooks(args: argparse.Namespace) -> Result:
    from drybag.guards.api import Found, install, uninstall

    root = _root(args.root or ".")
    # Each path is git's answer, from a `core.hooksPath` the repository sets or under the name a
    # submodule's `.gitmodules` chose, so each is printed through `printed.quoted`, as the
    # refusals print it; `--json` carries it as it is.
    if args.uninstall:
        removed = uninstall(root)
        hook = quoted(str(removed.path))
        if removed.found is Found.FOREIGN:
            summary = f"left {hook} as it was: it is not drybag's hook; nothing was removed"
        elif removed.found is Found.ABSENT:
            summary = f"there is no drybag hook at {hook}; nothing was removed"
        elif removed.restored is not None:
            summary = f"removed {hook}; restored the foreign hook chained to it"
        else:
            summary = f"removed {hook}; there was no foreign hook to restore"
        data: dict[str, Any] = {
            "path": str(removed.path),
            "restored": str(removed.restored) if removed.restored is not None else None,
            "found": removed.found.value,
        }
        return Result(summary, data)

    installed = install(root)
    hook = quoted(str(installed.path))
    if installed.preserved is not None:
        summary = (
            f"installed the commit-message hook at {hook}; the hook that was there "
            f"is kept at {quoted(str(installed.preserved))} and chained to"
        )
    elif installed.replaced:
        summary = f"reinstalled the commit-message hook at {hook}"
    else:
        summary = f"installed the commit-message hook at {hook}"
    install_data = {
        "path": str(installed.path),
        "preserved": str(installed.preserved) if installed.preserved is not None else None,
        "replaced": installed.replaced,
    }
    return Result(summary, install_data)


# `--home`'s default is `HOME`, and an empty one names no home (`config.machine.owner_home`), where
# `Path.home` answers `/`: this command once ended in an internal error creating `/.config`. Said
# before anything is written.
EMPTY_HOME = (
    "HOME names no home directory, so there is no default for --home or --machine; set HOME "
    "to your home directory, or pass --home PATH and --machine PATH"
)
# `--approval-boundary` writes nothing, so a flag that names something to write has no run to
# belong to: refused rather than dropped, since a person who typed it expects it done. `--home`
# and `--machine` name files too, and are taken: the advice names the user settings
# file and the trust record's directory as they would be written. `--uninstall` was taken and
# dropped, exit 0, for a person who asked for a hook to be removed. `--root` is taken and unread:
# it says only where `--git-hooks` acts, which is refused here.
_ADVICE_ALONE = (
    "--approval-boundary prints what to set and writes nothing, so it takes no --preset, "
    "--overlay, --plugin, --restore, --git-hooks or --uninstall; run `drybag setup` with those "
    "separately"
)
# `--yes` records the overlay's notes as reviewed and `--sync` switches note sync on, each while
# restoring, and neither means anything to any other form of this command: refused before anything
# is written, naming the flag that gives it a meaning.
_RESTORE_ONLY = (
    "{flag} {does} and is taken only with --restore; run `drybag setup --restore {flag}` for "
    "that, and nothing was written"
)
_RESTORE_FLAGS = {"yes": "records the overlay's notes as reviewed", "sync": "switches note sync on"}
# `--restore` restores from an overlay, and with none recorded and none named there is nothing to
# restore from: refused above the first write, naming the flag that gives it one.
_RESTORE_NEEDS_AN_OVERLAY = (
    "--restore restores from an overlay: name it with --overlay PATH, or run `drybag setup "
    "--overlay PATH` first to record one; nothing was written"
)
# What `--approval-boundary` prints: the deny rule over the trust record's directory and the user
# settings file it belongs in, the setting that turns the sandbox on, and the managed settings that
# keep a repository from turning it off (`trust.boundary` says where each was measured).
BOUNDARY_ADVICE = (
    "setup wrote nothing; an approval holds against repository bytes, and against an agent only "
    "while the agent's shell cannot write the trust record, which Claude Code's sandbox stops "
    "and drybag does not check: add {rules} to permissions.deny in {settings} and set "
    "{sandbox} there, and, since a repository's own settings can turn that sandbox off, set "
    "{enforcing} in {managed}, a file only an administrator writes"
)


def run_setup(args: argparse.Namespace) -> Result:
    for flag, does in _RESTORE_FLAGS.items():
        if getattr(args, flag) and not args.restore:
            raise Refusal(_RESTORE_ONLY.format(flag=f"--{flag}", does=does))
    named = (args.preset, args.overlay, args.plugin)
    if args.approval_boundary:
        if args.restore or args.git_hooks or args.uninstall or any(v is not None for v in named):
            raise Refusal(_ADVICE_ALONE)
        return run_boundary_advice(args)
    if args.git_hooks:
        if args.restore or any(value is not None for value in named):
            raise Refusal(_HOOKS_ALONE)
        return run_git_hooks(args)
    if args.uninstall:
        raise Refusal(_UNINSTALL_ALONE)
    if args.root is not None:
        raise Refusal(_ROOT_ALONE)

    from drybag.setup.run import setup

    # The file every *reader* of it reads (`config.loader.load`, `config.overlay.overlay_root`,
    # `trust.record.record_path`), under `HOME` alone. This command once took the `isatty`
    # sniff there: with `XDG_CONFIG_HOME=/xdg`, an owner running `drybag setup` in their own
    # shell wrote `/xdg/drybag/config.toml`, got exit 0, and every reader then said "no overlay
    # root is recorded in the machine configuration; run `drybag setup`". `--machine` itself is
    # still honoured: a path the owner typed on their own command line is not an environment
    # variable a repository can set.
    #
    # Resolved here rather than in `register()`, so that `drybag setup --help` prints the
    # sentence and not whichever home directory the parser happened to be built under.
    home, machine = _home_and_machine(args)
    if args.restore and args.overlay is None and overlay_root(machine) is None:
        raise Refusal(_RESTORE_NEEDS_AN_OVERLAY)
    runner = subprocess_runner()
    report = setup(
        args.preset or DEFAULT_PRESET,
        home=home,
        machine=machine,
        runner=runner,
        overlay=args.overlay,
        plugins=args.plugin or (),
    )
    data = {
        "machine_written": report.machine_written,
        "plugins_installed": list(report.plugins_installed),
        "deny_written": report.deny_written,
        "cli_on_path": report.cli_on_path,
        "overlay": str(report.overlay) if report.overlay is not None else None,
        "notes": list(report.notes),
    }
    if not args.restore:
        return Result(_summary(machine, data), data)
    # The overlay `setup` recorded this run, else the one the machine file records. Never `None`:
    # a run with neither was refused above, and `setup` records one whenever `--overlay` names one.
    overlay = cast(Path, report.overlay or overlay_root(machine))
    return _restored(overlay, data, home=home, machine=machine, runner=runner, args=args)


def _summary(machine: Path, data: dict[str, Any]) -> str:
    """Machine setup's one line, from its `--json` data."""
    return "; ".join(
        (
            f"machine configuration written to {machine}",
            f"{len(data['plugins_installed'])} plugin(s) installed",
            "deny rules merged" if data["deny_written"] else "deny rules unchanged",
            "drybag is on PATH" if data["cli_on_path"] else "drybag is not on PATH",
            f"overlay recorded at {quoted(data['overlay'])}"
            if data["overlay"]
            else "no overlay recorded",
            *data["notes"],
        )
    )


def _restored(
    overlay: Path,
    data: dict[str, Any],
    *,
    home: Path,
    machine: Path,
    runner: Runner,
    args: argparse.Namespace,
) -> Result:
    """`setup --restore` after machine setup ran: the overlay checked and its projects file read,
    either refused naming it; `--sync` written; then setup's summary, holding the plugins restore
    installed and its notes too, one line per listed project, and exit 1 when one was refused or
    failed."""
    from drybag.overlay.api import overlay_fault
    from drybag.setup.machine import write_machine
    from drybag.setup.restore import line, listed_projects, restore

    try:
        if (fault := overlay_fault(overlay)) is not None:
            raise Refusal(fault)
        listed = listed_projects(overlay, home=home)
    except Refusal as exc:
        raise Refusal(f"{exc}; machine setup ran, and nothing was restored") from None
    if args.sync:
        write_machine(machine, personal={}, overlay_root=None, machine={}, sync={"enabled": True})
    done = restore(overlay, listed, home=home, machine=machine, runner=runner, yes=args.yes)
    data["overlay"] = str(overlay)
    data["sync"] = args.sync
    data["plugins_installed"] += done.plugins
    data["notes"] += done.notes
    data["missing"] = list(done.missing)
    data["projects"] = [{**o._asdict(), "dir": str(o.dir)} for o in done.projects]
    lines = [f"missing tools: {', '.join(done.missing)}"] if done.missing else []
    summary = _summary(machine, data) + ("; note sync switched on" if args.sync else "")
    lines += [summary, *(line(outcome) for outcome in done.projects)]
    bad = any(outcome.state in ("refused", "failed") for outcome in done.projects)
    return Result("\n".join(lines), data, exit_code=1 if bad else 0)


def _home_and_machine(args: argparse.Namespace) -> tuple[Path, Path]:
    """The home the user settings file lies under and the machine file, as `--home` and
    `--machine` name them or by default, or the failure for a machine that names neither."""
    from drybag.config.machine import machine_config_path, owner_home

    home = owner_home() if args.home is None else Path(args.home).expanduser()
    machine = machine_config_path() if args.machine is None else Path(args.machine).expanduser()
    if home is None or machine is None:
        raise Failure(EMPTY_HOME)
    return home, machine


def run_boundary_advice(args: argparse.Namespace) -> Result:
    """`setup --approval-boundary`: `BOUNDARY_ADVICE` for the trust record beside the machine file
    and the user settings file under the home, both as `setup` would write them; writes nothing.

    Both are printed absolute: the rule for the reason `record_deny_rules` gives, and the file so
    that a person who opens it from another directory opens the one meant."""
    from drybag.setup.machine import USER_SETTINGS
    from drybag.trust.api import (
        ENFORCING_SETTINGS,
        SANDBOX_ON,
        managed_named,
        record_deny_rules,
        record_path,
    )

    home, machine = _home_and_machine(args)
    # Never `None` beside a machine file, which `_home_and_machine` always names.
    record = cast(Path, record_path(machine))
    try:
        rules = record_deny_rules(record)
    except OSError as exc:
        # `fsops.resolved` meets a symbolic-link loop in the directory as an `OSError` on every
        # interpreter, which would otherwise end in `internal error`.
        raise Refusal(
            f"{quoted(str(record.parent))} cannot be checked ({fsops.said(exc)}), so no rule "
            "for it can be advised"
        ) from exc
    settings = (home / USER_SETTINGS).absolute()
    summary = BOUNDARY_ADVICE.format(
        rules=" and ".join(rules),
        settings=settings,
        sandbox=SANDBOX_ON,
        enforcing=ENFORCING_SETTINGS,
        managed=managed_named(),
    )
    data = {"deny_rules": list(rules), "settings": str(settings), "managed": managed_named()}
    return Result(summary, data)


def register(groups: SubParsers) -> None:

    setup = groups.add_parser("setup", help="configure this machine from a preset")
    setup.add_argument(
        "--preset", default=None, help="the preset to install (default: recommended)"
    )
    # Both defaults are `None` and are resolved in `run_setup`. A path computed here is computed
    # when the parser is built, which is every run of every command — and printed by
    # `drybag setup --help`, where it was the developer's own home directory.
    setup.add_argument("--home", default=None, help=HOME_HELP)
    setup.add_argument("--machine", default=None, help=SETUP_MACHINE_HELP)
    setup.add_argument(
        "--overlay",
        default=None,
        help=(
            "record an existing overlay by path, render one at a path that names nothing, or "
            "create one with create:<owner>/<name> (asks GitHub for a private repository from "
            "the template)"
        ),
    )
    setup.add_argument(
        "--plugin",
        action="append",
        default=None,
        type=grammar(PLUGIN_NAME, PLUGIN_RULE),
        metavar="NAME",
        help=(
            "install this plugin from the marketplace the preset names for each harness; once "
            "per plugin. Without it no plugin is installed"
        ),
    )
    setup.add_argument(
        "--approval-boundary",
        action="store_true",
        help=(
            "print the deny rule and the sandbox settings that keep an agent's shell out of the "
            "trust record's directory, and write nothing"
        ),
    )
    setup.add_argument(
        "--restore",
        action="store_true",
        help=(
            "restore this machine from the overlay its machine file records (or --overlay names): "
            "machine setup, then attach every checkout its machine/projects.toml lists"
        ),
    )
    setup.add_argument(
        "--yes",
        action="store_true",
        help="with --restore, record the overlay's notes for each attached project as reviewed",
    )
    setup.add_argument(
        "--sync",
        action="store_true",
        help="with --restore, switch note sync on in the machine file",
    )
    setup.add_argument(
        "--git-hooks",
        action="store_true",
        help="install the commit-message hook into this repository instead of machine setup",
    )
    setup.add_argument(
        "--uninstall",
        action="store_true",
        help="with --git-hooks, remove the hook and restore what it chained to",
    )
    # `None` and not `.`, so machine setup can tell a `--root` it was handed from one it was not.
    setup.add_argument("--root", default=None, help=SETUP_ROOT_HELP)
    setup.set_defaults(func=run_setup)
