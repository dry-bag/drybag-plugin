"""The overlay's rules: standing ones for every session, path-scoped ones once per file per session.

**Where they live.** `claude/rules/*.md` in each plate of the overlay (`config.plates`):
`common/`, each tag of the project (`groups/<tag>/`), then `projects/<project>/`, beside the
overlay's `permissions.json` and `hooks.json`. A rule whose frontmatter has a `paths:` key is
path-scoped; any other is a standing rule. The plates come in that order, each in name order, and
a later plate's rule replaces the earlier plate's rule of the same file name and takes its place,
as every named item does (`attach.items`).

**For the bound project only.** `<project>` is the one this machine's binding record names for the
checkout's id (`drybag.binding.bound_project`), never a name the repository supplies, so a clone
that names one of the owner's projects in its `drybag.toml` hears no rule. The overlay is the one
the machine file records.

**How they reach a session.** A standing rule, through the core plugin's `SessionStart`, on every
event that matcher covers, since a compacted or cleared context has lost it. A path-scoped rule,
through the `PostToolUse` entry the rendered plugin carries only when the overlay holds one
(`attach.render`): on a `Read`, `Edit` or `Write` of a file one of its `paths:` globs matches,
relative to the checkout's root, once per file per session. The globs are `*` and `?` within one
path segment, `**` across segments and `{a,b}` for alternatives, matched against the whole path.

**The once-per-file record** is an empty file under `<data>/sessions/`, named by the session id and
the file's absolute path, each hashed to a fixed width, since both come off a hook's stdin. A
payload with no session id keeps no record, so its rules are said on every read rather than
silenced under a name anything could lay down in advance; and a record that cannot be written costs
the same, never the rule.

**What reaches the model.** A rule's body is the owner's, delivered as they wrote it, like an
overlay note; the one path printed is the file the agent touched, through `printed.printable`.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.attach.items import RULE, fold
from drybag.binding import bound_project
from drybag.config.overlay import overlay_root
from drybag.config.paths import data_directory
from drybag.config.plates import Plate, plates
from drybag.errors import Failure
from drybag.printed import printable, quoted

# Under the machine's data directory: one directory of once-per-file records per session.
SESSIONS = "sessions"
STANDING_LEAD = "Standing rules from your drybag overlay, for every session in this checkout:"
SCOPED_LEAD = "A rule from your drybag overlay for {path}, said once per file in a session:"
# What `SCOPED_LEAD` names in place of a path outside the path grammar.
THIS_FILE = "this file"
# The longest rule file read. A named cap (CONTRIBUTING.md#named-caps): a rule is read on every
# file a session touches, and what a hook hands the model is cut at the harness's own cap
# (`native_caps.hook_output_chars`) anyway, so 64 KiB is far past any rule that could arrive
# whole; no shipped file states it.
_READ_LIMIT = 64 * 1024
_FENCE = "---"
_PATHS = "paths"


@dataclass(frozen=True)
class Rule:
    """One rule file: its name, its globs (`None` for a standing rule) and its body."""

    name: str
    paths: tuple[str, ...] | None
    body: str


def _unreadable(sentence: str) -> Failure:
    return Failure(f"{sentence}, so no rule was read")


def _items(value: str) -> list[str]:
    """A frontmatter list's items: split at the commas outside quotes and braces, so a glob's
    `{ts,tsx}` stays one item, each unquoted."""
    items: list[str] = []
    current: list[str] = []
    quote, depth = "", 0
    for char in value:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
        elif char in "'\"":
            quote = char
        elif char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            depth += {"{": 1, "}": -1}.get(char, 0)
            current.append(char)
    items.append("".join(current))
    return [item.strip() for item in items if item.strip()]


def _paths(front: list[str]) -> tuple[str, ...] | None:
    """The `paths:` key of a frontmatter's lines, or `None` when it has none: a flow list
    (`["a/**", b]`), a block list on the lines below it, or a comma-separated value."""
    for at, line in enumerate(front):
        key, colon, value = line.partition(":")
        if not colon or key != _PATHS:
            continue
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            return tuple(_items(value[1:-1]))
        if value:
            return tuple(_items(value))
        found: list[str] = []
        for item in front[at + 1 :]:
            listed = item.strip()
            if not listed.startswith("-"):
                break
            found.extend(_items(listed[1:]))
        return tuple(found)
    return None


def _parsed(name: str, text: str) -> Rule:
    lines = text.splitlines()
    if lines and lines[0].strip() == _FENCE:
        for at in range(1, len(lines)):
            if lines[at].strip() == _FENCE:
                body = "\n".join(lines[at + 1 :]).strip()
                return Rule(name, _paths(lines[1:at]), body)
    return Rule(name, None, text.strip())


def read(held: Sequence[Plate]) -> list[Rule]:
    """The overlay's rules in the plates `held`: the winners of the plates' fold (`attach.items`), a
    later plate's rule replacing an earlier plate's of its name and taking its place. A directory
    or a file that cannot be read is a `Failure`."""
    found: list[Rule] = []
    winners = fold(held, RULE, _unreadable).winners
    for name, path in winners.items():
        with fsops.reading(quoted(str(path)), _unreadable):
            text = fsops.read_regular_text(path, limit=_READ_LIMIT)
        found.append(_parsed(name, text))
    return found


def scoped_exist(held: Sequence[Plate]) -> bool:
    """Whether the plates `held` hold a path-scoped rule, which decides whether the render carries
    the `PostToolUse` entry."""
    return any(rule.paths is not None for rule in read(held))


def _bound(root: Path) -> list[Rule]:
    """The rules of the project this machine binds `root` to, in the overlay the machine file
    records; none for an unbound checkout or a machine that records no overlay."""
    bound = bound_project(root)
    overlay = overlay_root(None) if bound is not None else None
    if bound is None or overlay is None:
        return []
    return read(plates(overlay, bound.project)[0])


def standing(root: Path) -> str:
    """The bound project's standing rules, under one fixed lead, or nothing."""
    bodies = [rule.body for rule in _bound(root) if rule.paths is None and rule.body]
    return "\n\n".join((STANDING_LEAD, *bodies)) if bodies else ""


def _glob(pattern: str) -> re.Pattern[str]:
    """`pattern` as a regular expression matched against a whole `/`-separated path."""
    out: list[str] = []
    at, depth = 0, 0
    pattern = pattern.removeprefix("./").lstrip("/")
    while at < len(pattern):
        if pattern.startswith("**/", at):
            out.append("(?:.*/)?")
            at += 3
            continue
        if pattern.startswith("**", at):
            out.append(".*")
            at += 2
            continue
        char = pattern[at]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "{":
            out.append("(?:")
            depth += 1
        elif char == "}" and depth:
            out.append(")")
            depth -= 1
        elif char == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(char))
        at += 1
    # An unclosed brace matches as written rather than as a broken expression.
    return re.compile("".join(out) if depth == 0 else re.escape(pattern))


def _inside(base: Path, target: Path) -> str | None:
    return target.relative_to(base).as_posix() if target.is_relative_to(base) else None


def _relative(root: Path, path: str) -> tuple[Path, str] | None:
    """The file `path` names, absolute and normalised, and its path relative to `root`; `None`
    when it is not inside `root`. A relative `path` is the checkout's. Asked as written first,
    and then with links resolved, for a root the harness spells through a link."""
    target = Path(os.path.normpath(root / path))
    relative = _inside(Path(os.path.normpath(root)), target)
    if relative is None:
        relative = _inside(root.resolve(), target.resolve())
    return None if relative in (None, ".") else (target, relative)


def _segment(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _record(session_id: str, target: Path) -> str:
    """The once-per-file record's place under `<data>/sessions/`, keyed by session and file."""
    return f"{_segment(session_id)}/{_segment(str(target))}"


def _said(base: Path, record: str) -> bool:
    """Whether the record is the regular file a delivery wrote, asked without following it."""
    try:
        return stat.S_ISREG(os.lstat(base / record).st_mode)
    except OSError:
        return False


def _mark(base: Path, record: str) -> None:
    try:
        # The plain-path form for the directory itself, as `binding.record` writes this machine's
        # own data directory; the record inside it goes through the walk that refuses a symlink.
        base.mkdir(parents=True, exist_ok=True)
        fsops.write_within(base, record, "")
    except (OSError, fsops.UnsafePath):
        return None


def scoped_for(root: Path, path: str, session_id: str | None) -> str:
    """The bound project's path-scoped rules whose globs match `path`, under one fixed lead, the
    first time `session_id` touches that file; nothing on every later time, and nothing for a file
    outside `root` or one no rule matches."""
    found = _relative(root, path)
    if found is None:
        return ""
    target, relative = found
    bodies = [
        rule.body
        for rule in _bound(root)
        if rule.paths is not None
        and rule.body
        and any(_glob(glob).fullmatch(relative) for glob in rule.paths)
    ]
    if not bodies:
        return ""
    if session_id is not None:
        base, record = data_directory() / SESSIONS, _record(session_id, target)
        if _said(base, record):
            return ""
        _mark(base, record)
    lead = SCOPED_LEAD.format(path=printable(relative, withheld=THIS_FILE))
    return "\n\n".join((lead, *bodies))
