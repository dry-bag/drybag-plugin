"""Write the machine configuration file, the one its readers in `config` already read.

**This module invents no schema.** `config.owner.resolve_owner` reads `[personal]`, `[guards]`
and the session budgets of `[budgets]`, for every command's load and every session, and
`config.overlay.overlay_root` reads `[overlay] root`, all from the one file under `HOME`, and
none of them changes here. This writer adds `[machine]` beside them — what `setup` installed, so
`doctor` can check it later — and nothing else. `[guards]` and `[budgets]` are the owner's to write
by hand, and a rewrite carries them through untouched, as it carries every table and key it does
not own (below). A caller that wants another table is asking for another reader of this file,
which belongs beside those readers, in `config`.

**A rewrite merges, table by table, key by key — including the tables this writer knows
nothing about.** `setup` runs again on a machine that already has a file, and the common case is
"set the personal values, leave the overlay alone" or the reverse — `overlay_root=None` means
*this call does not know*, not *forget what was recorded*, so an unset value is simply not
written over and survives in the table it was spread onto. The three tests this file exists to
satisfy are the
three ways that can go wrong: nothing recorded stays nothing recorded, a value nobody touched
survives a rewrite that touched something else, and one table's `root` never collapses into a
fourth state (see `config.overlay.overlay_root`'s own docstring on that).

`README.md` lists this file under "Written by: **you**, or `drybag setup`", which makes a
table this writer does not recognise the ordinary case rather than the exotic one: a merge that
covered only the three tables it owns and replaced the rest of the file would lose a `[trust]`
table or a key at the top level after one `setup`. So every table it does not own is carried
through in the file's own order, and the three it owns are merged in place.

**Two things a rewrite still costs, both stated rather than discovered.** Comments do not
survive — `tomllib` discards them on the way in, and there is no round-tripping parser in the
standard library to keep them — and a key that is not a bare TOML key cannot be re-emitted.
Neither may wedge the command: `tomlout` emits every *value* type a TOML document can hold, so
a float or a nested table round-trips rather than making every future run exit 2, and the one
refusal left names this file, the key, and what to do about it instead of naming a serialiser
the owner has never heard of.

**`fsops.write_atomically` on a bare `Path`, not `fsops.write_within`.** Every other writer `setup`
uses owns a root — a project checkout, the overlay — and walks into it with `O_NOFOLLOW`. This file
has no such root: `config.machine.machine_config_path` resolves to
`~/.config/drybag/config.toml`, or `--machine` sends it elsewhere, and that directory is not one
this process was handed as "the thing to stay inside of". `fsops.write_atomically`'s own docstring
names exactly this caller: "for callers that already hold a trusted absolute path". The path is
trusted because it is built out of fixed strings this module and `config.machine` wrote
(`"drybag"`, `"config.toml"`) or a path the machine owner typed on their own command line — never
a segment a repository chose, which is what `mkdirs_within`'s containment exists to stop.

Every string that reaches `tomlout.dumps` is repository-*adjacent* rather than
repository-authored — the overlay root and the personal values are values the machine owner
typed — but it goes through the one serialiser anyway: `tomlout`'s own docstring says why a
second writer, hand-rolled for "just this one path", is the mistake this module exists to not
repeat.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drybag import fsops, tomlout
from drybag.config.machine import machine_config_path
from drybag.errors import Failure, Refusal
from drybag.harnesses import CLAUDE
from drybag.trust.api import RUN_OUTSIDE, SANDBOX_REFUSED, sandbox_refused

# Said by `setup` where a directory it writes -- drybag's machine directory, Claude Code's user
# settings -- refused a write as an agent's sandbox refuses one (`trust.boundary`'s docstring).
# A run it stops part-way is one the next run finishes, since every write it makes merges.
SETUP_SANDBOXED = SANDBOX_REFUSED.format(
    outcome="setup stopped",
    directory="a directory it writes",
    remedy=RUN_OUTSIDE.format(command="`drybag setup`"),
)

# The one machine-scope settings file `setup` writes, relative to `home`: Claude Code's settings
# file, which it reads under the home directory as it does under a project, so it is the
# registry's one spelling of that file. Codex has no equivalent: whether Codex has anything like
# a plugin's `userConfig` has never been measured, so nothing is written there and the report says
# so rather than guessing a path.
(USER_SETTINGS,) = CLAUDE.settings


@dataclass(frozen=True)
class Written:
    """What `write_machine` wrote. `path` is the file, for a caller that wants to say where."""

    path: Path


def read_machine(path: Path) -> dict[str, Any]:
    """The machine file as a raw `dict`, exactly as `tomllib` parses it, and `{}` where no file is.

    Read through `config.reading.read_machine_toml`, the one reader every reader of this file
    shares, so a file that cannot be read, is not UTF-8 or is not TOML is the one
    `MachineConfigError` here as everywhere: one failure in one set of words, naming the error's
    class or the parser's position and never the parser's message. `setup` reports it as the
    failure it is, rather than as an internal error with the operating system's own message.
    """
    from drybag.config.reading import read_machine_toml

    return read_machine_toml(path) or {}


def _table(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    return dict(value) if isinstance(value, dict) else {}


def read_personal(path: Path) -> dict[str, Any]:
    """The machine file's `[personal]` table, and `{}` where there is no file, no such table, or
    a `personal` that is not a table: what an earlier run or the owner already set there."""
    return _table(read_machine(path), "personal")


def write_machine(
    path: Path,
    *,
    personal: Mapping[str, object],
    overlay_root: Path | None,
    machine: Mapping[str, object],
    sync: Mapping[str, object] = {},
) -> Written:
    """Merge `personal`, `overlay_root`, `machine` and `sync` into the file at `path`, and write it.

    Each of the three is merged over what the file already holds, key by key, rather than
    replacing its table outright — a second `setup` run that only sets one of them must not
    erase what an earlier run recorded, which both unchanged readers still read (the test this
    docstring's module comment names). `overlay_root=None` reads as "not given this run": the
    existing `[overlay] root`, if any, is carried over unchanged. There is no way to ask this
    function to *clear* a recorded overlay root; nothing in drybag needs one.

    Everything else in the file — a table this writer has never heard of, a key at the top
    level — is carried through untouched, in the order it was written in.
    """
    existing = read_machine(path)

    merged_personal = {**_table(existing, "personal"), **personal}
    # All three spread over the existing table, `overlay` included: replaced outright, a
    # hand-written `[overlay] note` would be gone after any `setup`, and an `[overlay]` carrying no
    # `root` would vanish whole -- on the file this module's own docstring promises to merge
    # "table by table, key by key".
    #
    # That spread is also what carries a recorded `root` through a run that was not given one, so
    # no read-back of the existing `[overlay] root` is needed beside it. The property is declared
    # where the spread keeps it, as `mutations/`'s "write_machine replaces the overlay table
    # instead of merging over it".
    owned: dict[str, dict[str, object]] = {
        "personal": merged_personal,
        "overlay": {
            **_table(existing, "overlay"),
            **({"root": str(overlay_root)} if overlay_root is not None else {}),
        },
        "machine": {**_table(existing, "machine"), **machine},
        "sync": {**_table(existing, "sync"), **sync},
    }

    tables: dict[str, dict[str, object]] = {}
    # A key outside every table comes first, because TOML reads everything after a header as
    # belonging to it. Nothing this project writes here puts one there; a person might.
    loose = {key: value for key, value in existing.items() if not isinstance(value, dict)}
    if loose:
        tables[""] = loose
    for name, value in existing.items():
        if not isinstance(value, dict):
            continue
        if name in owned:
            if owned[name]:
                tables[name] = owned[name]
        else:
            tables[name] = dict(value)
    for name, table in owned.items():
        if table and name not in tables:
            tables[name] = table

    try:
        text = tomlout.dumps(tables)
    except Refusal as exc:
        # The one shape left that cannot be re-emitted: a key this serialiser cannot write bare.
        # It reached the owner as `Refusal: personal.scale holds a …` — no path, no remedy, and
        # the name of a module they have never heard of — on *every* future run, because the
        # file is read back at the top of every one of them.
        raise Refusal(
            f"{path} cannot be rewritten: {exc}. `drybag setup` rewrites this file, so it has "
            f"to be able to write back everything in it; remove or rename that key and run the "
            f"command again"
        ) from exc
    try:
        # Through a link at `path` (a dotfiles manager's), as the user settings file is written,
        # so the link stays; a loop there is the `OSError` answered below.
        fsops.write_atomically(fsops.resolved(path), text)
    except OSError as exc:
        if sandbox_refused(exc):
            raise Refusal(SETUP_SANDBOXED) from None
        linked = fsops.linked_directory(path)
        if linked is not None:
            raise Failure(
                f"{linked} cannot be written ({fsops.said(exc)}), so the machine configuration "
                f"cannot be kept there, where the link at {path} leads"
            ) from None
        where = (
            "; without --machine it is under HOME, which is the only place a hook reads it"
            if path == machine_config_path()
            else ""
        )
        raise Failure(
            f"{path.parent} cannot be written ({fsops.said(exc)}), so the machine configuration "
            f"cannot be kept there{where}"
        ) from None
    return Written(path)
