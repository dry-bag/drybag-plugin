"""The index is a rendering, not a file anyone edits.

Two machines editing one hand-written index is the most contended file in the store; a
generated one is resolved by regeneration. The curation does not disappear, it moves into each
note's `index:` line, or its `metadata.index`, or its description, read afresh on every render
and written into no note — and because a second writer appends its own lines to the index, the
generator harvests those into their notes before it renders, so nothing a session wrote is lost.

Sections come from `[memory] groups`, one per folder, and a note's `group` is rendered as a
sub-heading *inside* its folder's section. A note's folder is its default group, and read
literally that default would put a `group` value into the section list, which no configuration
declares; nesting the group under its folder is the reading that keeps both rules true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from drybag import fsops
from drybag.config.overlay import overlay_root
from drybag.config.paths import PathEscape, contained
from drybag.config.schema import OVERLAY_MODE, Config
from drybag.errors import Failure, Refusal
from drybag.fsops import TooLarge, read_regular_text, write_atomically
from drybag.memory.notes import (
    UNRANKED,
    Note,
    Provenance,
    is_one_line,
    walk,
    with_index,
    write_note,
)
from drybag.memory.reviewed import UNREVIEWED, UNREVIEWED_LABEL, record, status
from drybag.memory.store import (
    Store,
    in_repository,
    permitted_roots,
    shown,
    stores_holding,
    walked_groups,
)

INDEX_NAME = "MEMORY.md"
EXTRA_TITLE = "Elsewhere"
VOLATILE_SUFFIX = "volatile"
# Every character `str.splitlines()` breaks on, which is the set that matters here: a
# harvested title is written straight into a note's one-line `index:` frontmatter, and
# `notes._split` finds that note's fence with `splitlines()`. Excluding `\n` alone was
# excluding the one character that cannot arrive — `read_text` uses universal newlines — while
# U+2028 (an ordinary artefact of a copy-paste out of a PDF) passed straight through, spilled
# the remainder out of the fence, and quarantined the note out of the index, the standing rules
# and volatile injection, with every run exiting 0 and in overlay mode syncing to every machine.
_BREAKS = r"\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"
# `MEMORY.md`'s premise is that a *second, non-drybag* writer appends entries here, so an
# unclosed `[` is an ordinary accident rather than an attack; neither class spans a line.
_ENTRY = re.compile(rf"^- \[([^\]{_BREAKS}]+)\]\(([^){_BREAKS}]+)\)", re.MULTILINE)

# The marker lines around the region `render_index` writes in `MEMORY.md`: everything between
# them is the render's, and the harvest reads only what stands outside them (`_outside`).
REGION_BEGIN = "<!-- drybag:index -->"
REGION_END = "<!-- drybag:index:end -->"
HEADER = (
    "# Memory Index\n\n"
    "> Routing table. Every entry is a pointer, never the answer: when its trigger fires,\n"
    "> open the note.\n"
)
VOLATILE_LEAD = "Injected in full at session start; these links are for citation and pruning."
# The characters that end a markdown link early. A value carrying one of these is rendered
# into `- [title](target)` on both sides, so it does not merely look wrong: it puts whatever
# follows where `entries_in` reads the next field.
_LINK_SYNTAX = "])("


def entries_in(text: str) -> list[tuple[str, str]]:
    return _ENTRY.findall(text)


def section_title(group: str) -> str:
    """`"project-stable"` → `"Project — stable"`; a nested group keeps every segment.

    A configured group may be more than one path segment (`contained()` admits
    `"team/project-stable"`), and the two halves mean different things: the folders are a
    hierarchy and the hyphens are this function's own convention. So the segments are titled
    one at a time and joined with a separator that is not the one the hyphens produce.
    """
    return " / ".join(_segment_title(part) for part in group.split("/"))


def _segment_title(segment: str) -> str:
    head, *rest = segment.split("-")
    return " — ".join([head.capitalize(), *rest])


def is_volatile(group: str) -> bool:
    """Derived from the configured group name, not from one hardcoded string."""
    return group.endswith(VOLATILE_SUFFIX)


@dataclass(frozen=True)
class Reconciliation:
    notes: list[Note]
    harvested: list[str]
    provisional: list[str]
    unreadable: list[tuple[Path, str]]
    # The `memory.index_extra` entries held back from an index that reaches outside this
    # repository (`_to_machine`): `drybag.toml` is repository data, and the destination is
    # machine state. Named here because a drop `render_index` makes on its own has no other
    # channel back to a person running the command.
    refused_extra: list[str] = field(default_factory=list)
    # The native writer's pointer lines (`_native`), kept verbatim at the head of the render.
    native: list[str] = field(default_factory=list)
    # The note files that are links, which the walk skipped (`notes.walk`).
    skipped: list[Path] = field(default_factory=list)


def _resolved_if_permitted(store: Store, config: Config, target: Path) -> Path | None:
    """Where a symlinked index would resolve, if that location is one this store may use at
    all — independent of whether anything exists there yet.

    `Path.resolve()` is non-strict: it does not require the final component to exist, so a
    *dangling* symlink resolves to an absolute path exactly as a live one does, and can be
    tested against `permitted_roots` all the same. Outside overlay mode there is no overlay to
    validate against, so every symlinked index is refused, exactly like an ungoverned group
    symlink; in overlay mode only a resolution inside *this project's own* share
    (`permitted_roots`) is permitted — never a different project's, and never nothing (no
    overlay recorded at all).

    One rule, two questions. `index_source` additionally asks whether the resolved path
    *exists*, because there is nothing to read from a permitted link with nothing behind it
    yet. `_destination` does not ask that: a permitted-but-dangling link is precisely the file
    `memory index` must be able to create, and answering the permission question without
    existence is what lets it.
    """
    if store.mode != OVERLAY_MODE:
        return None
    overlay = overlay_root(store.machine)
    if overlay is None:
        return None
    allowed = permitted_roots(overlay, config.project.name)
    try:
        resolved = fsops.resolved(target)
    except OSError:
        # A link the repository can commit, and a symlink loop, which `fsops.resolved` answers
        # as `ELOOP` on every interpreter: a link that cannot be followed to a place is no link to
        # a permitted one, so it is refused like any other, never an internal error.
        return None
    if not any(resolved.is_relative_to(root.resolve()) for root in allowed):
        return None
    return resolved


def index_source(store: Store, config: Config) -> Path | None:
    """The index's own per-link target rule — the one `store._group_targets` applies to every
    configured group, applied here because nothing upstream applies it to `MEMORY.md`.

    `store.py` does not track the index as a group (it is not a `memory.groups` entry), so no
    per-link target check has ever reached it. Every reader of `store.path / INDEX_NAME` needs
    this same answer — the writer and the check (`_destination`), and the harvest
    (`_appended`) — so it lives here, beside
    `INDEX_NAME`, and is called rather than reimplemented.

    `None` has three causes, and this function's callers only ever need to ask the read
    question: is there something here to source. An absent target and a *refused* symlink
    (outside overlay mode, or resolving outside this project's share) both answer None, and so
    does a symlink that resolves to a *permitted* location with nothing written there yet —
    reading nothing from a link `attach` created before `memory index` ever ran is correct, not a
    refusal. `_destination` is the one place that must tell the second and third causes apart,
    which is why it does not reuse this return value for the "nothing yet" case; see its own
    docstring.

    A real file sources itself, unconditionally. A permitted symlink that already has a file
    behind it sources that file, resolved.
    """
    target = store.path / INDEX_NAME
    if not fsops.is_symlink(target):
        return target.resolve() if fsops.exists(target) else None
    resolved = _resolved_if_permitted(store, config, target)
    return resolved if resolved is not None and fsops.exists(resolved) else None


def _outside(path: Path | None) -> list[str]:
    """The lines of the index `path` (`index_source`'s, the injection's target rule turned around)
    outside the region drybag renders, where the native writer appends; all of a file with no
    region. The harvest reads only these: read back out of the region, the last render's line for
    an edited note was written into it as the native writer's."""
    if path is None or not fsops.is_file(path):
        return []
    try:
        # To the read cap, as `check_index` reads the same file.
        lines = read_regular_text(path).splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    if REGION_BEGIN in lines and REGION_END in lines[lines.index(REGION_BEGIN) :]:
        start = lines.index(REGION_BEGIN)
        end = lines.index(REGION_END, start)
        return lines[:start] + lines[end + 1 :]
    return lines


def _native(store: Store, outside: list[str]) -> list[str]:
    """The native writer's own pointer lines outside the rendered region: each line that links a
    topic file at the top of the store, outside every group, which the store still holds —
    verbatim, once each, in the order they stand.

    The harness's memory writer keeps its notes as top-level topic files and names each with one
    line in `MEMORY.md`; a store it wrote into holds them beside drybag's groups. They are not
    notes this area walks, so a render made of the groups alone took every one of those lines out
    of the index. A line whose file is gone names nothing and is not kept; a line naming a note is
    harvested into it, and the render carries it from there.
    """
    kept: list[str] = []
    for line in outside:
        found = _ENTRY.match(line)
        if found is None:
            continue
        target = found.group(2)
        if "/" in target or target == INDEX_NAME or not target.endswith(".md"):
            continue
        if fsops.is_file(store.path / target) and line not in kept:
            kept.append(line)
    return kept


def _to_machine(store: Store, config: Config) -> bool:
    """Whether the file `_destination` would write to reaches outside this project's own
    repository, where `memory.index_extra`, repository data, may not be published.

    Deliberately does not call `_destination`: a refused symlink is not a domain question, it
    is a write that cannot happen at all, and the caller that actually attempts one
    (`write_index`, `check_index`) is the one that raises for it. This only has to be right
    when a write can actually land somewhere, so a refused or unrecorded overlay answers False
    (repository) here — inert, because nothing downstream renders once that write is attempted.

    Mirrors `index_source` and `_destination`'s own three-cause shape rather than reusing
    either return value: `index_source`'s None already conflates "refused" with
    "permitted-but-dangling", which is exactly the distinction this needs kept apart from the
    *other* direction — a permitted, dangling link is machine state (`True`) the moment the
    target rule would honour it, not only once something has been written there.
    """
    source = index_source(store, config)
    if source is not None:
        return not in_repository(store, source)
    target = store.path / INDEX_NAME
    if not fsops.is_symlink(target):
        return not in_repository(store, target)
    resolved = _resolved_if_permitted(store, config, target)
    return resolved is not None and not in_repository(store, resolved)


def _relative(note: Note) -> str:
    return f"{note.group_name}/{note.path.name}"


def _metadata_index(note: Note) -> str | None:
    """The short index line the note carries under `metadata:`, when it holds more than
    whitespace, else `None`: the owner's own line, which `reconcile` takes before a harvested one
    and writes as provisional (its docstring says why)."""
    line = note.metadata.get("index", "").strip()
    return line or None


def reconcile(store: Store, config: Config, *, write: bool) -> Reconciliation:
    """Give every note an `index:` line, harvesting the second writer's before rendering one.

    The line, in order: a top-level `index:` (curated, left alone); `metadata.index`
    (provisional, `_metadata_index`); a line harvested from `MEMORY.md` (native); the
    description (provisional). `metadata.index` outranks the harvest because it is the owner's own
    line, where the harvested one is what the native writer appended outside the rendered region
    (`_outside`). A provisional line is the
    render's and is written into no note, so it follows a later edit to either source, and a line
    an earlier release wrote into a note as provisional is read as no line at all. `write` writes
    only a harvested line into its note: the native writer's text has no other place to live once
    the index is rendered again, and an edit to a note makes it unreviewed (`memory.reviewed`).

    `config` rather than a bare group list: the groups came from it at every call site anyway,
    and the index's target rule needs `project.name` and the recorded overlay, which a
    `Sequence[str]` cannot carry. The overlay comes from `store.machine`, so there is no longer
    a second argument a caller can forget to keep in step with the one that built the store.
    """
    source = index_source(store, config)
    outside = _outside(source)
    appended = {target: title for title, target in entries_in("\n".join(outside))}
    to_machine = _to_machine(store, config)
    found = walk(store.path, walked_groups(store, config))
    notes: list[Note] = []
    harvested: list[str] = []
    provisional: list[str] = []
    for note in found.notes:
        if note.index and note.index_provenance is not Provenance.PROVISIONAL:
            notes.append(note)
            continue
        own = _metadata_index(note)
        line = None if own else appended.get(_relative(note))
        if own:
            note = with_index(note, own, Provenance.PROVISIONAL)
            provisional.append(note.name)
        elif line:
            note = with_index(note, line, Provenance.NATIVE)
            harvested.append(note.name)
            if write:
                write_note(note)
        else:
            note = with_index(note, note.description, Provenance.PROVISIONAL)
            provisional.append(note.name)
        notes.append(note)
    refused_extra: list[str] = []
    if to_machine:
        # Always repository data — `drybag.toml` is a committed file — so every survivor of
        # `_extra`'s own containment checks is still a contributing entry this destination may
        # not carry.
        refused_extra = _extra(config, store)
    return Reconciliation(
        notes,
        harvested,
        provisional,
        found.unreadable,
        refused_extra,
        _native(store, outside),
        found.skipped,
    )


def _order(note: Note) -> tuple[int, int, str]:
    rank = note.startup
    return (
        UNRANKED if rank is None else rank,
        UNRANKED if note.group_order is None else note.group_order,
        note.name,
    )


def _entry(note: Note, unreviewed: set[Path]) -> str:
    """The note's line, labelled where the owner has not promoted it at its bytes, as the bundles
    label it: the harness reads `MEMORY.md` natively, beside them."""
    label = UNREVIEWED_LABEL if note.path in unreviewed else ""
    return f"- [{note.index}]({_relative(note)}){label}"


def _section(group: str, notes: list[Note], unreviewed: set[Path]) -> list[str]:
    lines = [f"## {section_title(group)}", ""]
    if is_volatile(group):
        lines += [VOLATILE_LEAD, ""]
    ungrouped = sorted((n for n in notes if not n.group), key=_order)
    lines += [_entry(note, unreviewed) for note in ungrouped]
    if ungrouped:
        lines.append("")
    # Grouped notes are collected by their `group` sub-heading first, then each heading is
    # emitted once. Interleaving `_order` across two sub-headings (Alpha, Beta, Alpha, ...)
    # must not split one heading's members apart or reopen it — every member of a heading has
    # to be gathered before that heading is ever written out.
    by_heading: dict[str, list[Note]] = {}
    for note in notes:
        if note.group:
            by_heading.setdefault(note.group, []).append(note)
    for heading in sorted(by_heading, key=lambda h: min(_order(n) for n in by_heading[h])):
        lines += [f"### {heading}", ""]
        lines += [_entry(note, unreviewed) for note in sorted(by_heading[heading], key=_order)]
    if by_heading:
        lines.append("")
    return lines


def _extra(config: Config, store: Store) -> list[str]:
    """`memory.index_extra` entries that actually stay inside the project root.

    `config/paths.py` names this field, alongside `memory.groups`, as one its own guard does
    not cover, and assigns the check to "the module that first reads those fields". The
    strings are repository-controlled and land verbatim in `MEMORY.md`, the file the harness
    memory link exposes — the same channel a symlinked index reaches. `contained` is called without
    `allow_final_symlink`, unlike `_group_targets`: a group legitimately *is* a symlink in
    overlay mode, while these are pointers to documents in the repository and a link at the
    last component escaping the root is the same escape as one halfway up.

    An entry that escapes is dropped, the way `_group_targets` drops a group whose target it
    refuses. Dropped silently, because this function returns a plain list and has no report
    channel of its own. (A *survivor* of
    these checks can still be dropped later, loudly, by `render_index`'s own domain gate —
    `reconcile` names those in `Reconciliation.refused_extra` — but that is a different
    question from the one this function answers, and this function does not need to know it is
    asked.)

    **What is kept is the path `contained` returned, not the string that was checked.** The
    value was validated as a path and then consumed as text: `contained` answers about
    absoluteness, `..` and symlinks and says nothing about a value being one line, so a TOML
    multi-line string passed and was written verbatim into `MEMORY.md` — twice, as a link's
    title and as its target — carrying repository-authored prose through every index
    regeneration. Two guards close that. The value must be a single line, and the question is
    put to `notes.is_one_line` rather than answered a second time here: this string is rendered
    into the same `- [title](target)` shape the harvest reads back out into a note's one-line
    `index:`, so the two ends of that round trip must not disagree about what one line is —
    `len(value.splitlines()) > 1`, which this was, answers False for a value that merely *ends*
    in a break. And it may not hold `]`, `(` or `)`, the three characters that close a markdown
    link early and put the remainder where `entries_in` reads a *target*.
    """
    kept: list[str] = []
    for target in config.memory.index_extra:
        if not is_one_line(target) or any(char in target for char in _LINK_SYNTAX):
            continue
        try:
            resolved = contained(store.root, target)
        except PathEscape:
            continue
        kept.append(str(resolved.relative_to(store.root)))
    return kept


def render_index(reconciled: Reconciliation, config: Config, store: Store) -> str:
    """The rendered `MEMORY.md` text: the render between `REGION_BEGIN` and `REGION_END`, then the
    native writer's pointer lines, where it appends; `memory.index_extra` held back where the
    destination reaches outside this repository (`_to_machine`). An unreviewed note is labelled
    only in a composed store's own index, since the label follows this machine's record."""
    to_machine = _to_machine(store, config)
    lines = [REGION_BEGIN, HEADER.rstrip("\n"), ""]
    by_group: dict[str, list[Note]] = {}
    for note in reconciled.notes:
        by_group.setdefault(note.group_name, []).append(note)
    unreviewed: set[Path] = set()
    if stores_holding(_destination(store, config)) is not None:
        held = record(store.machine)
        unreviewed = {
            n.path for n in reconciled.notes if status(store.machine, n, held) == UNREVIEWED
        }
    for group in walked_groups(store, config):
        if by_group.get(group):
            lines += _section(group, by_group[group], unreviewed)
    extra = [] if to_machine else _extra(config, store)
    if extra:
        lines += [f"## {EXTRA_TITLE}", ""]
        lines += [f"- [{target}]({target})" for target in extra]
        lines.append("")
    text = "\n".join(lines).rstrip("\n") + f"\n{REGION_END}\n"
    native = "".join(f"{line}\n" for line in reconciled.native)
    return text + (f"\n{native}" if native else "")


@dataclass(frozen=True)
class IndexCheck:
    drifted: bool
    words: int
    lines: int
    bytes_: int
    over_budget: bool
    over_caps: list[str]
    provisional: list[str]
    unreadable: list[str]  # store-relative


def _destination(store: Store, config: Config) -> Path:
    """The one file the writer writes and the check compares against: what the readers source.

    `index_source` is the rule every *reader* applies — `_appended`. The writer
    did not apply it and neither did the check, and each half of that was its own defect.
    `write_atomically` ends in `os.replace`, which replaces the **link** rather than its target:
    one run stranded the overlay's shared copy on every other machine, turned the index into a
    real file inside the repository, and so flipped `in_repository` to True and closed the trust
    gate on the index for good. `check_index` meanwhile read `store.path / INDEX_NAME` through
    `is_file()`, which follows the link, so `--check` answered about a file no reader sources:
    exit 0 and "index is current" while every reader refused the link it had followed.

    Both are the same question, so both ask it here, once.

    `None` from `index_source` has **three** causes, not two, and only one of them is a
    `Refusal`. An index that is simply absent (no file, no symlink) is the ordinary first run:
    the destination is the real path, and the check reports drift against nothing. An index
    that **is** a symlink comes apart into the other two: *refused* — outside overlay mode, or
    resolving outside this project's share of the overlay — where writing would clobber exactly
    the link the target rule declined to honour, and *permitted but not yet created* — `attach`
    creates the symlink before `memory index` ever renders a file behind it, so the very first
    run in an overlay project always finds this shape. `index_source` answers None for the
    second and third causes alike, because both are "nothing to read" — but they are not the
    same answer *here*: refused is a boundary this store may never write across, and
    permitted-but-dangling is precisely the file this command exists to create. Collapsing them
    made overlay mode unable to bootstrap at all.

    So this does not read `index_source`'s None as one thing. When it is None because the
    target is not a symlink at all, the ordinary-first-run answer applies unconditionally. When
    it is None because the target *is* a symlink, `_resolved_if_permitted` is asked the same
    permission question `index_source` asked — but without requiring the far end to already
    exist, which is the one difference the write side needs from the read side. A permitted
    answer is the write destination, dangling or not; anything else is the refusal below.
    """
    source = index_source(store, config)
    if source is not None:
        return source
    target = store.path / INDEX_NAME
    if not fsops.is_symlink(target):
        return target
    resolved = _resolved_if_permitted(store, config, target)
    if resolved is None:
        raise Refusal(
            f"{shown(target)} is a symlink this store may not source ({INDEX_NAME} may link only "
            f"into this project's share of the recorded overlay, in overlay mode); refusing "
            f"to read or replace it"
        )
    return resolved


def check_index(store: Store, config: Config, reconciled: Reconciliation) -> IndexCheck:
    """The drift report, against the file `index_source` says the index actually is.

    Reading `store.path / INDEX_NAME` instead — `is_file()` follows the link — made this answer
    about a file nothing injects: a green CI run over an empty bundle. `store.machine` is what
    lets it ask, which is why the value lives on the store rather than in a keyword this could
    be called without."""
    text = render_index(reconciled, config, store)
    path = _destination(store, config)
    try:
        # To the read cap, as every reader of a file a clone can commit reads it.
        current = read_regular_text(path) if fsops.is_file(path) else None
    except (UnicodeDecodeError, TooLarge):
        # Not what the render writes, whatever else it holds: drifted, and `drybag memory
        # index` replaces it.
        current = None
    caps = []
    if len(text.splitlines()) > config.native_caps.memory_index_lines:
        caps.append("memory_index_lines")
    if len(text.encode("utf-8")) > config.native_caps.memory_index_bytes:
        caps.append("memory_index_bytes")
    return IndexCheck(
        drifted=current != text,
        words=len(text.split()),
        lines=len(text.splitlines()),
        bytes_=len(text.encode("utf-8")),
        over_budget=len(text.split()) > config.budgets.effective("memory_index_words"),
        over_caps=caps,
        provisional=list(reconciled.provisional),
        # Store-relative, as the summary line names it: one file has one name
        # in one answer, and the absolute prefix is this machine's, not the store's.
        unreadable=[path.relative_to(store.path).as_posix() for path, _ in reconciled.unreadable],
    )


def write_index(store: Store, config: Config, text: str) -> Path:
    """Write the index to the file the readers source, and return that file.

    Takes `config` — a change to a signature `memory/api.py` published at the time — because
    `_destination` cannot answer without it, and answering without it was the defect; the
    overlay half comes from `store.machine`. The returned path is the file actually written,
    which in overlay mode is the shared copy in the overlay rather than the link inside the
    checkout.
    """
    if not fsops.is_dir(store.path):
        raise Failure(f"{shown(store.path)} does not exist; the store was not created")
    path = _destination(store, config)
    write_atomically(path, text)
    return path


def render_current(store: Store, config: Config) -> None:
    """`drybag memory index`'s write, in process: the appended lines harvested into their notes,
    the index rendered and written, and once more when it has drifted already (a line appended
    while it rendered)."""
    for _ in range(2):
        reconciled = reconcile(store, config, write=True)
        write_index(store, config, render_index(reconciled, config, store))
        if not check_index(store, config, reconcile(store, config, write=False)).drifted:
            break
