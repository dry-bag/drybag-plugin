"""What a `doctor` row is made of, and what an area hands the report to add rows of its own.

The vocabulary lives here rather than in `checks.py` because two kinds of module speak it: the
core's checks in `checks.py`, and an area's own `doctor.py`, which
`registry.contributions` discovers by name and which reaches this module through `doctor/api.py`.
`checks.py` is the core's checks and the run; this module is the shapes they share and the one
answer every area reads alike, the overlay root (`Context.overlay_root`), so it imports nothing of
any area. `REPORT_THIS` is here too: the run's guard in `checks.py` and the rows `registry.py`
builds for an area that could not contribute both give it, and it is a remedy, part of what a row
is made of, rather than anything of the run's or of discovery's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Final, Literal

from drybag.binding import main_checkout_or_none
from drybag.config.loader import read_document
from drybag.config.overlay import overlay_root as recorded_overlay_root
from drybag.config.reading import MachineConfigError
from drybag.config.schema import Config
from drybag.config.session import SessionConfig, bound_config
from drybag.errors import Failure, Refusal, resolved_or_none

OK: Final = "ok"
WARN: Final = "warn"
RED: Final = "red"
SKIP: Final = "skip"
Status = Literal["ok", "warn", "red", "skip"]
STATUSES: tuple[Status, ...] = (OK, WARN, RED, SKIP)
# The remedy of a row that is red because drybag's own code broke: nothing the reader did can
# clear it, and the command they ran is what a fix starts from.
REPORT_THIS = "report this, with the command you ran"
# The skip of a row that reads the project configuration, in a repository with none to read: no
# remedy of its own, since `not-initialised` says why there is none and what to do.
NO_CONFIG_SKIP = "there is no project configuration to check this against"


@dataclass(frozen=True)
class Check:
    """One row of the report: what was asked, what the answer was, and what to do about it.

    `remedy` is empty for a row nothing can be done about, and a `skip` is **not** entitled to
    an empty remedy merely for being a skip. The line is not "always" versus "on a state of the
    machine or the repository", and it is not whether some command elsewhere in the report would
    change that state: it is whether **the skip is itself worth acting on**. A skip that reports
    something wrong which no other row will say — a plugin root nothing can find, which is every
    hook entry on this machine silent; a recorded state nothing could corroborate; a recorded path
    that is not there — says what to do. A skip that reports a measurement that is simply not
    available — nothing recorded to measure, no data root, no way to ask — and that no command in
    that row's gift changes, carries nothing. A reader is never handed a command that would not
    help, and never denied one that would.

    One row may have both kinds of arm: a machine that never recorded something and one that
    recorded it and then moved it are two states with two sentences, and only the second carries
    a remedy. Which of the report's rows skip, on what, and which of those arms carry a remedy is
    `docs/cli.md`'s census, not this type's.
    """

    name: str
    status: Status
    detail: str
    remedy: str = ""


@dataclass(frozen=True)
class Row:
    """What one check answers. The name is the registry's, stamped by `checks._guarded`."""

    status: Status
    detail: str
    remedy: str = ""


@dataclass(frozen=True)
class MachineUnread:
    """`Context.overlay_root`'s answer when the machine file that records the root could not be
    read: whether this machine records an overlay is not known, which is not "it records none".

    `reason` is `config.reading.read_machine_toml`'s refusal, which names the file and the
    parser's position or the error's class, and never the file's text. The `preferences` row is
    red on it and says it; a row that reads the overlay root skips without claiming that nothing
    is recorded, which was false and pointed the owner at `drybag setup` for a stray bracket.
    """

    reason: str


@dataclass(frozen=True)
class Context:
    """Everything a check may read for one report, built whole by `checks.run_checks` and never
    changed after.

    `session` is the configuration read the way a session reads it (`config.session`), and
    `config` is its project configuration, `None` for a repository with no `drybag.toml` or one
    that does not load: every check runs all the same, and one that reads `config` skips with
    `NO_CONFIG_SKIP` when there is none, while the first check says why there is none.

    **What the core answers for, and what every area reads alike.** The fields are the core's:
    the run's inputs, the configuration and the two plugin roots. `overlay_root` is the core's
    too — `[overlay] root` is a key of the machine file, which the core owns (`config.overlay`) —
    and is here because more than one area reads it, so it is resolved once per report for all of
    them. What an area owns — the note store, the
    attach ledger, the binding — is resolved inside that area's own `doctor.py` and never reaches
    this type, so building it imports nothing of any area.
    """

    root: Path
    home: Path | None
    machine: Path | None
    env: Mapping[str, str]
    config: Config | None
    plugin_root: Path | None = None
    # The plugin root this process can vouch for, which is the only one anything here executes.
    # `plugin_root` is the root the environment named, if any; this is `None` unless
    # self-derivation answered. Two fields and not a flag, because the check that runs the wrapper
    # should not be able to reach the other answer at all.
    own_root: Path | None = None
    # Keyword-only and required: a report built without the session would have no owner's layer
    # to name and no way to tell a repository with no `drybag.toml` from one that fell back.
    session: SessionConfig = field(kw_only=True)

    @cached_property
    def overlay_root(self) -> Path | MachineUnread | None:
        """The overlay root the machine file records, whether or not anything is there; `None`
        when it records none, or when the root it records cannot be resolved; `MachineUnread`
        when the machine file itself cannot be read or parsed.

        Resolved on first read and never again for this report: `cached_property` keeps the
        answer in the instance's `__dict__` without going through `__setattr__`, which is why a
        frozen dataclass without slots can carry it, and an answer that is itself `None` is kept
        as well. A fresh `Context` per report is a fresh answer per report, so nothing one run
        resolved reaches the next.

        A resolution that fails is a row's skip, never "this check could not run", which is red
        and gates the exit code for something the repository did not do wrong. Told apart by the
        exception's type and never by its text: a machine file that does not load is
        `MachineUnread`, which the `preferences` row is red on and every reader of this answer
        skips on in its own words, since saying "no overlay root is recorded" of a file nobody
        could read was false; any other failure the way `errors.resolved_or_none` reads one, as
        nothing to look at.
        """
        try:
            return recorded_overlay_root(self.machine)
        except MachineConfigError as exc:
            return MachineUnread(str(exc))
        except (Failure, Refusal, OSError):
            return None

    @cached_property
    def attach_config(self) -> Config | None:
        """`config`, or, where this machine binds a checkout whose main checkout has no
        `drybag.toml` at all, the configuration `attach` acts on there
        (`config.session.bound_config` in the main checkout, for the project the binding record
        names): the rows about what `attach` delivered — `attached` and the note store — judge such
        a checkout on what `attach` used. Every other row reads `config`, and every other case,
        a file the session dropped or one only the main checkout has included, is `None`, as
        `config` is; a resolution that fails is none too (`errors.resolved_or_none`)."""
        if self.config is not None:
            return self.config

        def built() -> Config | None:
            main = main_checkout_or_none(self.root)
            if main is None or read_document(main) is not None:
                return None
            return bound_config(main, machine=self.machine, owner=self.session.owner)

        return resolved_or_none(built)


@dataclass(frozen=True)
class Contribution:
    """What an area's `doctor.py` hands the report from its `register()`.

    `checks` are `(name, check)` pairs, asked after the core's own in area-name order and each
    through the guard the core's go through, so one that raises costs its own row. A name is
    unique in the report: an area that repeats a core check's, another area's or its own costs
    its rows, as one red row named after the area.
    """

    checks: tuple[tuple[str, Callable[[Context], Row]], ...]


def configured(
    check: Callable[[Context, Config], Row], *, attach: bool = False
) -> Callable[[Context], Row]:
    """`check`, a row that reads the project configuration, asked with it where the report has one;
    where it has none the row skips with `NO_CONFIG_SKIP`, and `not-initialised` says why. With
    `attach`, a row about what `attach` delivered, the configuration is `Context.attach_config`."""

    def row(context: Context) -> Row:
        config = context.attach_config if attach else context.config
        if config is None:
            return Row(SKIP, NO_CONFIG_SKIP)
        return check(context, config)

    return row
