"""The core's checks, the run that asks them, and the rows the areas contribute after them.

A row of the report is one check's answer under the check's name. The core's checks are `CHECKS`,
and their rows come first, in that order; every other row is an area's, contributed by its own
`doctor.py` (CONTRIBUTING.md, "Areas") and asked after the core's in area-name order. So nothing
here imports an area, and nothing here names or counts the rows an area contributes: which rows a
report has is `docs/cli.md`'s table, and `tests/doctor/test_checks.py`'s literal `REPORT`.
This module is the list and the run: how the areas' rows are found and guarded is
`registry.py`'s.

**A `Check` is not a `Finding`.** `findings.Finding` carries a rule, a path and a line, and its
docstring says the label carries "what the check computed" while the detail "may quote the
repository" and is for `--json`. A doctor row needs a fourth thing neither of those is — a
**remedy**, the command a reader is supposed to run next — and it has no path or line to carry.
Widening `Finding` would reach into three areas that depend on its current shape, so this area
defines its own record and reuses `findings.listed` for the summary line alone.

**What may be printed, and what may not.** Counts, labels, statuses and drybag's own
vocabulary are computed here and print freely. A repository-authored string does not: not
`[drybag] version`, not a hook command. The hook sink holds its diagnostics log
to that line in as many words — reasons, never payloads — and this module holds every other
row to it, as each area's `doctor.py` holds the rows it contributes, where a note's
filename and the reason a store does not resolve are repository-authored too.

**`diagnostics` follows the same ruling.** The sink's log has three fields that are drybag's
own vocabulary *for a log drybag wrote*. The log is found through `${CLAUDE_PLUGIN_DATA}`, so
this process never establishes that, and `error` is free text with no grammar and no cap on the
read side at all, so `diagnostics` prints a count and the reader opens the file. `_diagnostics`
says what quoting would let through.

**A value the environment names is not the same as a value this process chose**, and the two
checks that touch a plugin root say which they have: `plugin_root` is the one a variable named,
`own_root` is the only root anything executes.

**The sink's layout is read from `drybag.hooks.api`, where it is defined.** `DIRECTORY`,
`MARKERS`, `DIAGNOSTICS` and `DIAGNOSTICS_MAX_BYTES` are four strings this area and the hook area
must agree on, so they are shared vocabulary, *defined* on the hook area's surface, which
`sink.py` imports too — a re-export from `sink.py` would be a cycle, since `sink.py` imports
`api.py`. So this module imports another area through its published surface, as every module
does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import drybag
from drybag import REPOSITORY_URL, fsops
from drybag.config.loader import CONFIG_FILE
from drybag.config.machine import (
    DEFAULT_CONFIG_DIR,
    machine_config_path,
    owner_home,
    passwd_home,
)
from drybag.config.schema import MEMORY_MODES, Config
from drybag.config.session import (
    SESSION_READ_LIMIT,
    TOO_LARGE_WARNING,
    SessionConfig,
    cause,
    session_config,
)
from drybag.doctor.model import (
    OK,
    RED,
    REPORT_THIS,
    SKIP,
    WARN,
    Check,
    Context,
    Contribution,
    Row,
    configured,
)
from drybag.doctor.registry import contributions
from drybag.findings import listed
from drybag.grammar import PATH_VALUE
from drybag.harnesses import CANONICAL, CLAUDE, HARNESSES
from drybag.hooks.api import (
    DIAGNOSTICS,
    DIAGNOSTICS_MAX_BYTES,
    DIRECTORY,
    MARKERS,
    data_root,
)
from drybag.printed import clipped
from drybag.semver import later

# Where the harness reads drybag's wrapper from, relative to the plugin root. `hooks/` stays
# at the plugin root — `overlay/template.py` says why — so it is found by environment or by
# checkout probe and never through `importlib.resources`.
WRAPPER = "hooks/run-hook.sh"
# Wall-clock bound on the one subprocess this *module* launches. A report launches more, inside the
# areas whose rows it asks; `docs/cli.md` counts them, and `drybag.doctor` says which one leaves
# the machine.
#
# A named cap (CONTRIBUTING.md#named-caps), and no shipped file changes with it: this bounds
# `drybag --version` behind an interpreter probe, and nothing about it is a project's to tune.
# Wide enough for a cold interpreter start on a loaded machine, narrow enough that a hung probe does
# not hang `doctor`.
WRAPPER_TIMEOUT_SECONDS = 30
# The remedy of the plugin-root skip. A quiet `skip` row is what a machine with no findable
# plugin root looks like from here, and a `skip` with an empty remedy is what
# `skills/doctor/SKILL.md` tells the model not to treat as red — so it said nothing about the
# loudest fault `doctor` can meet. The two halves of it are not interchangeable: only the first
# gives a root `wrapper` will execute, which is why `wrapper`'s skip for a root the environment
# names carries that first half alone, and takes it from here rather than from a copy.
RUN_FROM_OWN_ROOT = (
    "run `drybag doctor` from the plugin's own launcher, so its root answers for itself"
)
PLUGIN_ROOT_REMEDY = (
    f"{RUN_FROM_OWN_ROOT}, or set CLAUDE_PLUGIN_ROOT to where the plugin is installed, which "
    "lets `wrapper` read the wrapper's executable bit without running it"
)
# Why `diagnostics` prints a count and no content. One constant because the reason is the row's
# whole substance, and a change that starts quoting the file has to delete this sentence to do it.
UNVOUCHED_LOG = (
    "the environment names where this file is, so nothing here can establish that drybag "
    "wrote it, and its fields are drybag's vocabulary only for a log drybag wrote"
)
# The variable, never its value: `${CLAUDE_PLUGIN_DATA}` is repository-reachable, so the path it
# expands to is as repository-authored as the file's contents.
DIAGNOSTICS_REMEDY = (
    "read ${CLAUDE_PLUGIN_DATA}/drybag/diagnostics.jsonl yourself; each line is one record, "
    "with its event, handler and error type: a hook failure, an answer the output cap cut, or a "
    "session that ran without some of its configuration (config-fallback)"
)


def _own_root() -> Path | None:
    """The plugin root **this** drybag is part of, derived from the running module's path.

    `scripts/drybag` puts `<plugin root>/src` on `sys.path`, so a drybag launched by the
    plugin — or out of a checkout — can name its own root without asking anything. A wheel
    cannot: `hooks/` is outside the module root by design, and `None` is the honest answer.
    """
    own = Path(drybag.__file__).resolve().parents[2]
    return own if fsops.is_file(own / WRAPPER) else None


# Every registered harness's name for the plugin root, `CANONICAL`'s first, read off the registry.
# Both names, because both reach this process: Codex exports `PLUGIN_ROOT` and also
# `CLAUDE_PLUGIN_ROOT`, as the spike record (`docs/plans/2026-09-05-agent-harness-p0-spikes.md`)
# measured in its *Codex plugin hooks* trial, so a rule written against one of them is
# `config/machine.py`'s own finding again — "gating one of a pair of equivalent inputs is not a
# partial defence, it is a redirect with a longer name". Neither is ever executed; see
# `_wrapper`.
NAMED_ROOTS = tuple(dict.fromkeys(harness.plugin_root_env for harness in (CANONICAL, *HARNESSES)))


def _named_root(env: Mapping[str, str]) -> Path | None:
    """The plugin root either of `NAMED_ROOTS` names, when it carries a wrapper."""
    for name in NAMED_ROOTS:
        named = env.get(name)
        if named and fsops.is_file(Path(named) / WRAPPER):
            return Path(named)
    return None


# A repository with no `drybag.toml`: what works there, and what adds the rest. A warning and
# never red, because it is the state of every repository the plugin is installed beside before
# anyone writes one, and the guards and this report work in it.
NOT_INITIALISED = (
    f"there is no {CONFIG_FILE} here; the session guards and `drybag doctor` work without one "
    "and memory stays the coding agent's own, and one adds the rest"
)
NOT_INITIALISED_REMEDY = f"write a {CONFIG_FILE} with `[drybag] version` and `[project] name`"
# A checkout with no `drybag.toml` that this machine binds (`Context.attach_config`): its
# configuration is the overlay's layers over the preset, so no file is what it lacks.
BOUND_WITHOUT_FILE = (
    f"there is no {CONFIG_FILE} here; this machine binds the checkout, so its configuration comes "
    "from the overlay's layers"
)
# A `drybag.toml` that loads with keys ignored or put back to the preset's. Key names only: a
# note's sentence carries the loader's refusal. A key is repository-authored text, so it is
# printed bounded (`_noted_keys`).
NOTED = "{file} loads with {count} setting(s) ignored or put back to the preset's: {keys}"
# How many noted names a row prints before it counts the rest: `not-initialised`'s keys, and the
# machine file's notes on `preferences`. A named cap. A repository chooses how many keys its
# `drybag.toml` holds, the `doctor` skill relays this report to a model verbatim, and three say
# where to start; `drybag docs check` lists every one. `docs/cli.md` states the number in
# words, and `tests/test_documents.py` holds every sentence that does to this value.
NAMES_SHOWN = 3
NOTED_REMEDY = (
    f"`drybag docs check` refuses the file and prints why each one was; fix or remove them in "
    f"{CONFIG_FILE}"
)
# What this row prints in a key's place when the key is outside the path grammar: `_noted_keys`
# withholds it there, as `printed.printable` withholds such a key from a line a model reads.
UNPRINTABLE_KEY = "<a key that is not a plain name>"
FALLBACK_REMEDY = (
    f"`drybag docs check` prints why; run `drybag doctor` again after fixing {CONFIG_FILE}"
)
# A file longer than a session reads is not one a command refuses: `docs check` reads it to the
# command's own bound and loads it, so `FALLBACK_REMEDY` sent the owner to a command that exits 0.
# Formatted from the constant, which is the session's to move.
TOO_LARGE_REMEDY = (
    f"shorten {CONFIG_FILE} below {SESSION_READ_LIMIT:,} bytes, the most a session reads of it, "
    f"then run `drybag doctor` again"
)


def _not_initialised(context: Context) -> Row:
    """Whether there is a `drybag.toml`, and how much of it the session can use.

    Read the way a session reads it (`config.session`): none at all is a warning, except in a
    checkout this machine binds, where it is ok and names the overlay as the source; a file that
    loads with notes is a warning listing their keys, and only a file the session drops whole is
    red, in the session's own words. The machine file's warning is the `preferences` row's.
    """
    session = context.session
    dropped = session.project_warnings
    if context.config is None:
        if dropped:
            remedy = TOO_LARGE_REMEDY if dropped[0] == TOO_LARGE_WARNING else FALLBACK_REMEDY
            return Row(RED, cause(dropped[0]), remedy)
        if context.attach_config is not None:
            return Row(OK, BOUND_WITHOUT_FILE, "")
        return Row(WARN, NOT_INITIALISED, NOT_INITIALISED_REMEDY)
    if session.noted:
        keys = _noted_keys(session.noted)
        detail = NOTED.format(file=CONFIG_FILE, count=len(session.noted), keys=keys)
        return Row(WARN, detail, NOTED_REMEDY)
    return Row(OK, f"{CONFIG_FILE} loads", "")


def _noted_keys(keys: Sequence[str]) -> str:
    """The first `NAMES_SHOWN` of `keys`, then how many more.

    A key is any text the repository chose, bounded in length by nothing. One outside the path
    grammar is withheld, as `printed.printable` withholds it from a line a model reads; one
    inside it prints through `printed.clipped`, so the row is as long as the cap allows, however
    long a name the file holds."""
    shown = [clipped(key) if PATH_VALUE.match(key) else UNPRINTABLE_KEY for key in keys]
    return _counted(shown[:NAMES_SHOWN], len(keys) - NAMES_SHOWN, ", ")


def _counted(shown: list[str], more: int, separator: str) -> str:
    """`shown` joined by `separator`, and how many more were left out when `more` is positive."""
    return separator.join(shown) + (f"{separator}and {more} more" if more > 0 else "")


VERSION_BEHIND = "set [drybag] version to the version running here once the project has moved to it"
VERSION_AHEAD = "update the drybag plugin: this project records a newer drybag than the one running"
VERSION_UNREADABLE = (
    "set [drybag] version to the X.Y.Z of the drybag release this project was last moved "
    "to: drybag cannot read the direction of a version it cannot read"
)
VERSION_UNORDERED = (
    "the two share one X.Y.Z and differ after it in a way drybag does not order, such as two "
    "pre-releases; set [drybag] version to the version running here by hand if that is the "
    "one this project should move to"
)


def _versions(context: Context) -> Row:
    running = drybag.__version__
    if context.config is None:
        # The installation's half of the question still has an answer: which drybag runs.
        return Row(OK, f"drybag {running} is running; there is no project configuration to match")
    recorded = context.config.drybag.version
    if recorded == running:
        return Row(OK, f"the project and this drybag are both {running}", "")
    # The project's own string is repository-authored and is not quoted back; what is printed
    # is the version that is actually running. The remedy follows the direction (`later`): a
    # leading `X.Y.Z` decides the direction when the two differ in it, a release is newer than
    # its own pre-release, and a value without a leading `X.Y.Z`, or a pair `later` does not
    # order, is sent to be written by hand. `later(v, v)` is `None` exactly when `v` has no
    # leading `X.Y.Z`.
    ahead = later(recorded, running)
    if ahead is None:
        readable = later(recorded, recorded) is not None
        remedy = VERSION_UNORDERED if readable else VERSION_UNREADABLE
    else:
        remedy = VERSION_AHEAD if ahead else VERSION_BEHIND
    return Row(
        WARN,
        f"{CONFIG_FILE} declares a different drybag version from the {running} running here",
        remedy,
    )


def _wrapper(context: Context) -> Row:
    """Execute the wrapper once, and report the fault it printed.

    This check closes a blind spot that no row reading a file can see. A fault the wrapper
    meets, such as no `python3` of 3.11 or newer on `PATH`, prints to stderr and exits **0**;
    the harness discards
    stderr on a 0; no Python ran, so nothing reached the sink; and `doctor` itself runs under
    whatever interpreter the user invoked it with.
    On a machine where the probe fails, every bundle is silently absent and all three diagnostic
    surfaces are blind. One subprocess closes it.

    `--version` and not a hook event: the point is whether the wrapper can reach drybag at
    all, and the cheapest question that proves it is the one that changes nothing.

    **Only a root this process derived for itself is ever executed.** `context.own_root` and
    not `context.plugin_root`: with a wheel install `_own_root()` answers `None`, and a project
    that commits `hooks/run-hook.sh` mode 100755 plus an `env` block naming its own tree would
    get that script *run* by `drybag doctor` under a named root, which would then report
    `wrapper: ok` — code execution and a false clean bill of health in one row. Requiring the named
    root to lie outside the project root is the weaker containment: it needs both sides resolved to
    survive a committed symlink, and it still admits a second attacker-controlled checkout on the
    same machine. A `skip` that names what it could not vouch for is the honest answer for a root
    this process did not choose, and the wrapper's executable bit is still read there.
    """
    # `own_root` and not `plugin_root`, on one line, so the whole rule is the single thing a
    # mutation flips: what is launched here is a root this process derived, never one a
    # variable named.
    root = context.own_root
    if root is None:
        if context.plugin_root is None:
            return Row(
                SKIP,
                "the plugin root is not readable from here, so there is nothing to run and "
                "nothing here can say whether a hook entry would reach drybag at all",
                PLUGIN_ROOT_REMEDY,
            )
        # The bit needs only the path: a wrapper without `+x` exits 126, and Claude Code reads
        # every non-2 exit as a non-blocking error, which is permission.
        wrapper = context.plugin_root / WRAPPER
        if not os.access(wrapper, os.X_OK):
            return Row(
                RED,
                f"{WRAPPER} is not executable, so every hook entry exits 126 and the harness "
                "reads that as a non-blocking error; this is the plugin root the environment "
                "names, read here and run nowhere",
                f"chmod +x {wrapper}",
            )
        return Row(
            SKIP,
            "the only plugin root here is one the environment names, and a root this process "
            "cannot vouch for is never executed: its wrapper would run before any drybag "
            "guard does",
            RUN_FROM_OWN_ROOT,
        )
    env = {
        key: value
        for key, value in context.env.items()
        if not key.startswith(("CLAUDE_", "PLUGIN_", "DRYBAG_"))
    }
    # Under Claude Code's names for the two roots, as `hooks/hooks.json` runs the wrapper.
    for variable, value in ((CLAUDE.plugin_root_env, root), (CLAUDE.project_dir_env, context.root)):
        if variable is not None:
            env[variable] = str(value)
    try:
        done = subprocess.run(  # noqa: S603 - list form, never a shell; drybag's own wrapper
            [str(root / WRAPPER), "--version"],
            cwd=context.root,
            capture_output=True,
            text=True,
            # Never quoted: the wrapper echoes what it was handed, a project directory in latin-1
            # bytes included, and strictly that raised and lost the row.
            errors="replace",
            check=False,
            timeout=WRAPPER_TIMEOUT_SECONDS,
            env=env,
            # Closed, not inherited, so a child that decides to read stdin cannot hold the report
            # open behind `WRAPPER_TIMEOUT_SECONDS`.
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Row(
            RED,
            f"{WRAPPER} could not be run ({type(exc).__name__}), so no hook entry can fire",
            f"check that {root / WRAPPER} exists and is executable",
        )
    if done.stderr or done.returncode != 0:
        return Row(
            RED,
            f"{WRAPPER} exited {done.returncode} and wrote to stderr, so every hook entry "
            "answers nothing on this machine and nothing else reports it",
            "run `hooks/run-hook.sh --version` and read its stderr",
        )
    return Row(OK, f"{WRAPPER} reached drybag and exited 0", "")


def _budgets(context: Context, config: Config) -> Row:
    """Every budget set above the preset and every one set below it.

    The base branch's `drybag.toml` sets a gate's budget and the owner's machine file a
    session's, in either direction, and the value set is the value in force (`Budgets.effective`).
    No consumer applies a platform cap to a budget — `native_caps` are checked beside the budget,
    never folded into it — so the row names no clamp. Budget keys are `Budgets.NAMES` and the loader
    refuses any other, so these are drybag's own names rather than a repository's strings.
    """
    budgets = config.budgets
    raised = sorted(
        name for name, value in budgets.configured.items() if value > budgets.preset[name]
    )
    lowered = sorted(
        name for name, value in budgets.configured.items() if value < budgets.preset[name]
    )
    moved = [
        f"{len(names)} budget(s) {direction}: {listed(names)}"
        for names, direction in ((raised, "raised above the preset"), (lowered, "lowered below it"))
        if names
    ]
    return Row(OK, "; ".join(moved) or "every budget is the preset's")


# The keys whose value is one of drybag's own words, which the loader holds to this vocabulary,
# and so may be printed. Every other text value is the repository's or the owner's to write, and
# is not.
ENUMERATED: Mapping[str, tuple[str, ...]] = {"memory.mode": MEMORY_MODES}
PREFERENCES_REMEDY = "fix the machine file the detail names, then run `drybag doctor` again"
# What reading the overlay's two layers said (`SessionConfig.resolution.notes`), named in the
# `preferences` row as information.
OVERLAY_NOTES = "The overlay layers: {notes}"
# A machine file that does not load, in the words of its refusal (`Owner.unloaded`), which names
# the file and the parser's position and never its text. Red, as every red row is a statement that
# this installation is wrong: a session reads past the file, and every command refuses it.
MACHINE_UNLOADED = (
    "{error}, so every command refuses it, and a session runs on the preset's personal values and "
    "guards in its place"
)


def _shown(key: str, value: object) -> str | None:
    """`value` as the row may print it, or `None` where it is text somebody else wrote."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    vocabulary = ENUMERATED.get(key, ())
    return value if isinstance(value, str) and value in vocabulary else None


def _named(key: str, value: object, layer: str) -> str:
    shown = _shown(key, value)
    return f"{key} ({layer})" if shown is None else f"{key} = {shown} ({layer})"


def _preferences(context: Context) -> Row:
    """Each key the session resolved, and the layer its value came from.

    The session's one resolution (`SessionConfig.resolution`) over the preset, the repository's
    file after the NOTE load, the overlay's common, tag and project files and the machine file gives
    every value and its layer; the row derives none. The value is printed only where it is a
    boolean, a number or one of drybag's own words (`ENUMERATED`): a name list or a
    personal value is text a repository, an overlay or an owner wrote, and stays out of a row a
    model may read. Runs with no `drybag.toml` too, where nothing is the repository's.

    A machine file that does not load is red, in its refusal's words (`MACHINE_UNLOADED`): every
    command refuses it, and the report, which reads it as a session does, was otherwise the one
    run that exited 0 on it. What the owner's layer read past in a file that loads is a warning.
    Every note of the resolution — what the overlay's files could not give, and what `resolve`
    refused — is named after the values (`OVERLAY_NOTES`), and moves no status.
    """
    resolution = context.session.resolution
    named = [
        _named(key, resolution.values[key], resolution.said(key))
        for key in sorted(resolution.provenance)
    ]
    detail = "; ".join(named)
    if resolution.notes:
        # Information, never a status: a machine with no overlay, or a project with no file of its
        # own, is an ordinary state. Each note was composed through `drybag.printed` where it
        # was made (`config.overlay_layers`, `classes.resolve`), so nothing raw reaches the row.
        detail = f"{detail}. {OVERLAY_NOTES.format(notes='; '.join(resolution.notes))}"
    owner = context.session.owner
    if owner.unloaded is not None:
        return Row(
            RED, f"{detail}. {MACHINE_UNLOADED.format(error=owner.unloaded)}", PREFERENCES_REMEDY
        )
    notes = owner.notes
    if notes:
        return Row(
            WARN,
            f"{detail}. The machine file could not be used whole: "
            f"{_counted(list(notes[:NAMES_SHOWN]), len(notes) - NAMES_SHOWN, '; ')}",
            PREFERENCES_REMEDY,
        )
    return Row(OK, detail)


def _cli_path(context: Context) -> Row:
    """Whether `drybag` resolves by name on this machine.

    Codex performs no `${CLAUDE_PLUGIN_ROOT}` substitution in skill content, as the spike record's
    *plugin-root substitution and executable bits* trial measured, so a skill that says
    `drybag …` needs the name to resolve on PATH there.

    **Asked of `context.env`, like every other check that reads the environment**, and never of
    `os.environ["PATH"]` directly, which a bare `shutil.which("drybag")` reads. `run_checks`
    defaults that mapping to `os.environ`, so a real run reads the same `PATH` either way; what
    asking the context buys is an answer that is a function of the context rather than of
    whichever shell the caller happens to be in, so a test can state the answer it expects — read
    from the process, the row says `ok` on a developer machine with the tool installed and `warn`
    in a container without it, and a body hardcoded to `WARN` would pass the whole suite.

    The default is `""` and not `None`, which is the difference between the sentence above being
    true and being true of every environment but one: `shutil.which(path=None)` falls back to
    `os.environ["PATH"]`, so an `env` carrying no `PATH` would reach the process environment
    through the very call that exists to stop doing that. An environment with no `PATH` resolves
    nothing, which is the honest answer and the one the row's own remedy addresses.

    **The resolved path is never printed.** `PATH` is read from `context.env` precisely because
    it is repository-authored, and a POSIX path component is unbounded and may hold a newline --
    `shutil.which` round-trips one -- so the value is the same class of input `_diagnostics`
    refuses to print. The row says
    the name resolves; the reader's own `command -v drybag` says where.
    """
    found = shutil.which("drybag", path=context.env.get("PATH", ""))
    if found is None:
        return Row(
            WARN,
            "`drybag` does not resolve on PATH, so a skill that invokes it by name fails on "
            "Codex, which performs no plugin-root substitution in skill content",
            # `REPOSITORY_URL` and not the address written out: a URL is spelled once. A second
            # spelling of a URL is a second thing to move when the repository does, and `doctor`
            # is the command whose whole job is finding the two halves of something that has
            # stopped agreeing.
            f"run `uv tool install git+{REPOSITORY_URL}`",
        )
    return Row(OK, "`drybag` resolves on PATH")


def _is_record(line: bytes) -> bool:
    """Whether one line of the sink's log is a JSON object, which is all this check asks of it.

    Bytes and not text: the file may be anything, and `json.loads` raising `UnicodeDecodeError`
    on a line that is not UTF-8 is the same answer as raising `JSONDecodeError` on one that is
    not JSON — this is not a record. So is a line the parser cannot read although it is valid
    JSON: an integer literal longer than the interpreter converts raises a plain `ValueError`, and
    nesting past what the parser follows raises `RecursionError`. The log is wherever a committed
    `env` block points, and either one let through ended this row in `_guarded`'s red.
    """
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return False
    return isinstance(record, dict)


def _diagnostics(context: Context) -> Row:
    """How many reasons the hook sink recorded. A count, and not one byte of the file.

    **Nothing in this file is quoted, because nothing here can establish who wrote it.** The log
    is `${CLAUDE_PLUGIN_DATA}/drybag/diagnostics.jsonl`, and that variable is the same class
    as `NAMED_ROOTS`: a committed `.claude/settings.json` `env` block reaches this process without
    a trust prompt. `event`,
    `handler` and `error` are drybag's own vocabulary *for a log drybag wrote*; for a log a
    repository committed they are three free-text fields, and `skills/doctor/SKILL.md` tells the
    model to relay this detail verbatim: quoted, a committed log whose `handler` is an
    instruction-shaped string and whose `error` is 5,000 characters gives a `warn` detail of more
    than 5,000 characters carrying both.

    So no field is allowed through, however few. What is left is a count, which is this check's
    own answer, and a remedy that names the file by the variable rather than by its value — the
    value is repository-authored too.

    **The read is bounded here, because the cap the sink documents is enforced on write.**
    `DIAGNOSTICS_MAX_BYTES` bounds what `DataSink.diagnostic` appends; a file this process did
    not write has no cap at all, and one byte past it is itself an answer — this is not a file
    the sink produced, and the count below is a floor rather than a total.
    """
    # The rule the sink found the root by, so this row reads the tree the sink wrote.
    data = data_root(context.env)
    if not data:
        return Row(
            SKIP,
            "no harness data root is set in this environment, so the hook sink cannot be read",
            "",
        )
    base = Path(data) / DIRECTORY
    try:
        sessions = len(list((base / MARKERS).iterdir())) if fsops.is_dir(base / MARKERS) else 0
    except OSError as exc:
        # The harness data root is somebody else's directory on somebody else's filesystem, and
        # an unreadable one is a fact about this machine rather than a fault in the
        # installation. Unguarded it would reach `_guarded`, which renders any exception but an
        # `OSError` red and an `OSError` as a warning about the whole row — so it is answered here,
        # where the row can still say what it could and could not count.
        return Row(
            WARN,
            f"the hook sink's session markers could not be listed ({fsops.said(exc)}), so "
            f"neither the session count nor the record count below can be given",
            DIAGNOSTICS_REMEDY,
        )
    log = base / DIAGNOSTICS
    if not fsops.is_file(log):
        return Row(OK, f"no hook records are logged; {sessions} session(s) seen")
    try:
        raw, over = fsops.read_bounded(log, DIAGNOSTICS_MAX_BYTES)
    except OSError as exc:
        return Row(
            WARN,
            f"the hook sink's log is there and could not be read ({fsops.said(exc)})",
            DIAGNOSTICS_REMEDY,
        )
    count = sum(1 for line in raw.splitlines() if _is_record(line))
    if not count and not over:
        return Row(OK, f"no hook records are logged; {sessions} session(s) seen")
    # Records, and never told apart: which kind a line is lies in its `error` field, which is
    # drybag's vocabulary only for a log drybag wrote (`UNVOUCHED_LOG`).
    counted = f"{'at least ' if over else ''}{count} hook record(s) logged"
    return Row(
        WARN,
        f"{counted}; {sessions} session(s) seen. Not one line is quoted: {UNVOUCHED_LOG}",
        DIAGNOSTICS_REMEDY,
    )


# The two variables that could name the machine configuration file, and that no command reads.
# `config/machine.py` nominates this check by name: a machine owner who sets one "really does lose
# it rather than getting a wrong answer quietly".
IGNORED_ENV = ("DRYBAG_CONFIG", "XDG_CONFIG_HOME")
# Information and not a warning: drybag follows `HOME`, as the harness does (`config.machine`).
# The value of `HOME` is not printed: `doctor` may be run by an agent whose environment a
# repository chose.
HOME_DIFFERS = (
    "HOME is not the home directory the password database records for this user; drybag "
    "follows HOME, as the harness does"
)


def _names_home_config(value: str, home: Path) -> bool:
    """Whether `value`, an `XDG_CONFIG_HOME`, names `home`'s `.config`, the directory the machine
    file is read under, so ignoring it loses nothing; a value that does not resolve names none."""
    if not os.path.isabs(value):
        return False
    try:
        return fsops.resolved(Path(value)) == fsops.resolved(home / DEFAULT_CONFIG_DIR)
    except OSError:
        return False


def _ignored_env(context: Context) -> Row:
    """The variables that could name the machine file and that no command reads, and a `HOME` that
    is not the password database's: information, never a status. Each is a choice of the person
    who set it, which no command of drybag's can undo, so a warning about it would be a row that
    never clears."""
    # The home every command reads under, and none for a `HOME` that names none (`owner_home`).
    home, recorded = owner_home(), passwd_home()
    set_here = [
        name
        for name in IGNORED_ENV
        if context.env.get(name)
        and not (
            name == "XDG_CONFIG_HOME"
            and home is not None
            and _names_home_config(context.env[name], home)
        )
    ]
    differs = home is not None and recorded is not None and home.resolve() != recorded.resolve()
    if not set_here:
        return Row(OK, HOME_DIFFERS if differs else "no environment variable is being ignored")
    detail = (
        f"{listed(set_here)} is set and is not honoured: every command reads the machine "
        f"configuration at ~/.config/drybag/config.toml and nothing else, or the file --machine "
        f"names"
    )
    if differs:
        detail = f"{detail}; {HOME_DIFFERS}"
    return Row(OK, detail)


# The core's checks, in the order the `doctor` table in `docs/cli.md` lists them, ahead of every
# row an area contributes. The list is the report's order and the core's only registry: a check
# added here needs no other edit, and a check missing from it is a check nothing runs. An area adds
# rows after these through its own `doctor.py` (`registry.contributions`), never by an edit here.
CHECKS: tuple[tuple[str, Callable[[Context], Row]], ...] = (
    ("not-initialised", _not_initialised),
    ("versions", _versions),
    ("wrapper", _wrapper),
    ("budgets", configured(_budgets)),
    ("preferences", _preferences),
    ("cli-path", _cli_path),
    ("diagnostics", _diagnostics),
    ("ignored-env", _ignored_env),
)


def _guarded(name: str, check: Callable[[Context], Row], context: Context) -> Check:
    """One check's answer, or a red row naming the exception type it died of.

    Never a traceback out of `run_checks`. The report is the thing the user has left when
    everything else is broken, so a check that raises costs one row and not the diagnosis — and
    the exception's *message* is not printed, because a `Failure` built out of `memory.groups`
    or a note's path is repository-authored, and a repository is untrusted input (principle 5).

    `Exception` and not `BaseException`: what was asked for is that a check which *raises*
    becomes a red row. `KeyboardInterrupt` and `SystemExit` are not that — catching them turns
    one `Ctrl-C` into a report of red rows, instead of stopping.

    **An `OSError` is a `warn` and everything else is a `red`, and the split is the point.**
    `red` is what gates the exit code, so a red row is a statement that this installation is
    wrong. A file that could not be opened is not that: the directories these checks read live
    on the machine, not in the installation — an unreadable `${CLAUDE_PLUGIN_DATA}` read as red
    would give `diagnostics: red` and exit 1 with nothing wrong anywhere. Every other exception
    is a defect in this module and keeps its red, because that is what the row is for.
    """
    try:
        row = check(context)
    except OSError as exc:  # the machine, not the installation
        return Check(
            name,
            WARN,
            f"this check could not read something it needed: {type(exc).__name__}",
            "check that the files and directories this check reads are readable here",
        )
    except Exception as exc:  # a broken check must cost one row, never the whole report
        return Check(name, RED, f"this check could not run: {type(exc).__name__}", REPORT_THIS)
    return Check(name, row.status, row.detail, row.remedy)


def _report_checks(
    contributed: list[Contribution],
) -> tuple[tuple[str, Callable[[Context], Row]], ...]:
    """The report's checks in the report's order: the core's, then each area's."""
    return CHECKS + tuple(check for contribution in contributed for check in contribution.checks)


def _context(
    root: Path,
    *,
    home: Path | None,
    machine: Path | None,
    env: Mapping[str, str],
    session: SessionConfig,
) -> Context:
    """The report's `Context`, whole, with both plugin roots."""
    return Context(
        root,
        home,
        machine,
        env,
        session.config,
        own_root=_own_root(),
        plugin_root=_named_root(env),
        session=session,
    )


def run_checks(
    root: Path,
    *,
    home: Path | None,
    machine: Path | None,
    env: Mapping[str, str] | None = None,
) -> list[Check]:
    """One row per check, the core's and every area's, whatever state the machine is in.

    Three keyword parameters, which is the published signature. None chooses the interpreter the
    `wrapper` check's probe runs under: the wrapper takes the `python3` on `PATH`. A test fails
    the probe the way a machine does — with a plugin root whose launcher is not there.

    `env` defaults to the process environment because two checks are *about* the environment —
    `ignored-env` reads it, and `diagnostics` finds the harness data root in it.
    """
    env = os.environ if env is None else env
    # Every check is asked whatever the repository holds: the configuration is read the way a
    # session reads it (`config.session`), which never raises for anything the repository or the
    # machine file holds, so a repository with no `drybag.toml`, or one that does not load, has
    # every row the installation can answer, and a row that reads the project configuration skips
    # without one (`NO_CONFIG_SKIP`). The machine file is resolved once, the one no variable
    # moves, so every row that names it names the file that was read.
    read = machine_config_path() if machine is None else machine
    session = session_config(root, machine=read)
    contributed = contributions(CHECKS)
    context = _context(
        root,
        home=home,
        machine=machine,
        env=env,
        session=session,
    )
    return [_guarded(name, check, context) for name, check in _report_checks(contributed)]
