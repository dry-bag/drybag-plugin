"""Small always-loaded documents and existing link targets; each bound is a `[budgets]` key."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from drybag import fsops
from drybag.config.paths import contained
from drybag.errors import Failure
from drybag.findings import Finding
from drybag.fsops import read_regular_text, said
from drybag.printed import quoted
from drybag.prose import blank_fences, present_within, resolves_within

if TYPE_CHECKING:
    from drybag.config.schema import Config

TRAIL_MARKER = "## Design and plan trail"
STATUS_HEADING = "## Current status"
# Anchored to a whole line, and public because `docs.trail` rebuilds the block this marker
# opens and must anchor it the same way: a plain substring search also matches a deeper
# heading that contains the marker (`### Design and plan trail`) or a sentence quoting it.
# Cutting the roadmap there would exempt every hand-written line below from the budget, and
# rebuilding from there would swallow every line between that subheading and the end marker.
# One definition, so the two readers cannot disagree about where the listing starts.
TRAIL_MARKER_LINE = re.compile(rf"^{re.escape(TRAIL_MARKER)}$", re.MULTILINE)
# Neither half may cross the bracket or parenthesis that opens another link. With `[^\]]+` and
# `[^)]+` a text of repeated `[a](` made every opening bracket scan to the end of the text:
# 20,000 characters took 0.55 s, and each doubling four times as long, over any document a
# change can commit. The one link the old pattern read and this one does not is a target holding
# a parenthesis, which it cut at the first `)` and so never named a real file.
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\[\]]+\]\(([^()]+)\)")
_IGNORED_LINK_PREFIXES = ("#", "/", "http://", "https://", "mailto:")


def read_document(path: Path, where: str | Path) -> str:
    """One repository document's text, or a `Failure` naming it.

    `cli.run` maps a `Failure` to exit 1 and everything else to exit 2, and 2 is reserved for a
    refusal or an internal error. One latin-1 byte in `AGENTS.md`, in the roadmap, in a
    plan or in `trail.toml` reached the frame as `internal error: UnicodeDecodeError` and exit
    2 — telling the operator this tool is broken rather than that their file is, with nothing in
    the message to act on. A repository's malformed input must read as their input being wrong.

    The exception is not rendered — it names the
    offending byte, and the byte came out of the file.

    `where` is how this area's findings already name the file, so the message names the
    configured path rather than an absolute one under a runner's scratch directory. It goes
    through `quoted` here rather than at each caller: a plan's name is the pull request's, and
    `plan check` prints this refusal in CI, so no caller may forget the bound.
    """
    shown = quoted(Path(where).as_posix())
    try:
        # A regular file only: a plan globbed outside a work tree can be a committed link to
        # `/dev/stdin`, which hung `plan check`.
        return read_regular_text(path)
    except UnicodeDecodeError as error:
        raise Failure(f"{shown}: is not valid UTF-8 ({error.reason})") from None
    except OSError as error:
        raise Failure(f"{shown}: could not be read ({said(error)})") from None


def section_lines(text: str, heading: str) -> int | None:
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return end - start


def roadmap_prose(text: str) -> str:
    """The roadmap up to the generated trail listing.

    The trail is regenerated from the corpus and grows with every filed design, so counting
    it would charge the roadmap for documents living elsewhere. Everything above it is the
    hand-written part this budget exists for. See `TRAIL_MARKER_LINE` for why the cut is
    anchored to a whole line.
    """
    return TRAIL_MARKER_LINE.split(text, 1)[0]


def _normalise_target(raw: str) -> str:
    """A markdown link target reduced to its path: no wrapper, anchor, or query."""
    target = raw.strip().strip("<>")
    if target.startswith(_IGNORED_LINK_PREFIXES):
        return ""
    return unquote(target.split("#", 1)[0].split("?", 1)[0])


def local_markdown_targets(text: str) -> list[str]:
    targets = (_normalise_target(m.group(1)) for m in _MARKDOWN_LINK.finditer(text))
    return [target for target in targets if target]


def _over(
    findings: list[Finding], rule: str, path: str, measured: int, config: Config, budget: str
) -> None:
    limit = config.budgets.effective(budget)
    if measured > limit:
        findings.append(Finding(rule, path, None, f"{measured} > {limit} ({budget})"))


def check_budgets(root: Path, config: Config) -> list[Finding]:
    found: list[Finding] = []
    agents_path = contained(root, config.paths.agents_md)
    if not fsops.is_file(agents_path):
        detail = "the always-loaded document does not exist"
        return [Finding("missing-document", config.paths.agents_md, None, detail)]
    agents = read_document(agents_path, config.paths.agents_md)
    _over(
        found,
        "agents-lines",
        config.paths.agents_md,
        len(agents.splitlines()),
        config,
        "agents_md_lines",
    )
    _over(
        found,
        "agents-words",
        config.paths.agents_md,
        len(agents.split()),
        config,
        "agents_md_words",
    )
    status = section_lines(agents, STATUS_HEADING)
    if status is None:
        found.append(
            Finding(
                "status-missing", config.paths.agents_md, None, f"no `{STATUS_HEADING}` section"
            )
        )
    else:
        _over(found, "status-lines", config.paths.agents_md, status, config, "status_lines")
    roadmap_path = contained(root, config.paths.roadmap)
    if fsops.is_file(roadmap_path):
        prose = roadmap_prose(read_document(roadmap_path, config.paths.roadmap))
        _over(
            found,
            "roadmap-lines",
            config.paths.roadmap,
            len(prose.splitlines()),
            config,
            "roadmap_prose_lines",
        )
        _over(
            found,
            "roadmap-words",
            config.paths.roadmap,
            len(prose.split()),
            config,
            "roadmap_prose_words",
        )
    return found


def _links(root: Path, config: Config) -> list[tuple[str, Path]]:
    """Each local link target in the always-loaded document, with where it lands inside the
    root; a target that lands outside it is left out."""
    agents_path = contained(root, config.paths.agents_md)
    if not fsops.is_file(agents_path):
        return []
    found: list[tuple[str, Path]] = []
    agents = read_document(agents_path, config.paths.agents_md)
    # Fenced code is an example, not a claim — the same rule every other reader here applies.
    for target in local_markdown_targets(blank_fences(agents)):
        # Relative to the document's own directory, contained against the project root: a link
        # out of `docs/` into `src/` is inside the project, while `../../../etc/hosts` is not
        # and is never asked of the filesystem — that answer would be about this disk.
        landed = resolves_within(root, target, base=agents_path.parent)
        if landed is not None:
            found.append((target, landed))
    return found


def check_links(root: Path, config: Config) -> list[Finding]:
    return [
        Finding("missing-link", config.paths.agents_md, None, target)
        for target, landed in _links(root, config)
        if present_within(root, landed) is False
    ]


def docs_gate(root: Path, config: Config) -> list[Finding]:
    """The `docs` gate's whole composition: the budgets and the link targets.

    `docs check` with no flag answers with this function.
    """
    return check_budgets(root, config) + check_links(root, config)
