"""Lint implementation plans: references, verification steps, `Scope:`, and `Premise:`.

The first two rules come from one branch's retro. A plan contradicted itself about a test
location and the stale pointer survived into three later tasks — so every reference must
resolve unless the line says `(create)`. And a step phrased "confirm nothing bounds X" embeds
its own expected answer: the author already doubted it enough to write the check and hid that
doubt from whoever executes it. That one shipped an open defect. The verb is not special to
"confirm" — "verify" and "check" hide the same doubt the same way — so all three are covered,
guarded against the compound-word false positive a bare `no` produces (`no-op` is not the
negation `no`).

A path the plan removes is the first rule's claim run backwards: it resolves until its task lands
and never after, and a finished plan is history that a later change may still edit. So a
`Delete:` line declares its paths as `Create:` does, and `(delete)` marks its line as `(create)`
does.

The `Scope:` and `Premise:` rules come from the same retro: an unrelated change rode a bugfix
plan because nothing on the plan named what belonged to it, and a fix's own discriminator
outran the entry evidence it was supposedly fixing because nothing forced that evidence onto
the page.

The mutation-outcome rule comes from the next branch's, where one plan wrote three
discrimination proofs as settled results and all three were wrong. The first reddened every
test in its module instead of the one named, at import, because the removal it described broke
a key-parity invariant. The second could not have gone red at all: the function it mutated
swallows everything in its own `try`/`catch`. And the third predicted a fail-closed test would
stay green under a mutation that test's own call-count assertion cannot survive. Written as
predictions they would have been checked; written as facts they were read as evidence the
assertions discriminated, and the branch shipped believing three oracles nobody had watched
fail.

Prediction and report are told apart by TENSE, not by a keyword either has to carry. A plan is
written before the work and a report after it, so "the test reddens" is a forecast while "the
test reddened" is an observation, and English marks that difference in the verb itself. So the
rule's vocabulary is present tense and nothing else: a plan reporting a mutation it already ran
is not exempted, it is never matched. `Expected:` is the one marker the rule does read, because
it is how an author says a present-tense sentence is a forecast on purpose. The vocabulary is
the colour idiom, present tense, and deliberately not `fails`/`passes`, which every plan uses
for the TDD red step; measured on the corpus this lint was written against, admitting them
multiplied the findings by thirty and almost all of the rise was correct prose. Bounding the
outcome to within `_OUTCOME_SPAN` characters of the instruction that governs it is the other
half of that narrowing — it separates a consequence from an unrelated later clause of a long
paragraph.

The claim is a SENTENCE, so the rule reads paragraphs and not lines. Scanning line by line made
catching a false discrimination proof depend on where the text happened to wrap: an arrow
ending one line with `redden` opening the next went unflagged while the identical claim two
lines below, short enough to fit, was flagged. Blocks are joined and each character keeps its
source line, so a report still names the line the claim starts on. The `Expected:` marker is
read over the claim's own SENTENCE, which is the unit that marker marks: a marker in a
neighbouring sentence of the same paragraph exempts nothing, and one written after the claim is
not read at all.

These are floors. A regular expression cannot judge whether a computed number's model covers
its subject; that rule lives in the plan template and the review lens.

Scope is the diff, not the corpus: plans are immutable history, so a finished plan pointing at a
deleted module is a correct record, not a defect. Two things depart from that rule. A `root`
that is not a git repository at all — the normal state only for a lint-fixture tree in a test,
never for a real checkout — lints every plan under `paths.plans`, so the fixtures that pin these
rules actually discriminate instead of passing vacuously through an unresolvable diff. And
named `PATH` arguments lint exactly those plans, because a developer editing a plan is looking
at the file on disk, not at a commit that does not exist yet; the plans the working tree holds
beyond the diff are reported as `unlinted` rather than linted silently, so the remedy is to name
the file.

The same holds within a plan. A delivered plan's `Interfaces:` blocks are kept current, so a later
change edits plans whose other lines name paths the tree has since moved or removed, and holding
it to those made the only way through a rewrite of history. In the diff-scoped mode a reference
is therefore settled only on a line the change wrote (`written_lines`): a new plan is all such
lines, and an edit is held to its own. Every other rule still reads the whole plan, and naming a
plan as `PATH` still settles every reference in it.

A repository whose BASE will not resolve is a finding and not an OK. Printing a note and exiting
0 is how a gate like this one runs green for its whole life without ever having linted anything:
a CI checkout made at the default depth of 1 holds no base ref, so the diff cannot be taken and
no plan could have failed whatever it contained. A check that cannot answer must say so in its
exit code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from drybag import fsops
from drybag.config.layout import local_base
from drybag.config.paths import contained
from drybag.docs.hygiene import read_document
from drybag.errors import Failure, Refusal
from drybag.findings import Finding
from drybag.gitenv import NO_ANSWER, ForkUnknown, fork_points, git_run
from drybag.prose import blank_fences, path_references, present_within, resolves_within

if TYPE_CHECKING:
    from drybag.config.schema import Config

# A plan's claim to fix a bug, which owes a `Premise:` line. The prefix is fixed at `BR`, and three
# digits or more because fewer is a different shape: a heading or a number never an identifier.
BUG_FIXES = re.compile(r"\bFixes\s+BR-\d{3,}\b")
# A line declaring paths: a dash, after blanks on its own line. Led by `^\s*`, the pattern reached
# the same dash from every line start in the blank lines above it, reading them again from each:
# 0.33 s over 16,000 of them, and four times as long at each doubling. A match from a line above
# ends where the one from the dash's own line does, so the paths declared are the same.
_DECLARES = re.compile(r"^[^\S\n]*-\s*(?:Create|Test|Delete):\s*(.+)$", re.MULTILINE)
_LINE_MARK = re.compile(r"\((?:create|delete)\)", re.IGNORECASE)
# "confirm", "verify", and "check" all phrase a step as already knowing the answer it claims to
# test. `no(?!-)` keeps a compound word like "no-op" out of the negation: a hyphen is a word
# boundary, so plain `\bno\b` matches inside it and flags correct prose.
_LEADING = re.compile(
    r"\b(?:confirm|verify|check)\s+(?:that\s+)?(?:nothing|no(?!-)|none|it does not)\b",
    re.IGNORECASE,
)
# `[^\S\n]*\S` rather than `\s*\S`: the latter's `\s` crosses newlines under MULTILINE, so an
# empty `**Scope:**`/`**Premise:**` marker would be satisfied by any non-whitespace character
# anywhere later in the document — exactly the unfilled-marker case these rules exist to catch.
# Anchoring to non-newline whitespace forces the content onto the marker's own line.
_SCOPE_LINE = re.compile(r"^\*\*Scope:\*\*[^\S\n]*\S", re.MULTILINE)
#
# A premise marker, read to the first colon after it, past line ends; the group holds when the
# marker closes there and has content on its line. Every marker before that colon reaches the same
# one, so `_has_premise` reads each stretch once with `finditer`, where a search from each marker
# read the stretch again: 0.51 s over 16,000 markers with no colon after them.
_PREMISE_LINE = re.compile(r"^\*\*Premise[^:]*+(:\*\*[^\S\n]*\S)?", re.MULTILINE)
# The colour idiom, present tense. `fails`/`passes` are deliberately absent: see the module
# docstring for the measurement that removed them.
_OUTCOME = r"(?:reddens?|go(?:es)?\s+red|turns?\s+red|stays?\s+green|remains?\s+green)"
# The instructions that govern an outcome: the arrow of "mutation -> result", the `watch` of
# "watch it go red", and the `verify`/`confirm` of "verify it goes red under that mutation".
# Present tense only, so "verified"/"confirmed" — a report — is not among them.
_GOVERNOR = r"(?:->|→|\bwatch(?:ing)?\b|\bverif(?:y|ies)\b|\bconfirms?\b)"
# How far an outcome may sit from the instruction that governs it and still be read as its
# consequence: a named cap (CONTRIBUTING.md#named-caps) on a regular expression's reach, and no
# shipped file changes with it. Wide enough for a clause, narrow enough that a data-flow arrow early
# in a long paragraph cannot reach a "must stay green" constraint at its end.
_OUTCOME_SPAN = 60
# Governed and bounded; or ungoverned but counted, because "reddens 8 assertions" is a
# measurement, and a measurement written in the present tense has not been taken yet.
_ASSERTED_OUTCOME = re.compile(
    rf"{_GOVERNOR}[^\n]{{0,{_OUTCOME_SPAN}}}?\b{_OUTCOME}\b|\b{_OUTCOME}\s+\**\d", re.IGNORECASE
)
# The author saying this is a forecast. A REPORT needs no marker and never gets here: the
# vocabulary above is present tense, so "reddened", "went red" and "stayed green" are not
# outcomes this rule can see.
_MARKED_AS_EXPECTATION = re.compile(r"\bexpect(?:s|ed|ation|ations)?\b", re.IGNORECASE)
# A sentence boundary: terminal punctuation, any closing bracket or quote, then whitespace.
# The dot inside `src/widget/thing.py` is followed by a letter, so a path is not a boundary.
_SENTENCE_END = re.compile(r"[.!?][)\]\"'`]*\s")
# A unified-diff hunk header's new-side range, `+start[,count]`: the lines of the file as it is now
# that the hunk holds. A missing count is one line, and a count of 0 a hunk that only removed.
_HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)

_BASE_UNRESOLVABLE = (
    "which plans HEAD changes against `{base}` is unknown under {root} ({cause}), so NOTHING "
    "was linted and this gate proved nothing. Fetch the whole history (`fetch-depth: 0` in CI), "
    "or pass a `--base` this clone holds that shares history with HEAD."
)
_NO_ANSWER = (
    "{no_answer}, so which plans HEAD changes against `{base}` under {root} could not be listed "
    "and NOTHING was linted"
)
_SCOPE_MISSING = (
    'no `**Scope:**` admission criterion — one line saying "a change belongs to this branch '
    'iff …", so a mid-plan arrival is screened'
)
_PREMISE_MISSING = (
    "claims `Fixes` with no `**Premise:**` line quoting the entry evidence that establishes "
    "its central predicate"
)
_LEADING_STEP = (
    'verification step embeds its expected answer; write "determine whether …; if '
    '<unexpected>, stop and file"'
)
_ASSERTED = (
    "a mutation's outcome is stated as fact, and an unrun mutation has no outcome; mark it an "
    'expectation — "Expected: …; if it reddens for another reason the assertion needs '
    'redesigning" — or report what it actually did, in the past tense'
)


class BaseUnresolvable(Failure):
    """git cannot find the commits HEAD forked from the base at, so nothing was linted: an
    answer about the checkout, not about a plan.

    Raised, never returned as a finding, so a gate run reads it as a gate that could not judge,
    as it reads every other gate's. `plan check` alone turns it into its `base-unresolvable`
    finding, which is that command's own output.
    """


@dataclass(frozen=True)
class Lint:
    findings: list[Finding]
    linted: list[Path]
    unlinted: list[Path]


def _is_git_repo(root: Path) -> bool:
    """Whether ``root`` sits inside any git work tree, not just whether ``root/.git`` exists.

    `rev-parse --is-inside-work-tree` walks up through parent directories exactly like git
    itself does, so this matches git's own notion of "inside a repository" rather than requiring
    ``root`` to be a repository's top level.
    """
    return git_run(root, "rev-parse", "--is-inside-work-tree")[0] == 0


def _unresolved(unknown: ForkUnknown, base: str, root: Path) -> Failure:
    """What a question about the change's plans git did not answer means to this lint: a
    `Failure` when git gave no answer at all, and `BaseUnresolvable` otherwise."""
    if not unknown.answered:
        # Not "the base does not resolve": that finding's remedy is a deeper checkout, and a
        # git that could not be run or ran past its bound is a clone that may hold every ref.
        return Failure(_NO_ANSWER.format(no_answer=NO_ANSWER, base=base, root=root))
    return BaseUnresolvable(_BASE_UNRESOLVABLE.format(base=base, root=root, cause=unknown.cause))


def touched_plans(root: Path, base: str, plans_dir: Path) -> list[Path]:
    """Plans this change touches: those merging it could alter on `base`. `BaseUnresolvable`
    when that cannot be known, and a `Failure` when git gave no answer at all.

    A plan is one of them when HEAD's copy differs from `base`'s and from that of any commit
    `gitenv.fork_points` names. If it equals every fork point's, git's merge takes the base's
    copy; if it equals the base's, the merge leaves it as it is; either way merging alters
    nothing. Every fork point, not the one `<base>...HEAD` diffs against: that one can already
    hold an old plan the change puts back. And the base too, because against a fork point alone
    a plan the base changed since it reads as the change's own.

    Read with `-z`, the same way and for the same reason as `unlinted_plans`: without it git
    C-quotes any path holding a space or a non-ASCII byte, splitting on whitespace then tears
    `2026-01-03-a draft.md` into two fragments and neither ends in `.md`. The plan is committed,
    so `unlinted_plans` does not list it either — it is neither linted nor reported, and
    vanishes from the gate in silence. `-z` NUL-terminates each record instead and never quotes;
    the trailing empty field falls out with everything that does not end in `.md`.

    The base is refused when it is shaped like an option (principle 5), in the one spelling
    `guards.commit.commits_in` and `guards.commands.run_commit_strip` already use. It reaches a
    git argv slot ahead of any `--`, where git reads it as its own option: a `--base` of
    `--output=<path>` makes `git diff` write the diff to a file at a caller-chosen absolute
    path, outside `contained()` and outside `fsops`, and exit 0 with empty stdout — an empty
    list, and a gate reporting OK having linted nothing. That is the state `BaseUnresolvable`
    exists to make impossible — raised, it is a `plan` gate that could not run and `plan
    check`'s `base-unresolvable` finding — reached by an option-shaped typo instead of by a
    shallow checkout. The default,
    `config.layout.local_base`, is safe for its `refs/remotes/origin/` prefix alone, which is a
    property of that one caller and not of this argument.
    """
    if base.startswith("-"):
        raise Refusal(f"{base!r} looks like an option, not a base ref")
    relative = plans_dir.relative_to(root).as_posix()

    def changed(since: str) -> set[str]:
        out = _diff(root, base, "--name-only", "-z", since, "HEAD", "--", relative)
        return {name for name in out.split("\0") if name.endswith(".md")}

    found: set[str] = set()
    for fork in _forks(root, base):
        found |= changed(fork)
    found &= changed(base)
    return [root / name for name in sorted(found)]


def _forks(root: Path, base: str) -> tuple[str, ...]:
    """`gitenv.fork_points`, or the failure that says why they are not known. One spelling for
    `touched_plans` and `written_lines`, which ask the same question of the same base."""
    forks = fork_points(root, base)
    if isinstance(forks, ForkUnknown):
        raise _unresolved(forks, base, root)
    return tuple(forks)


def _diff(root: Path, base: str, *args: str) -> str:
    """What `git diff args` printed, or the failure that says why git gave no diff. A diff git
    did not answer is never an empty one: read as one, it is a change that touched nothing."""
    code, out = git_run(root, "diff", *args)
    if code != 0:
        raise _unresolved(ForkUnknown.of(code), base, root)
    return out


def unlinted_plans(root: Path, plans_dir: Path) -> list[Path] | None:
    """Plans in the working tree the committed diff does not carry; None when `git status`
    itself cannot be read.

    `touched_plans` reads a committed diff: a staged, modified or untracked plan is invisible to
    it, and an OK over that set says nothing about the file the developer is looking at.
    `--porcelain` names those files; `--untracked-files=all` lists an untracked plan rather than
    the directory that holds it.

    Read with `-z`: without it, `git status --porcelain` C-quotes any path holding a space or a
    non-ASCII byte, and the quoted string ends in a stray `"` rather than `.md` — the file is
    then dropped from the report silently. `-z` NUL-terminates each record instead and never
    quotes. A rename or copy status carries its "from" path as its own following NUL-terminated
    token with no `XY ` prefix; that token is consumed here and never read as a file in its own
    right.

    A status containing `D` names a path the working tree no longer holds — naming it as a
    `PATH` argument would still lint nothing there, so it is excluded up front rather than
    surfaced with a remedy that lints zero files.

    Returns None rather than an empty list when git itself cannot answer: an empty list here is
    indistinguishable from "nothing is uncommitted", and a caller that cannot tell those apart
    would report a clean run over a git failure it never saw.
    """
    relative = plans_dir.relative_to(root).as_posix()
    code, out = git_run(
        root, "status", "--porcelain", "--untracked-files=all", "-z", "--", relative
    )
    if code != 0:
        return None
    found: list[Path] = []
    entries = iter(out.split("\0"))
    for entry in entries:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            next(entries, None)  # the "from" path: its own token, carrying no `XY ` prefix
        if "D" in status:
            continue  # deleted in the working tree: nothing left there to lint
        if path.endswith(".md"):
            found.append(root / path)
    return found


def written_lines(root: Path, base: str, plan: Path) -> set[int]:
    """The lines of `plan`, as the working tree holds it, that this change wrote.

    A line the change wrote is one that differs from every fork point's copy and from the
    base's, for the reason `touched_plans` reads a plan as touched only against all of them: a
    line the base changed since the fork reads as added against the base alone, and one an old
    fork point lacks reads as added against that fork point alone. Diffed against the working
    tree, the file `lint` reads, so the numbers are the ones its findings carry. `--no-textconv`
    and `--no-ext-diff`, so the numbers are of the file's own lines and not of a filter's output.
    `base` has been through `touched_plans`, which refuses one shaped like an option.
    """
    relative = plan.relative_to(root).as_posix()
    written: set[int] | None = None
    for since in (*_forks(root, base), base):
        out = _diff(
            root, base, "-U0", "--no-color", "--no-ext-diff", "--no-textconv", since, "--", relative
        )
        lines: set[int] = set()
        for hunk in _HUNK.finditer(out):
            start = int(hunk.group(1))
            lines.update(range(start, start + int(hunk.group(2) or 1)))
        written = lines if written is None else written & lines
    return written or set()


def logical_blocks(prose: str) -> list[tuple[str, list[int]]]:
    """Blank-line blocks joined into one string each, with every character's source line.

    A claim is a sentence, not a line. Joining is what lets the rule read one, and the
    per-character line map is what keeps a report pointing at the line the claim opens on rather
    than at the top of its paragraph.
    """
    blocks: list[tuple[str, list[int]]] = []
    buffer: list[str] = []
    line_of: list[int] = []
    for number, line in enumerate(prose.splitlines(), 1):
        if not line.strip():
            if buffer:
                blocks.append(("".join(buffer), list(line_of)))
            buffer, line_of = [], []
            continue
        if buffer:
            buffer.append(" ")
            line_of.append(number)
        buffer.append(line)
        line_of.extend([number] * len(line))
    if buffer:
        blocks.append(("".join(buffer), list(line_of)))
    return blocks


def asserted_outcomes(prose: str) -> list[int]:
    """Lines opening a claim that states a mutation's outcome as fact."""
    found: set[int] = set()
    for text, line_of in logical_blocks(prose):
        # The marker is read over the claim's own SENTENCE, up to where the claim ends, and never
        # over the lines the match happens to span. A match starts at its GOVERNOR, so a marker
        # written before the governor fell outside that span whenever the text wrapped between
        # the two — the same wrap-dependence the paragraph reading removed from detection,
        # relocated into the exemption. A sentence is the unit `Expected:` marks: a marker in a
        # neighbouring sentence still exempts nothing, and one written after the claim is not
        # read at all.
        #
        # Claims come in order, so the sentence ends and the markers are read forward once each
        # beside them. Read again from the block's start at every claim, a paragraph of 32,000
        # claims took 52 s, four times as long at each doubling. A marker is a word, so one lies
        # within a claim's sentence exactly when the first that starts in it ends by the claim's
        # end.
        ends = _SENTENCE_END.finditer(text)
        markers = _MARKED_AS_EXPECTATION.finditer(text)
        end, marker, opening = next(ends, None), next(markers, None), 0
        for match in _ASSERTED_OUTCOME.finditer(text):
            while end is not None and end.end() <= match.start():
                opening, end = end.end(), next(ends, None)
            while marker is not None and marker.start() < opening:
                marker = next(markers, None)
            if marker is not None and marker.end() <= match.end():
                continue
            found.add(line_of[match.start()])
    return sorted(found)


def _has_premise(prose: str) -> bool:
    """Whether a premise marker in `prose` closes with content on its line (`_PREMISE_LINE`)."""
    return any(marker.group(1) for marker in _PREMISE_LINE.finditer(prose))


def _lint_one(
    path: Path, where: str, root: Path, *, judged: set[int] | None = None
) -> list[Finding]:
    """Every finding one plan carries, with the line numbers of the file as it is on disk.

    `judged`, when given, is the lines whose references are settled against the tree: the ones
    the change wrote (`written_lines`). Every other rule reads the whole plan either way.
    """
    # Fenced code is not plan prose: its fixtures name deliberately fake paths.
    prose = blank_fences(read_document(path, where))
    found: list[Finding] = []
    # Per plan, not per line: these are properties of the document, not of one line in it.
    if not _SCOPE_LINE.search(prose):
        found.append(Finding("scope-missing", where, None, _SCOPE_MISSING))
    if BUG_FIXES.search(prose) and not _has_premise(prose):
        found.append(Finding("premise-missing", where, None, _PREMISE_MISSING))
    declared: set[str] = set()
    for line in _DECLARES.findall(prose):
        declared |= set(path_references(line))
    for number, line in enumerate(prose.splitlines(), 1):
        if not _LINE_MARK.search(line) and (judged is None or number in judged):
            for target in path_references(line):
                # A claim that lands outside the root is not asked of the filesystem: the
                # answer would be about this disk rather than about the repository, and a plan
                # naming an absolute path that exists locally would pass here and fail in CI.
                landed = resolves_within(root, target)
                if landed is None or target in declared:
                    continue
                if present_within(root, landed) is not False:
                    continue
                found.append(Finding("dead-reference", where, number, target))
        if _LEADING.search(line):
            found.append(Finding("leading-step", where, number, _LEADING_STEP))
    found.extend(
        Finding("asserted-outcome", where, number, _ASSERTED) for number in asserted_outcomes(prose)
    )
    return found


def lint(root: Path, config: Config, *, plans: list[Path], base: str | None = None) -> Lint:
    plans_dir = contained(root, config.paths.plans)
    base = base or local_base(config)
    unlinted: list[Path] = []
    diff_scoped = False
    if plans:
        missing = [p for p in plans if not fsops.is_file(p)]
        if missing:
            raise Failure("not a plan file: " + ", ".join(str(p) for p in missing))
        # Every finding carries a repo-relative path, so a named plan outside the root is
        # refused here rather than reaching `relative_to` below, where it is a `ValueError` the
        # frame reports as an internal error (2) instead of as the findings-shaped failure the
        # missing-file case two lines up already produces.
        outside = [p for p in plans if not p.is_relative_to(root)]
        if outside:
            raise Failure(
                "not inside the project root, so not this project's plan: "
                + ", ".join(str(p) for p in outside)
            )
        selected = list(plans)
    elif _is_git_repo(root):
        diff_scoped = True
        touched = touched_plans(root, base, plans_dir)
        pending = unlinted_plans(root, plans_dir)
        if pending is None:
            raise Failure(
                f"git status could not be read under {root}, so uncommitted plans could not "
                "be found"
            )
        selected = [p for p in touched if fsops.is_file(p)]
        unlinted = [p for p in pending if p not in set(touched)]
    else:
        selected = sorted(plans_dir.glob("*.md")) if fsops.is_dir(plans_dir) else []
    findings: list[Finding] = []
    for path in sorted(selected):
        judged = written_lines(root, base, path) if diff_scoped else None
        where = path.relative_to(root).as_posix()
        findings.extend(_lint_one(path, where, root, judged=judged))
    return Lint(findings, sorted(selected), sorted(unlinted))
