"""The overlay's plates for a bound project, in the one order every reader takes them.

A plate is a directory of the owner's overlay that holds layers of one kind: `common/`, then one
`groups/<tag>/` for each tag the project lists, then `projects/<name>/`. A later plate wins a value
or an item name and content otherwise unions, so the order is the precedence: `common`, the
project's tags in list order, the project, and (for a value) the machine file after all of them.

**The tags are read from the overlay's record of the project, never from the repository.** The
record is `projects/<name>/project.toml`, its `tags` key a list of names, and the project is the
one this machine's binding record names (`binding.bound_project`), so a clone that commits a
`drybag.toml` or a note naming a tag chooses none: the owner's overlay and the owner's machine
are the anchors, and the repository can write neither. A tag is `TAG`, a name that is also a
directory name, a link name and a TOML string; one outside it, one with no `groups/<tag>/`
directory and a record that cannot be read are each a note and no plate, never a refusal, because
a session never dies of its configuration. A tag listed twice is one plate, at its first place; a
`groups/<tag>` that is a link to a directory is a directory, the overlay being the owner's.

The record has one reader, `read_record`, which raises what it meets and has no policy of its own:
`tags` says what an unreadable record means here (a note), and `memory.store.read_binding_record`
and `attach` say what it means to them.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from drybag import fsops
from drybag.config.reading import UNPARSEABLE, read_fault
from drybag.printed import clipped, quoted

COMMON = "common"
GROUPS = "groups"
PROJECTS = "projects"
RECORD = "project.toml"
# The name a tag's notes take as a link in the composed store, `tag-<tag>`: the project side of the
# store gives up this prefix (`memory.store.reserved`), and `attach` creates no group directory
# that starts with it. Named here, in the core, because both delivery areas spell it.
TAG_LINK_PREFIX = "tag-"
# A named cap: a tag is at most forty characters, from a lowercase letter or digit, then those and
# hyphens. The name becomes a directory name, a link name, an index heading and a TOML string, and
# forty is past every tag a person types. No shipped file states it.
TAG = re.compile(r"\A[a-z0-9][a-z0-9-]{0,39}\Z")

UNLISTED = "so the project lists no tags"
NOT_A_LIST = "{path}: tags must be a list, so the project lists no tags"
NOT_A_STRING = "{path}: the tags entry {position} is not a string; it is skipped"
NOT_A_TAG = (
    "{path}: the tag {tag} is not 1 to 40 lowercase letters, digits and hyphens, "
    "starting with a letter or digit; it is skipped"
)
NO_GROUP = "{path}: the tag {tag} has no directory {group}; it is skipped"


class PlateKind(StrEnum):
    """Which kind of directory a plate is."""

    COMMON = "common"
    TAG = "tag"
    PROJECT = "project"


@dataclass(frozen=True)
class Plate:
    """One directory of the overlay: `name` is `common`, the tag, or the project's name."""

    kind: PlateKind
    name: str
    directory: Path


def read_record(path: Path) -> dict[str, Any]:
    """The project record at `path` as `tomllib` parses it.

    The one reader of `projects/<name>/project.toml`, with no failure policy: an `OSError`, a
    `UnicodeDecodeError` and each of `UNPARSEABLE` are raised as met, because what a record that
    cannot be read means is each caller's to say. Read through `fsops.read_regular_text`, so a
    record swapped for a FIFO or a device is refused unread and one past the cap is refused too.
    """
    return tomllib.loads(fsops.read_regular_text(path))


def _listed(overlay: Path, project: str) -> tuple[list[Any], str | None]:
    """The entries of the `tags` list `project`'s record holds, and the note for a record that is
    present and unusable (`record_fault`): it cannot be read, or `tags` is not a list. No record, or
    a record with no `tags` key, lists none and is no fault. An entry that is not a string is
    `tags`' to note, not a fault."""
    record = overlay / PROJECTS / project / RECORD
    shown = quoted(str(record))
    try:
        listed = read_record(record).get("tags")
    except FileNotFoundError:
        return [], None
    except (UnicodeDecodeError, OSError, *UNPARSEABLE) as exc:
        return [], read_fault(shown, exc, UNLISTED)
    if listed is None:
        return [], None
    if not isinstance(listed, list):
        return [], NOT_A_LIST.format(path=shown)
    return listed, None


def record_fault(overlay: Path, project: str) -> str | None:
    """Why `project`'s record cannot say which tags it lists, where it cannot. Readers of a
    session take that as no tags and say it; `attach` refuses it, since a run that took it so
    would withdraw what the tags delivered."""
    return _listed(overlay, project)[1]


def tags(overlay: Path, project: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The usable tags of `project` in list order, each once, and the notes: `record_fault`, a tag
    outside `TAG`, an entry that is not a string and a tag with no `groups/<tag>/` directory, each
    noted once however often it is listed."""
    listed, fault = _listed(overlay, project)
    if fault is not None:
        return (), (fault,)
    shown = quoted(str(overlay / PROJECTS / project / RECORD))
    usable: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    for position, tag in enumerate(listed, 1):
        if not isinstance(tag, str):
            notes.append(NOT_A_STRING.format(path=shown, position=position))
            continue
        if tag in seen:
            continue
        seen.add(tag)
        if TAG.match(tag) is None:
            notes.append(NOT_A_TAG.format(path=shown, tag=clipped(tag)))
        elif not _directory(overlay / GROUPS / tag):
            notes.append(NO_GROUP.format(path=shown, tag=tag, group=quoted(f"{GROUPS}/{tag}")))
        else:
            usable.append(tag)
    return tuple(usable), tuple(notes)


def plates(overlay: Path, project: str) -> tuple[tuple[Plate, ...], tuple[str, ...]]:
    """`common`, each usable tag of `project`, then the project, and the notes `tags` gave. The
    project's plate is present whether or not its directory exists: readers already handle an
    absent share."""
    used, notes = tags(overlay, project)
    return (
        (
            Plate(PlateKind.COMMON, COMMON, overlay / COMMON),
            *(Plate(PlateKind.TAG, tag, overlay / GROUPS / tag) for tag in used),
            Plate(PlateKind.PROJECT, project, overlay / PROJECTS / project),
        ),
        notes,
    )


def _directory(path: Path) -> bool:
    """`path` reaches a directory; one that cannot be asked about is not one."""
    try:
        return fsops.is_dir(path)
    except OSError:
        return False
