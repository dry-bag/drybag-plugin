"""The composed store: the one directory on this machine that the harness reads memory from.

**One directory, named by the binding id, outside every checkout.** `<data>/stores/<id>/` holds a
link per group into the overlay — `developer` to `<overlay>/common/memory`, the shared notes,
`tag-<tag>` to the `memory/` of each tag the project's record lists (where that is a directory) and
each directory of `<overlay>/projects/<project>/memory/` to itself, but for the names that start
with `tag-` — and `MEMORY.md`, a link to the index `drybag memory index` renders in that share.
`attach` builds it in `overlay` mode and
writes its absolute path as `autoMemoryDirectory` into the main checkout's excluded
`settings.local.json`, which Claude Code reads from every worktree, so the harness reads the
overlay's notes through one key and no per-worktree link. `<project>` is the project this machine's
binding record names, which the caller passes, and the groups are the directories the overlay
holds for it: no name the repository chose becomes a path here.

**It is the overlay-mode store drybag reads too** (`store.resolve`), so the bundles and the
harness read one directory, and `attach` renders the index behind its `MEMORY.md` link where the
overlay has none yet (`render_missing_index`), so neither reads a dangling one.

**What drybag places here is links, and only links are taken.** The harness writes into the
directory it reads, so a file here that is not a link is a note it wrote, and neither a rebuild nor
a withdrawal removes it. `build` makes the store's links exactly the overlay's current set and
`withdraw` takes them all, then the directory where nothing else is left. Both list this
directory, which is derived from the id and never read from a record a checkout keeps, and only
`attach` puts links in it (CONTRIBUTING.md#enumerated-writes). The path names the whole id, so it
is never printed whole: a fault names the data directory and the id's short form
(`binding.unwritten`), and every message naming a path in it goes through `store.shown`.
"""

from __future__ import annotations

import os
from pathlib import Path

from drybag import fsops
from drybag.binding import unwritten
from drybag.config.plates import PlateKind, plates
from drybag.config.schema import Config
from drybag.errors import Failure
from drybag.memory.index import INDEX_NAME, index_source, reconcile, render_index, write_index
from drybag.memory.notes import walk
from drybag.memory.reviewed import promote, unreviewed
from drybag.memory.store import (
    COMMON_GROUP,
    STORE_DIR,
    composed_directory,
    overlay_group_target,
    project_notes,
    reserved,
    resolve,
    share_directories,
    tag_link,
)


def link_sources(overlay: Path, config: Config) -> tuple[Path, ...]:
    """Every path in the overlay a link of the store points at for the configured groups: the
    index in this project's share, then each group's directory. `attach` asks each, above its
    first write, whether a path that long can exist on this machine
    (`attach.binding.refuse_unless_share_can_exist`): the index is what `render_missing_index`
    writes and every group's but the common one a directory `attach` creates."""
    project = config.project.name
    held = plates(overlay, project)[0]
    return (
        project_notes(overlay, project) / INDEX_NAME,
        *(overlay_group_target(held, g) for g in config.memory.groups),
    )


def _sources(overlay: Path, project: str) -> dict[str, Path]:
    """Each name the store holds and the path in the overlay its link points at: the common notes,
    `tag-<t>` for each usable tag whose `memory/` is a directory (a link at none would take a
    note written through it into nowhere), each directory of the project's share that is not in
    the reserved names, and the index."""
    held = plates(overlay, project)[0]
    share = project_notes(overlay, project)
    found = {COMMON_GROUP: overlay_group_target(held, COMMON_GROUP)}
    for plate in held:
        target = plate.directory / STORE_DIR
        if plate.kind is PlateKind.TAG and fsops.is_dir(target):
            found[tag_link(plate.name)] = target
    for entry in share_directories(overlay, project):
        if not reserved(entry):
            found[entry] = share / entry
    found[INDEX_NAME] = share / INDEX_NAME
    return found


def promote_share(overlay: Path, project: str, *, machine: Path | None) -> list[Path]:
    """Record as reviewed on this machine each note of the overlay's share for `project` that the
    record does not hold at its bytes, walked in the overlay itself through every group `build`
    links, the common one included: the path of each note recorded, none when all are. A trust
    record that cannot be read refuses (`UnreadableTrust`) rather than being replaced."""
    groups = [path for name, path in _sources(overlay, project).items() if name != INDEX_NAME]
    chosen = unreviewed(machine, [n for g in groups for n in walk(g.parent, [g.name]).notes])
    return list(promote(machine, chosen)) if chosen else []


def _links(directory: Path) -> list[str]:
    """The names of the links in `directory`, which is there."""
    return sorted(name for name in os.listdir(directory) if fsops.is_symlink(directory / name))


def build(overlay: Path, project: str, id: str) -> Path:
    """Make `<data>/stores/<id>/` hold a link to each of the overlay's groups for `project` and to
    its index, and no other link, and return it. A file that is not a link is left where it is."""
    directory = composed_directory(id)
    sources = _sources(overlay, project)
    try:
        # The plain-path form for the data directory itself, as `binding.record` writes it.
        directory.mkdir(parents=True, exist_ok=True)
        for name in _links(directory):
            if name not in sources or fsops.readlink_within(directory, name) != sources[name]:
                fsops.unlink_within(directory, name)
        for name, source in sources.items():
            if not fsops.exists(directory / name) and not fsops.is_symlink(directory / name):
                fsops.symlink_within(directory, name, source)
    except OSError as exc:
        raise unwritten(directory.parent, id, exc) from None
    return directory


def withdraw(id: str) -> bool:
    """Take every link out of `<data>/stores/<id>/`, then the directory where nothing is left;
    whether no store is left. A note the harness wrote there keeps it."""
    directory = composed_directory(id)
    if not fsops.is_dir(directory):
        return True
    try:
        for name in _links(directory):
            fsops.unlink_within(directory, name)
        if os.listdir(directory):
            return False
        fsops.rmdir_within(directory.parent, directory.name)
    except OSError as exc:
        raise unwritten(directory.parent, id, exc) from None
    return True


def render_missing_index(root: Path, config: Config, *, machine: Path | None) -> None:
    """Render the index behind the store's `MEMORY.md` link where the overlay has none yet.

    A first attach linked `MEMORY.md` at a file nothing had written, so every session read a
    dangling link and `memory index --check` failed until someone ran `memory index` by hand. This
    is that command's own render, with the notes left as they are (`write=False`: an attach adds no
    `index:` line to a note). After `build`, whose link is what makes the render's destination the
    overlay's copy."""
    store = resolve(root, config, machine=machine)
    if store is None:
        raise Failure(
            "the composed store was built and the store still does not resolve; "
            "`drybag memory index --check` reports why"
        )
    if index_source(store, config) is not None:
        return
    write_index(store, config, render_index(reconcile(store, config, write=False), config, store))
