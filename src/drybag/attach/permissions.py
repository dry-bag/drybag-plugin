"""What attaching would add to this project's local settings, and where it may read it from.

**The plates are the sources, and the committed settings file is the defect.** The inputs are the
`claude/` directory of each plate of the overlay (`config.plates`: `common/`, the project's tags,
`projects/<name>/`) — the machine owner's own files, in the repository the machine file anchors.
`.claude/settings.json` is read for *nothing*: it is committed, so it is repository-controlled, and
committed settings that would widen a permission are never merged.
`.claude/settings.local.json` is read, and only to subtract: a rule the project already carries
is not something this attach would add.

**The diff is allow rules alone.** The overlay's hook entries reach a session through the plugin
`attach.render` renders, never through `settings.local.json`, which Claude Code rewrites whole and
where no marker survives; this module reads the overlay's hook files for that render
(`hook_groups`). An allow rule has exactly one witness, the ledger `write.py` keeps.

**What may be printed.** `added_allow` is built out of the overlay, which is the owner's own
repository, so a command may show it, and `attach` does, on the screen it proceeds with.
`already_present` comes out of `.claude/settings.local.json`, which a hostile clone *can* commit,
so it is repository-authored: this module carries the strings so a caller can subtract with them,
and `attach.check` reports only how many there were.
"""

from __future__ import annotations

import stat
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.attach.binding import Binding, cannot_exist
from drybag.attach.items import CODEX_RULE, PROJECT_CLAUDE, fold
from drybag.config.plates import Plate, PlateKind
from drybag.errors import Failure
from drybag.fsops import read_regular_text
from drybag.harnesses import CLAUDE
from drybag.jsonobject import WRITTEN_PAST
from drybag.scaffold import (
    EntriesError,
    ParserLimitError,
    settings_object,
    settings_text,
)

# The project-local file `attach` owns outright, as the harness registry names it: the one file
# Claude Code reads that a repository keeps out of git. `.claude/settings.json` beside it is the
# committed one and is never read.
(LOCAL_SETTINGS,) = CLAUDE.local_settings
PERMISSIONS_FILE = "permissions.json"
HOOKS_FILE = "hooks.json"
# Where Codex reads standing instructions.
CODEX_RULES = ".codex/rules"


@dataclass(frozen=True)
class PermissionDiff:
    """What `attach` would add to the allow rules, what is already there, and what it would take
    back: `withdrawn_allow`, filled by `withdrawing`, is a rule the previous ledger recorded that
    the overlay no longer grants and the settings file still holds."""

    added_allow: tuple[str, ...]
    already_present: tuple[str, ...]
    withdrawn_allow: tuple[str, ...] = ()

    @property
    def granted(self) -> frozenset[str]:
        """Every rule the overlay grants, added or already present."""
        return frozenset((*self.added_allow, *self.already_present))


def settings_document(text: str) -> dict[str, Any]:
    """The local settings file as an object, refusing a shape the merge could not read back.

    `write.py` adds allow rules to this document and takes back the hook entries an earlier
    release merged into it, so both have to agree about what a readable document is — and a shape
    this cannot read is refused rather than filtered, because what a filter drops here is
    somebody's own setting and nothing would say it went.
    """
    return settings_object(text, LOCAL_SETTINGS)


def _read(path: Path, *, share: Path | None = None, label: str | None = None) -> str:
    """A file's text, or an empty string when there is no such file.

    `label` is how a refusal names the file, for one the project names: the project's own
    `.claude/settings.local.json` is named as such and never by the machine's absolute path,
    as `attach.write` names `.gitignore` and the ledger. An overlay source keeps its path.

    `share` is for the overlay's sources under `projects/<name>/` and nothing else, and names that
    directory: a path its spelling rules out (`binding.cannot_exist`) is a file the overlay does not
    have when `project.name` picks out no directory there (`_names_no_directory`), because the one
    free part of that spelling is the name and a repository chooses it. Every other fault is the
    overlay failing to answer and stays a `Failure`, which `doctor` reports as a warning: no
    permission, a directory where the file goes, and a path ruled out *below* a directory the name
    does pick out -- the name holds no `/`, so a file where `claude/` goes is the owner's own
    state, and read as absent it granted nothing: the owner's entries read red and `attach`
    stripped them. So is any fault under `common/` or a tag's `groups/<tag>/`, whose spelling the
    overlay alone chooses. The project's own `.claude/` is never read this way: a `.claude` that is
    a file has to stop `attach` before it writes, and read as an empty settings file it would stop
    only at the write of that file.
    """
    with fsops.reading(label or path, Failure):
        try:
            # A regular file only (`fsops.read_regular_text`): `.claude/settings.local.json` is a
            # path a clone can commit, and a link there to `/dev/zero` read until memory ran out,
            # one to a FIFO waited for a writer. Anything else is a file that cannot be read.
            return read_regular_text(path)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            if share is not None and cannot_exist(exc) and _names_no_directory(share):
                return ""
            raise


def _names_no_directory(share: Path) -> bool:
    """Whether `share`, the overlay's `projects/<name>`, is no directory, so nothing below it is
    the owner's and a path through it that cannot exist is one the overlay has no file at.

    Asked of the share itself because `cannot_exist` says only that *some* component of a path is
    not a directory or is too long, never which. Only the share's own answer is about the name,
    and any component below a share that is a directory is the owner's own state. Whatever is
    absent counts, `projects/` or the share: nothing is below a directory that is not there, so
    nothing there is the owner's. An overlay need not keep `projects/` at all, and a long name
    under a long overlay root can put a file under an absent share past the longest path while
    the share itself is not.

    Every caller reaches this past a share that is absent or a directory, so those are the two
    answers that decide anything: `attach` and `--check` refuse any other share above their reads
    (`binding.refuse_unless_share_can_exist`), and `doctor` opens `projects/<name>/` only for a
    binding whose record it has just read there. So a `projects/` that is a file is not told apart
    from a share a file holds: no caller can reach either.
    """
    try:
        mode = share.stat().st_mode
    except FileNotFoundError:
        # No share, or no `projects/` above it: the overlay has no directory for this project.
        return True
    except OSError as exc:
        return cannot_exist(exc)
    return not stat.S_ISDIR(mode)


def allow_list(raw: dict[str, Any], label: str) -> list[str]:
    """The `permissions.allow` list of a settings document `settings_object` read, or a refusal.

    **The one reader of that list**, for `--check` and for the real run alike: `diff_permissions`
    reads every source and the project's own file through it, and `write.py` reads the file it
    merges into and withdraws from through it, so `--check` refuses exactly the documents the real
    run refuses. Two copies of it disagreed about `null`: one read a `null` `permissions` or
    `allow` as no rules, the other refused it, so `--check` exited 0 promising a rule for a file
    the real run then refused. A key that is absent is no rules; a key that is there must hold an
    object, and then a list of strings, and `null` is a value that is neither. A shape this cannot
    read is a refusal and never a filter, for the reason `hook_groups` gives. `label` names the
    file in the refusal.
    """
    if "permissions" not in raw:
        return []
    permissions = raw["permissions"]
    if not isinstance(permissions, dict):
        raise EntriesError(f"{label}: 'permissions' is not an object")
    if "allow" not in permissions:
        return []
    allow = permissions["allow"]
    if not isinstance(allow, list) or not all(isinstance(rule, str) for rule in allow):
        raise EntriesError(f"{label}: 'permissions.allow' is not a list of strings")
    return list(allow)


def _allow_rules(document: str, path: Path) -> tuple[str, ...]:
    """The `permissions.allow` list of one settings-shaped document, read by `allow_list`."""
    label = str(path)
    return tuple(allow_list(settings_object(document, label), label))


def hook_groups(path: Path, *, share: Path | None) -> dict[str, list[dict[str, Any]]]:
    """`event -> groups` out of a `hooks.json`-shaped document, refusing a shape it cannot read:
    each group's `hooks` a list, when it is there, of entries that each carry a command.

    A shape this cannot read is a refusal and never a filter, for `scaffold.entries`' own
    reason: what is dropped silently here is an entry the owner put in their overlay on
    purpose, and nothing would say it never arrived. `share` is `_read`'s.
    """
    hooks = settings_object(_read(path, share=share), str(path)).get("hooks", {})
    if not isinstance(hooks, dict):
        raise EntriesError(f"{path}: 'hooks' is not an object")
    found: dict[str, list[dict[str, Any]]] = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list) or not all(isinstance(g, dict) for g in groups):
            raise EntriesError(f"{path}: 'hooks.{event}' is not a list of entry groups")
        for group in groups:
            # Absent is no entries; `null`, or any other value that is not a list, is refused, as
            # `allow_list` refuses one where its list goes.
            entries = group.get("hooks", [])
            if not isinstance(entries, list):
                raise EntriesError(f"{path}: an entry group's 'hooks' is not a list")
            for entry in entries:
                if not (isinstance(entry, dict) and isinstance(entry.get("command"), str)):
                    raise EntriesError(f"{path}: an entry under {event} has no command")
        found[str(event)] = [dict(g) for g in groups]
    return found


def codex_rules(binding: Binding) -> tuple[tuple[str, Path], ...]:
    """Every standing-rule file `attach` would place under `.codex/rules/`, as (target, source).

    Enumeration here, copying in `write.py`, because `--check`'s whole promise is "read it
    before the real run" and `docs/cli.md` lists `.codex/rules/` under **Writes**: half a write
    that never appears in the report makes that promise false. A file Codex reads as standing
    instruction is exactly the kind of thing an owner wants named before it lands.

    This is **reporting and not gating**, as everything the overlay grants is: the overlay is the
    machine owner's own artifact, so `attach` shows what it places and proceeds.

    The files are the winners of the plates' fold (`attach.items`), so of two files sharing a
    name the later plate's lands, in its place; the pair is returned rather than two lists so the
    caller cannot pair them up differently from the way the write does.

    A directory this user cannot list, or one whose entries cannot be asked about (`0o444`: the
    listing works and `stat` of an entry does not), fails as a rule file that cannot be read
    does in `write._codex_rule_texts`: one fault class, the overlay holding something this user
    cannot read, and one classification. The project's directory carries `project.name`, so the
    path prints through `quoted`.
    """
    winners = fold(binding.plates, CODEX_RULE, _unlisted).winners
    return tuple((f"{CODEX_RULES}/{name}", source) for name, source in winners.items())


def _unlisted(sentence: str) -> Failure:
    return Failure(f"{sentence}, so nothing was written")


def claude_sources(held: Sequence[Plate], name: str) -> tuple[tuple[Path, Path | None], ...]:
    """The per-harness file `name` of each of the plates `held`, in plate order, each with the
    directory `project.name` picks for it, when it has one (`_read`'s `share`): the project's
    plate alone. The permission diff and the render's hook union read their sources here."""
    return tuple(
        (
            plate.directory / PROJECT_CLAUDE / name,
            plate.directory if plate.kind is PlateKind.PROJECT else None,
        )
        for plate in held
    )


def local_document(root: Path) -> str:
    """The project's own `settings.local.json`, or an empty string when it has none."""
    return _read(root / LOCAL_SETTINGS, label=LOCAL_SETTINGS)


# Room for the one key `attach` adds to the text it writes back after the merge, the
# `autoMemoryDirectory` pointer at the composed store: the key's name and a path, and no path a
# filesystem resolves passes 4,096 bytes.
_FALLBACK_ROOM = 8 * 1024


def _refuse_unless_written_back(document: str) -> None:
    """Refuse a settings document whose write-back the next read would refuse.

    `attach` writes the file back indented -- the merge, and the `autoMemoryDirectory` pointer
    after it -- and a short document many levels deep grows by its depth on every line: 12 KB six
    thousand lists deep is 72 MB, past `fsops.REGULAR_READ_LIMIT`, so the read after the write
    refused, after every other write of the run, and so did every `detach` after it. Asked where
    `attach` and `--check` both read the file, so the two answer alike and `attach` answers before
    its first write; and of every document, whether or not this run writes it back, since a later
    run that adds one rule would.
    """
    text = settings_text(settings_document(document), LOCAL_SETTINGS)
    if len(text) + _FALLBACK_ROOM > fsops.REGULAR_READ_LIMIT:
        raise ParserLimitError(f"{LOCAL_SETTINGS} {WRITTEN_PAST}")


def diff_permissions(root: Path, binding: Binding) -> PermissionDiff:
    """What attaching `binding` would add to `root`, without writing a byte."""
    # This line is the whole of the committed-settings rule in the module docstring:
    # `.claude/settings.json` sits one name away from both sources and is not on it.
    sources = claude_sources(binding.plates, PERMISSIONS_FILE)
    granted = [
        rule
        for source, share in sources
        for rule in _allow_rules(_read(source, share=share), source)
    ]
    document = local_document(root)
    _refuse_unless_written_back(document)
    # Named by its project path, as every refusal of the project's own settings file is.
    held = set(_allow_rules(document, Path(LOCAL_SETTINGS)))
    added_allow = tuple(dict.fromkeys(rule for rule in granted if rule not in held))
    already = tuple(dict.fromkeys(rule for rule in granted if rule in held))
    return PermissionDiff(added_allow, already)


def withdrawing(
    diff: PermissionDiff, recorded: Iterable[str], held: Collection[str]
) -> PermissionDiff:
    """`diff` with the rules to take back: those `recorded` (the previous attach ledger's `allow`,
    the one witness of which rules are `attach`'s) that the overlay no longer grants and the
    settings file still `held`. A rule the owner added by hand is not recorded and stays; a
    recorded rule the owner removed by hand is not held and is nothing to take back.

    The ledger is a checkout file a branch can write, so it is no anchor against one: the worst a
    committed `allow` achieves is taking a permission away, one the owner typed included. `detach`
    accepts the same bound.

    What the overlay grants is `diff`'s own (`PermissionDiff.granted`)."""
    granted = diff.granted
    taken = tuple(dict.fromkeys(rule for rule in recorded if rule in held and rule not in granted))
    return replace(diff, withdrawn_allow=taken)
