"""What the sync may move: the memory-only rules, and only these.

A path is memory when the pathspecs below match it -- `glob` magic, so `*` stays inside one
directory and `projects/a/checkout/memory/x` is not a note -- but for a project's `MEMORY.md`,
which each machine renders for itself and the sync never stages, writes or moves (a range that
changes one is outside memory), and only while git records it as a
plain, non-executable file: a link (120000), an embedded repository (160000) or an executable
(100755) under a memory name is refused, since the name would then point somewhere else or run.
And only while it is a note: a name ending in `.md` with no component from the group's or
project's name down that is empty or starts with "." (no `.claude/skills/...`, no
`.gitattributes`, no project called `..`), and text to git, since a note `git diff --numstat` calls
binary is one no diff of it shows.

A commit is memory-only when it lists at least one path and every path it lists is memory; renames
are off, so moving `guard.py` into `common/memory/` lists `guard.py` too. A range is memory-only
when every commit in it is and the tree changed only memory between the merge base and the tip: a
merge lists no path of its own, so the first rule refuses it and the second catches its resolution.
The anchor is git's own record of the paths and modes of the sha judged, and the layout constants
of `memory`, which no repository byte reaches.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from drybag import fsops
from drybag.config.plates import GROUPS
from drybag.gitenv import QUERY_TIMEOUT_SECONDS
from drybag.memory.api import COMMON, INDEX_NAME, PROJECTS, STORE_DIR
from drybag.sync.git import GitError, checked, git, left

# A project's rendered index, which each machine renders for itself: left out under `projects`.
NOT_AN_INDEX = f":(top,exclude,glob){PROJECTS}/*/{STORE_DIR}/{INDEX_NAME}"
# The pathspecs of the notes, their exclusion and the parse below all derive from the layout.
MEMORY_PATHSPEC: tuple[str, ...] = (
    f":(top,glob){COMMON.as_posix()}/**",
    f":(top,glob){GROUPS}/*/{STORE_DIR}/**",
    f":(top,glob){PROJECTS}/*/{STORE_DIR}/**",
    NOT_AN_INDEX,
)
_NAMED_TOPS = {"group": GROUPS, "project": PROJECTS}


@dataclass(frozen=True)
class MemoryPath:
    """A top-relative path the pathspecs match, taken apart: its `scope` (`common`, `group` or
    `project`), the group's or project's `name` (`None` for the shared notes) and what lies
    `below` the memory root."""

    scope: str
    name: str | None
    below: tuple[str, ...]

    @property
    def root(self) -> str:
        """`common/memory`, `groups/<name>/memory` or `projects/<name>/memory`."""
        if self.name is None:
            return COMMON.as_posix()
        return f"{_NAMED_TOPS[self.scope]}/{self.name}/{STORE_DIR}"

    @property
    def is_note(self) -> bool:
        """Whether a file here may move at all, by its name: every component from the group's or
        project's name down is a plain name -- not empty, not `.` or `..`, not starting with "."
        -- and the last ends in `.md`.

        So no `.claude/skills/...` and no `.gitattributes` under memory, and no project called
        `..` or `.git`: such a tree passes the pathspec yet cannot be checked out, and moving
        `main` to it would wedge every later job. The mode and the content are judged separately
        (`entry_offends`).
        """
        names = ((self.name,) if self.name is not None else ()) + self.below
        plain = all(name and not name.startswith(".") for name in names)
        return plain and self.below[-1].endswith(".md")


def memory_path(path: str) -> MemoryPath | None:
    """`path` parsed by the layout, or `None` when the pathspecs do not match it.

    The same match as `MEMORY_PATHSPEC` with `glob` magic: `*` is one non-empty directory name, a
    final `**` is anything inside, and a project's own `MEMORY.md` is left out. A test holds it
    equal to git's own. A match is not enough to
    move a path: being a note (`MemoryPath.is_note`) and what git records it as (`entry_offends`)
    are judged on top. The path is as git lists it, `-z`, so quoted or Cyrillic names arrive as
    written.
    """
    parts = path.split("/")
    common = list(COMMON.parts)
    if len(parts) > len(common) and parts[: len(common)] == common and all(parts[len(common) :]):
        return MemoryPath("common", None, tuple(parts[len(common) :]))
    for scope, top in _NAMED_TOPS.items():
        if (
            len(parts) >= 4
            and parts[0] == top
            and parts[1]
            and parts[2] == STORE_DIR
            and all(parts[3:])
        ):
            if scope == "project" and parts[3:] == [INDEX_NAME]:
                return None
            return MemoryPath(scope, parts[1], tuple(parts[3:]))
    return None


def unsafe_parent(overlay: Path, path: str) -> str | None:
    """The first directory above the top-relative `path` that is a link or not a directory, or
    `None` when each is a real directory or absent. Anchor: what is at each ancestor, unfollowed."""
    parts = path.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        current = overlay.joinpath(*parts[:depth])
        if fsops.is_symlink(current) or (fsops.exists(current) and not fsops.is_dir(current)):
            return "/".join(parts[:depth])
    return None


# What a memory path may become: a regular, non-executable file, or nothing (a deletion). A link
# (120000) would make a "note" point at a hook or outside the overlay, a gitlink (160000) would
# make it another repository, and an executable (100755) is executable content, which never
# moves. `000000` is git's "nothing here", which only a deletion (`D`) may end in.
ALLOWED_MODES = frozenset({"100644", "000000"})
DELETED_MODE = "000000"


@dataclass(frozen=True)
class RawEntry:
    """One line of `--raw` output: the mode git recorded after the change, the status letter, the
    path and the sha of the new side (zeros for a deletion). `binary` is set when `--numstat`
    printed "-\\t-" for it: git will not show its text."""

    new_mode: str
    status: str
    path: str
    blob: str
    binary: bool = False


def entry_offends(entry: RawEntry) -> bool:
    """Whether this change may not move: not a memory path, not a plain file afterwards, not a
    note by name -- deleted or not, it was never a note -- or content git calls binary. A deletion
    carries no content, so the last does not apply to it."""
    parsed = memory_path(entry.path)
    if parsed is None or not parsed.is_note or entry.new_mode not in ALLOWED_MODES:
        return True
    if entry.new_mode == DELETED_MODE:
        return entry.status != "D"
    return entry.binary


def _entries(fields: list[str], start: int, what: str) -> tuple[list[RawEntry], int]:
    """The `--raw` pairs (`<meta>\\0<path>\\0`) of `--raw --numstat -z` output from
    `fields[start:]`, the binary ones marked from the `<added>\\t<deleted>\\t<path>\\0` lines after
    them, and where the stream goes on. The pairs strictly alternate, so a path that starts with
    ":" or a newline is still a path."""
    entries: list[RawEntry] = []
    index = start
    while index < len(fields) and fields[index].startswith(":"):
        meta = fields[index][1:].split(" ")
        path = fields[index + 1] if index + 1 < len(fields) else ""
        if len(meta) != 5 or not path:
            raise GitError(f"{what}: not a --raw line: {fields[index]!r}")
        entries.append(RawEntry(meta[1], meta[4], path, meta[3]))
        index += 2
    binary: set[str] = set()
    while index < len(fields) and fields[index] != "":
        counts = fields[index].split("\t", 2)
        if len(counts) != 3:
            raise GitError(f"{what}: not a --numstat line: {fields[index]!r}")
        if counts[0] == counts[1] == "-":
            binary.add(counts[2])
        index += 1
    return [replace(entry, binary=entry.path in binary) for entry in entries], index


# How `log` and `diff` list a change: modes and binary-ness, never renames, no color or helper.
_RAW = ("--raw", "--numstat", "--no-abbrev", "-z", "--no-renames", "--no-color")


# How many fields stand before the path in each kind of `git status --porcelain=v2` entry: a
# change, an unmerged path and an untracked one (renames are off).
_BEFORE_PATH = {"1": 8, "u": 10, "?": 1}


@dataclass(frozen=True)
class Status:
    """What one `git status` says: the dirty memory paths, commits ahead of `@{u}`, `HEAD`, and
    which of the paths the index does not hold at all."""

    paths: tuple[str, ...]
    ahead: int
    head: str | None
    untracked: tuple[str, ...] = ()


def status(repo: Path, *, timeout: float) -> Status:
    """The memory paths that differ anywhere between `HEAD`, the index and the working tree, with
    the branch line, from one `git status`: the trigger's question and the commit's.

    `-z`, so a name with a space, a quote or a newline comes back as written, and
    `--no-optional-locks`, so the call never writes the index while the owner's own git may be
    running. A trigger, not a verdict: a link with a memory name is reported like any note, and
    the commit step refuses it by its mode.
    """
    result = checked(
        git(
            repo, "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-z",
            "--untracked-files=all", "--no-renames", "--", *MEMORY_PATHSPEC,
            timeout=timeout,
        ),
        "git status",
    )  # fmt: skip
    paths: list[str] = []
    untracked: list[str] = []
    ahead, head = 0, None
    for record in result.out.split("\0"):
        if record.startswith("# branch.oid "):
            oid = record.removeprefix("# branch.oid ").strip()
            head = oid if oid != "(initial)" else None
        elif record.startswith("# branch.ab "):  # "+<ahead> -<behind>", only with an upstream
            text = record.removeprefix("# branch.ab ").split()[0].lstrip("+")
            ahead = int(text) if text.isdigit() else 0
        elif record and not record.startswith("# "):
            paths.append(record.split(" ", _BEFORE_PATH.get(record[0], 1))[-1])
            untracked.extend(paths[-1:] if record[0] == "?" else [])
    return Status(tuple(dict.fromkeys(paths)), ahead, head, tuple(untracked))


def commit_entries(
    repo: Path,
    rev_range: str,
    *,
    timeout: float = QUERY_TIMEOUT_SECONDS,
) -> list[tuple[str, list[RawEntry]]]:
    """`[(sha, entries)]` for every commit in `rev_range`, newest first, modes included.

    Renames are off, so a rename lists both sides. Merges explicitly list nothing
    (`--diff-merges=off`, whatever `log.diffMerges` says): a merge's own resolution is judged by
    the tree bound in `range_is_memory_only`, never by a per-parent listing.
    """
    what = f"git log {rev_range}"
    result = checked(
        git(
            repo,
            "log",
            *_RAW,
            "--diff-merges=off",
            "--no-show-signature",
            "--format=%x00%H",
            rev_range,
            "--",
            timeout=timeout,
        ),
        what,
    )
    # Per commit: "\0<sha>\0", then, if it lists anything, "\n" and the `--raw` pairs. The newline
    # belongs to git's separator and is only ever before the FIRST meta of a commit; a path may
    # itself begin with a newline, and is never touched.
    commits: list[tuple[str, list[RawEntry]]] = []
    fields = result.out.split("\0")
    index = 0
    while index < len(fields):
        if fields[index] != "":
            raise GitError(f"{what}: expected a commit, found {fields[index]!r}")
        if index + 1 >= len(fields) or fields[index + 1] == "":
            index += 1  # the stream's final terminator
            continue
        sha = fields[index + 1]
        index += 2
        if index < len(fields) and fields[index].startswith("\n"):
            fields[index] = fields[index][1:]
        entries, index = _entries(fields, index, what)
        commits.append((sha, entries))
    return commits


def diff_entries(
    repo: Path,
    *revisions: str,
    paths: Sequence[str] = (),
    timeout: float = QUERY_TIMEOUT_SECONDS,
) -> list[RawEntry]:
    """The changes of `git diff --raw <revisions>`, in git's order; with `paths`, of exactly those
    paths (literal pathspecs), never of the whole tree.

    The commit step runs it as `HEAD` against the working tree for the notes it may commit, an
    intent-to-add entry standing for each new one: what git would record for each, its mode and
    whether it is text, judged before any commit is made.
    """
    what = f"git diff {' '.join(revisions)}"
    literal = {"GIT_LITERAL_PATHSPECS": "1"} if paths else None
    result = checked(
        git(
            repo, "diff", *_RAW, "--no-ext-diff", *revisions, "--", *paths,
            timeout=timeout, env=literal,
        ),
        what,
    )  # fmt: skip
    fields = result.out.split("\0")
    entries, index = _entries(fields, 0, what)
    if any(fields[index:]):
        raise GitError(f"{what}: unexpected output after the --raw lines")
    return entries


def range_is_memory_only(
    repo: Path,
    base: str,
    tip: str,
    *,
    timeout: float = QUERY_TIMEOUT_SECONDS,
    commits: list[tuple[str, list[RawEntry]]] | None = None,
) -> tuple[bool, list[str]]:
    """Whether `base..tip` may move automatically, and what stops it if not; `commits` is the
    range as `commit_entries` lists it, when the caller has listed it already.

    The offending list holds every path any commit changes that may not move (`entry_offends`),
    the sha of every commit that lists no path (a merge, an empty commit), and every such path of
    the diff from the merge base to `tip`, in that order and without repeats. An empty range is
    memory-only; callers decide separately whether there is anything to move.
    """
    offending: list[str] = []
    deadline = time.monotonic() + timeout
    what = f"the range check of {base}..{tip}"

    def offend(item: str) -> None:
        if item not in offending:
            offending.append(item)

    if commits is None:
        commits = commit_entries(repo, f"{base}..{tip}", timeout=left(deadline, what))
    for sha, entries in commits:
        if not entries:
            offend(sha)
        for entry in entries:
            if entry_offends(entry):
                offend(entry.path)
    merge_base = git(repo, "merge-base", base, tip, timeout=left(deadline, what))
    if merge_base.code == 1 and not merge_base.timed_out:
        # No common history: everything in the tip is new, so measure from the empty tree.
        empty = git(
            repo,
            "hash-object",
            "-t",
            "tree",
            "--stdin",
            timeout=left(deadline, what),
            stdin=b"",
        )
        bound = checked(empty, "git hash-object").out.strip()
    else:
        bound = checked(merge_base, f"git merge-base {base} {tip}").out.strip()
    for entry in diff_entries(repo, bound, tip, timeout=left(deadline, what)):
        if entry_offends(entry):
            offend(entry.path)
    return not offending, offending
