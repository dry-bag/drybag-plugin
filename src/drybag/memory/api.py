"""The memory area's import surface: everything a consumer may import from this area.

A module and not the package's `__init__`, for one reason: discovery imports an area's
`drybag.memory.<module>` through the package, so a re-export list in `__init__.py` pulls the
whole area — configuration included — into every discovery and reddens
`tests/boundaries/test_discovery.py`. Keeping the surface one level down costs a consumer
six characters and keeps discovery cheap, without a lazy `__getattr__` that would cost every
consumer its types.

**The list is what consumers outside this area import, plus what those names oblige** — not
what an area might be said to need, which is a list that grows names nothing imports. An area
that needs something absent from this list grows it deliberately, in a commit that says which
area and why — it does not import a private module of this area.

The names imported from outside this area today, by the `attach` and `overlay` areas and by this
repository's tests: the resolver (`resolve` and `project_notes`), the overlay layout `attach`
writes and `overlay` renders (`PROJECTS`, `RECORD`, `STORE_DIR`), the
note roots `sync`'s pathspecs are built from beside `config.plates.GROUPS` (`COMMON`,
`PROJECTS`), and the index's name, which those pathspecs leave out under each project, since each
machine renders its own (`INDEX_NAME`), the one
reader of the overlay's project record, which `attach` reads it with and answers its own way when
it cannot (`read_binding_record`), the binding's one classifier and the states it answers in
(`binding_state`, `UNBOUND` and `BOUND`, which `attach`'s own doctor row reads), which `attach`
answers the binding question with instead of a second classifier of its own, and the note a moved
`origin` earns (`origin_note`), which `attach` and its session-start line say, the bundle slots
`tests/hooks/test_hooks_json.py` holds the hooks file to (`SLOTS`), the composed store `attach`
builds, points the harness at, renders the missing index of and `detach` withdraws, its
directory, which the `attach` area's `doctor.py` compares the pointer with, and every path its
links point at in the overlay, which `attach` asks the length of before its first write
(`build_composed`, `composed_directory`, `render_missing_index`, `withdraw_composed`,
`link_sources`), the project's memory directories the store leaves unlinked for being in the tag
namespace, which `attach`'s doctor row names (`reserved_project_groups`), and the trust region
`tests/test_install_path.py` asserts end to end (`DELIMITER`, `markers`). The note walk and
`resolved` have no reader outside this area and are not here. The overlay root the machine file
records and the checkout's
`origin` are the core's to answer (`config.overlay.overlay_root`, `gitenv.origin_remote` and
`gitenv.GitUnavailable`), and the core may not import this area; the
crossings still pinned are listed in `tests/boundaries/test_delivery.py`.

**`promote_share`** is imported by `setup --restore --yes` (`setup.restore`), which records the
notes of the overlay's share for each project it attaches as reviewed.

**Three more have no importer and stay, each for a reason written here**, because a name
kept in silence is what made this pass necessary:

- **The store, and the rest of the binding's vocabulary**: `Store` is what `resolve` returns, and a
  return type absent from a surface is a value a consumer can hold and cannot declare, which
  `tests/test_surfaces.py` derives rather than restates.
  `BINDING_STATES` completes the closed vocabulary `binding_state` answers in, beside the two
  states that have a caller, for the reason the trust region's bullet below gives.

- **The rest of the trust region's vocabulary**: `wrap`, beside the `DELIMITER` and `markers`
  that already have a caller. Reading such a region needs `markers` and `DELIMITER`; producing
  one needs `wrap`, which draws its own nonce. Publishing the reading half and hiding the writing
  half leaves a consumer able to recognise the region and unable to make one, which is the reason
  `attach/api.py` gives for `STATES` and `doctor/api.py` for `STATUSES`: half a closed
  vocabulary cannot be used by the consumer that is handed a value from it. The project's
  standing constraint is that anything a repository authored reaches a model only inside such a
  region, so a consumer that injects repository bytes has to be able to make one.

**Not here, though they look like candidates**: names with no importer in `src/`, `scripts/` or
`tests/`, no published signature naming them, and no reader reaching them by string. Each is
reachable from this area's own module, which is what `memory/commands.py` and
this area's tests use; an area that comes to need one grows this list by the rule above.

- **The index group** — `index_source`, `write_index`, `render_index`, `render_current`,
  `check_index`, `reconcile`, `Reconciliation`, `IndexCheck`. `attach`, which creates the
  symlinked index, calls none of them; the index is a command (`memory index`), and this area's
  own `index-render` hook runs it.
- **The notes group** — `read_note`, `render_note`, `with_index`, `write_note`, `NoteError`,
  `NoteType` — and nothing outside this area imports them.
- **The inventory group** — `inventory`, `totals`, `Entry` — and nothing outside this area
  imports them.
- **The store predicates** — `in_repository`, `overlay_group_target` and `resolved`, none with
  a reader outside this area.
- **The helpers** — `blocks` and `split`, with no reader outside this area.
- **The bundles group** — `fit`, `render`, and the `Bundle` and `Fit` their signatures name. The
  `bundles` row is this area's own (`memory/doctor.py`) and reads `memory.bundles` directly.
"""

from drybag.memory.bundles import SLOTS
from drybag.memory.composed import build as build_composed
from drybag.memory.composed import link_sources, promote_share, render_missing_index
from drybag.memory.composed import withdraw as withdraw_composed
from drybag.memory.index import INDEX_NAME
from drybag.memory.store import (
    BINDING_STATES,
    BOUND,
    COMMON,
    PROJECTS,
    RECORD,
    STORE_DIR,
    UNBOUND,
    Store,
    binding_state,
    composed_directory,
    origin_note,
    project_notes,
    read_binding_record,
    reserved,
    reserved_project_groups,
    resolve,
)
from drybag.memory.trust import DELIMITER, markers, wrap

__all__ = [
    "BINDING_STATES",
    "BOUND",
    "COMMON",
    "DELIMITER",
    "INDEX_NAME",
    "PROJECTS",
    "RECORD",
    "SLOTS",
    "STORE_DIR",
    "UNBOUND",
    "Store",
    "binding_state",
    "build_composed",
    "composed_directory",
    "link_sources",
    "markers",
    "origin_note",
    "project_notes",
    "promote_share",
    "read_binding_record",
    "render_missing_index",
    "reserved",
    "reserved_project_groups",
    "resolve",
    "withdraw_composed",
    "wrap",
]
