"""Regenerate the "Design and plan trail" listing inside the roadmap.

The roadmap's prose owns phase status; this listing makes the corpus navigable: every spec and
plan, grouped by theme, annotated with its delivery state. Delivered is the default; anything
else is declared in `trail.toml` beside the roadmap — that file is the one part a human
maintains, and it is the forward track in machine-readable form. A document
appearing for the first time has to declare its state, `delivered` included: a design is
written before the thing is built, so the first listing of one would otherwise assert that
unimplemented work has shipped.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from posixpath import relpath
from typing import TYPE_CHECKING, Any

from drybag import fsops
from drybag.config.paths import contained
from drybag.config.reading import UNPARSEABLE, toml_position
from drybag.docs.hygiene import TRAIL_MARKER, TRAIL_MARKER_LINE, read_document
from drybag.docs.themes import ThemePattern, compile_theme
from drybag.errors import Failure
from drybag.findings import LISTED_LIMIT, Finding, listed
from drybag.fsops import utf_8_name
from drybag.gitenv import NO_ANSWER, git_run, in_work_tree
from drybag.printed import clipped, quoted

if TYPE_CHECKING:
    from drybag.config.schema import Config

# The marker and its anchored pattern are `docs.hygiene`'s, defined once: the budget reader cuts
# the roadmap's prose at the same line this module rebuilds from, and two spellings of one
# literal are two things that can disagree. Bound here for readability, not redefined.
MARKER = TRAIL_MARKER
END_MARKER = "<!-- end design and plan trail -->"
TRAIL_FILE = "trail.toml"
UNFILED = "Unfiled"
# The most `[[theme]]` tables a `trail.toml` holds: with `themes.PATTERN_MAX_CHARS`, what one
# document name can cost to place, whatever the repository wrote.
THEMES_MAX = 32
TOO_MANY_THEMES = f"a trail.toml holds at most {THEMES_MAX} [[theme]] tables"
# The `trail` gate's finding: a listing that is not what `rebuild` would write.
TRAIL_STALE = "trail-stale"
DELIVERED = "delivered"
_ROW = re.compile(r"^- \[`([^`]+)`\]", re.MULTILINE)
# Every value this file interpolates into the listing has to survive being written into it
# verbatim. `rebuild` locates the block it replaces with `text.find(END_MARKER, …)`, so an end
# marker inside a `label` or a `state` splits the block there, and everything past the split
# falls outside the region the next run rewrites: three successive `drybag docs trail` runs
# grew the roadmap by its own height each time, `docs trail --check` became permanently stale
# with nothing an operator could do to satisfy it, and the prose the value carried after the
# marker settled into the roadmap — a document agents load, with no delimited region and no
# trust record behind it. A newline is the same defect one step earlier: it is what lets a value
# put a marker, or a heading, alone on a line, which is where `TRAIL_MARKER_LINE` and the budget
# reader's cut both look.
#
# There are two kinds of such value and they are caught in two places, because they arrive by two
# routes and their remedies differ. A `trail.toml` `label` or `state` is caught in `read_trail`,
# beside the type checks, because a `trail.toml` outside the contract must read as that file
# being wrong. A document's own FILENAME reaches the listing as both the row and the link,
# straight off `glob("*.md")` with no file to be outside a contract — and guarding only the
# first half left the defect whole: a tracked `…-a<!-- end design and plan trail -->b.md` grew
# the roadmap 18 → 22 → 26 lines over three runs, each exiting 0, with the end-marker count
# going 2 → 4 → 6, and `--check` stale forever. It is caught in `render_listing`, after the
# ignored and untracked documents are dropped, so a sibling session's oddly named local file
# cannot fail the command for everybody — the same reason `_untracked` exists.
_UNINTERPOLABLE = (
    "{path}: a {what} is written into the generated listing verbatim, so it must be a single "
    "line and must carry neither `{marker}` nor the end-of-trail comment"
)
# `{row!r}` is repository-authored text in a message: this reaches the terminal of the person who
# ran the command against their own repository, and naming the file is the whole of what makes
# "rename it" actionable. `!r` keeps a name holding a newline or a carriage return on one line, and
# spells a byte that is not UTF-8 as its escape.
_UNLISTABLE = (
    "{row!r} cannot be written into the {roadmap} listing: a document's name becomes both the "
    "row and the link verbatim, so it must be a single line of UTF-8 text and must carry neither "
    "`{marker}` nor the end-of-trail comment — rename the file"
)
_PREAMBLE = (
    "\n\nEvery design and plan document, grouped by theme and annotated with its\n"
    "delivery state. Regenerate with `drybag docs trail` after adding a document.\n"
    f"Delivered is the default; anything else is declared in `{TRAIL_FILE}` beside this\n"
    "file, which mirrors the forward track above.\n"
)


@dataclass(frozen=True)
class Trail:
    themes: tuple[tuple[str, ThemePattern], ...]
    states: dict[str, str]


def trail_target(config: Config) -> str:
    """Where `trail.toml` sits for `config`, beside the roadmap, as a project-relative path.

    Only a location, read off `[paths] roadmap` without touching the disk: whether it may be read
    or written is `contained()`'s to say, which `trail_path` asks.
    """
    return str(PurePosixPath(config.paths.roadmap).parent / TRAIL_FILE)


def trail_path(root: Path, config: Config) -> Path:
    return contained(root, trail_target(config))


def _interpolable(value: str) -> bool:
    """Whether a repository-authored value may be written into the listing unchanged.

    One line as every reader of the roadmap splits it: a carriage return is a line break to
    `read_text`, which turns it into `\\n`, and to Markdown, so a listing carrying one read back
    as a different listing and `--check` was stale forever.
    """
    return not ({"\n", "\r"} & set(value)) and MARKER not in value and END_MARKER not in value


def load_trail(root: Path, config: Config) -> Trail:
    """`config`'s `trail.toml` under `root`, read: `read_trail` of `trail_path`, named in every
    message by `trail_target`."""
    return read_trail(trail_path(root, config), trail_target(config))


def read_trail(path: Path, where: str) -> Trail:
    """`[[theme]]` tables in order (`label`, `pattern`) and a `[states]` table; absent is empty.
    Every value is repository-authored: a pattern outside `docs.themes`' language is a `Failure`,
    and none is ever handed to `re`, whose backtracking a repository's pattern can make
    exponential.

    `where` is how every message names the file — `trail_target`'s project-relative spelling, as
    this area names every file it reads. `path` is what is opened, and it is absolute: printed, it
    named the file by this machine's own layout (`failed: '/home/…/docs/trail.toml': is not valid
    UTF-8`), which is nothing a reader of a CI log can find in the repository."""
    if not fsops.is_file(path):
        return Trail((), {})
    shown = quoted(where)
    try:
        raw: dict[str, Any] = tomllib.loads(read_document(path, where))
    except UNPARSEABLE as exc:
        # Through `config.reading.toml_position`: `tomllib`'s message embeds the source for
        # several of its faults — a duplicate table is reported with the table's name in it —
        # and a TOML key is arbitrary quoted text. A `trail.toml` is a file a repository
        # commits, which is what makes the leak reachable here.
        raise Failure(f"{shown} is not valid TOML {toml_position(exc)}") from None
    themes: list[tuple[str, ThemePattern]] = []
    # `[[theme]]` is an array of tables, so `theme` is a list — but the whole file is
    # repository-authored, and `theme = 1` would otherwise be iterated straight into a
    # `TypeError` the frame reports as an internal error (2). A project's malformed file must
    # read as their file being wrong, never as this tool being broken.
    declared = raw.get("theme", [])
    if not isinstance(declared, list):
        raise Failure(f"{shown}: `theme` must be a list of [[theme]] tables")
    # Every document name is tried against every theme until one matches, so the themes' count
    # multiplies what `PATTERN_MAX_CHARS` bounds for one of them.
    if len(declared) > THEMES_MAX:
        raise Failure(f"{shown}: {TOO_MANY_THEMES}")
    for entry in declared:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("label"), str)
            or not isinstance(entry.get("pattern"), str)
        ):
            raise Failure(f"{shown}: every [[theme]] needs a string `label` and a string `pattern`")
        if not _interpolable(entry["label"]):
            raise Failure(
                _UNINTERPOLABLE.format(path=shown, what="[[theme]] `label`", marker=MARKER)
            )
        try:
            themes.append((entry["label"], compile_theme(entry["pattern"])))
        except ValueError as refused:
            # The rule and not the pattern: the pattern is the repository's own text, and the
            # rule is what fixes it. `compile_theme` raises only its own fixed sentences.
            # The label through `quoted`, as every repository-chosen name in a refusal, and
            # clipped, since nothing bounds its length.
            raise Failure(f"{shown}: theme {clipped(entry['label'])}: {refused}") from None
    states = raw.get("states", {})
    if not isinstance(states, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in states.items()
    ):
        raise Failure(f"{shown}: [states] must map document rows to state strings")
    if not all(_interpolable(state) for state in states.values()):
        raise Failure(_UNINTERPOLABLE.format(path=shown, what="[states] value", marker=MARKER))
    return Trail(tuple(themes), dict(states))


def theme_of(name: str, trail: Trail) -> str:
    for label, pattern in trail.themes:
        if pattern.search(name):
            return label
    return UNFILED


def _ignored(root: Path, paths: list[Path]) -> set[Path]:
    """Paths excluded by the repository's ignore rules alone.

    `--no-index` matters: `check-ignore` consults the index and answers "not ignored" for
    anything currently tracked, which would silently list a local-only document.

    `-z` matters for the same reason it does in `touched_plans`, and this is the one place the
    answer is matched back against the path that was asked about. Without it git C-quotes any
    path holding a non-ASCII byte or a space on OUTPUT — `docs/plans/2026-01-01-caf\303\251.md`
    comes back wrapped in quotes with the bytes escaped — so the answer never equals the path
    this function sent, an ignored document is read as "not ignored" and is listed in the
    roadmap, which is the exact case `--no-index` exists for. `-z` also makes the INPUT
    NUL-separated, so a path is never split on a byte of its own name either."""
    if not paths:
        return set()
    stdin = "\0".join(p.relative_to(root).as_posix() for p in paths)
    code, out = git_run(root, "check-ignore", "--no-index", "--stdin", "-z", stdin=stdin)
    # 1 simply means "nothing matched"; anything else is a tree git cannot speak for.
    if code not in (0, 1):
        _unasked(root, "check-ignore", code, "ignores", "a local-only one")
        return set()
    return {root / name for name in out.split("\0") if name}


def _unasked(root: Path, command: str, code: int, question: str, leak: str) -> None:
    """Fail where git gave no answer inside a work tree; outside one there is nothing to ask.

    Read as an empty set, a failed filter listed every document on disk: a timeout, a `git` that
    could not start or a checkout git refuses (dubious ownership) put `leak` into the committed
    roadmap. Whether this is a repository is read off the disk, because git refuses that
    question the same way (`gitenv.in_work_tree`).
    """
    if not in_work_tree(root):
        return
    cause = NO_ANSWER if code == -1 else f"`git {command}` exited {code}"
    raise Failure(
        f"{cause}, so which documents this repository {question} is not known, and the listing "
        f"would name {leak}; nothing was listed"
    )


def _untracked(root: Path, paths: list[Path]) -> set[Path]:
    """Documents git does not track yet, which the listing must not claim.

    CI checks out the tracked tree and computes the listing from that, so an untracked document
    makes a developer's `--check` disagree with CI over a file CI cannot see — routinely, since
    a sibling session's work-in-progress lands in the same directory. Skipping them keeps the
    two answers identical and matches what the listing is: a generated index OF THE REPOSITORY,
    not of one machine's disk. Falls back to "nothing untracked" outside a work tree, so a
    non-git tree keeps working instead of silently emptying itself; inside one, a question git
    gave no answer to fails."""
    if not paths:
        return set()
    code, out = git_run(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *(str(p.relative_to(root)) for p in paths),
    )
    if code != 0:
        _unasked(root, "ls-files", code, "tracks", "an untracked one")
        return set()
    return {root / name for name in out.split("\0") if name}


def _documents(root: Path, config: Config) -> list[tuple[str, str, Path]]:
    """`(row, link, path)` per document: the row is `<dirname>/<file>`, the link is relative
    to the roadmap's own directory."""
    roadmap_dir = str(PurePosixPath(config.paths.roadmap).parent)
    found: list[tuple[str, str, Path]] = []
    for configured in (config.paths.specs, config.paths.plans):
        directory = contained(root, configured)
        for path in sorted(directory.glob("*.md")):
            row = f"{PurePosixPath(configured).name}/{path.name}"
            found.append((row, relpath(f"{configured}/{path.name}", roadmap_dir), path))
    return found


def render_listing(root: Path, config: Config, trail: Trail) -> str:
    documents = _documents(root, config)
    paths = [path for _, _, path in documents]
    skip = _ignored(root, paths) | _untracked(root, paths)
    buckets: dict[str, list[tuple[str, str]]] = {}
    named: set[str] = set()
    for row, link, path in documents:
        if path in skip:
            continue
        # Refused and not skipped: dropping the document would be the listing lying by silence,
        # which is the failure its other two guards exist to prevent, and the operator has a
        # remedy either way. The link is checked too — it is the same name, joined to the
        # configured `specs`/`plans` path, and that path is repository-authored as well.
        if not (_interpolable(row) and _interpolable(link) and utf_8_name(path.name)):
            raise Failure(_UNLISTABLE.format(row=row, roadmap=config.paths.roadmap, marker=MARKER))
        named.add(row)
        buckets.setdefault(theme_of(path.name, trail), []).append((row, link))
    # A rename or deletion must not silently downgrade a state to the default. Without this, a
    # renamed design would reappear as `delivered` — asserting in the roadmap that unimplemented
    # work has shipped, which is the exact failure this listing exists to prevent, and `--check`
    # would stay green because the file is still self-consistent.
    stale = sorted(set(trail.states) - named)
    if stale:
        # Through `quoted`, by way of `clipped`: a `[states]` key is arbitrary TOML from a
        # committed file, line breaks and escape sequences included, and this message is the only
        # place the operator learns which key to fix, so it is escaped, not withheld — and
        # clipped, since nothing bounds one key's length.
        #
        # And capped at `LISTED_LIMIT`, though this message is the only channel: a `Failure` has
        # no `--json` behind it. Uncapped, a map declaring many states named every one of them
        # after a `plans` move, on the one line `docs trail --check` prints in CI. Nothing is
        # lost by the cap: the count is every key, the named ones are the first in sorted order,
        # and the rest are keys in the operator's own `trail.toml`, which a re-run after updating
        # these names in turn.
        rest = "; re-run after updating these to name the rest" if len(stale) > LISTED_LIMIT else ""
        raise Failure(
            f"{TRAIL_FILE} names {len(stale)} document(s) that no longer exist (renamed, deleted, "
            "or now gitignored); update the map before regenerating: "
            f"{listed([clipped(key) for key in stale])}{rest}"
        )
    lines: list[str] = []
    total = pending = 0
    for label in [*(label for label, _ in trail.themes), UNFILED]:
        rows = buckets.get(label)
        if not rows:
            continue
        total += len(rows)
        lines.append(f"\n### {label} ({len(rows)})\n")
        # Sorted on the filename with its directory dropped, so a theme's designs and plans
        # interleave by date instead of splitting into two blocks.
        for row, link in sorted(rows, key=lambda r: r[0].split("/", 1)[1]):
            state = trail.states.get(row, DELIVERED)
            if state != DELIVERED:
                pending += 1
            lines.append(f"- [`{row}`]({link}) — {state}")
    lines.append(
        f"\n<!-- generated by drybag docs trail — {total} documents, "
        f"{pending} not plainly delivered -->"
    )
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def listed_paths(text: str) -> set[str]:
    """Document rows the trail listing in `text` names. Only the generator writes rows in this
    shape, so matching the whole file rather than the block is safe and stays tolerant of a
    block that cannot be located."""
    return set(_ROW.findall(text))


def undeclared_new_documents(before: str, after: str, trail: Trail) -> list[str]:
    """Documents the listing gains that never declared a state, so took the default.

    This is the mirror of the stale-key guard: that one catches a state whose document
    disappeared, this one catches a document that appeared with no state. Both end in the
    roadmap asserting that unimplemented work shipped — and this is the path taken every time
    somebody writes a design, not the rare rename.

    Nothing when `before` names no documents at all: that is a gutted or first-ever listing
    rather than a real addition, and treating the whole corpus as new would bury the signal and
    break regenerating a mangled block back into shape."""
    known = listed_paths(before)
    if not known:
        return []
    return sorted(row for row in listed_paths(after) - known if row not in trail.states)


def rebuild(text: str, root: Path, config: Config, trail: Trail) -> str:
    start = TRAIL_MARKER_LINE.search(text)
    if start is None:
        raise Failure(f"{config.paths.roadmap} is missing the '{MARKER}' heading")
    end = text.find(END_MARKER, start.end())
    if end == -1:
        raise Failure(f"{config.paths.roadmap} is missing the '{END_MARKER}' comment")
    head = text[: start.end()]
    # `tail` opens with the newline that followed the end marker, and `render_listing` already
    # supplies it. Stripping it keeps repeated runs byte-identical instead of growing a blank
    # line each time.
    tail = text[end + len(END_MARKER) :]
    return head + _PREAMBLE + render_listing(root, config, trail) + tail.lstrip("\n")


def trail_gate(root: Path, config: Config) -> list[Finding]:
    """The `trail` gate's whole composition: the roadmap's listing, as `rebuild` would write it.

    `docs trail --check` answers with this function. A missing roadmap is a gate that cannot
    judge the tree, and raises as every such gate does.
    """
    roadmap = contained(root, config.paths.roadmap)
    if not fsops.is_file(roadmap):
        raise Failure(f"{config.paths.roadmap} does not exist")
    current = read_document(roadmap, config.paths.roadmap)
    trail = load_trail(root, config)
    if current == rebuild(current, root, config, trail):
        return []
    return [Finding(TRAIL_STALE, config.paths.roadmap, None, "")]
