"""The import surface: everything a consumer may import from this area.

A module and not the package's `__init__`, for the reason `drybag.guards.api`,
and `drybag.memory.api` both give: area discovery imports a package
before it imports the submodule it wants, so a re-export list in `__init__.py` would pull this
whole area — and with it the configuration layer — into every `discover()` call.
`tests/docs/test_surface.py` asserts the `__init__` imports nothing at all.

**Nothing outside this area imports any name on this list**, measured over `src/`,
`scripts/` and `tests/`: this area's own tests reach `drybag.docs.plans`,
`drybag.docs.hygiene` and `drybag.docs.trail` directly, and every other area runs the
commands.
So every name below is here on an argument rather than on a caller, and the argument is
written beside it — a surface that survives a trim with no explanation is what made the trim
necessary.

The two are checks this area *is*, one call each: `check_budgets` and
`check_links` each answer one question about the documentation tree and return `Finding`s from
`drybag.findings`, the leaf three areas share — a consumer imports the finding shape from there,
not from here.

`lint` and the `Lint` it returns are **not** here: `plan check` is this area's own.

`Finding` and `labels` are **not** here: they are `drybag.findings`', and a consumer imports
them from there.

**The trail half is not here.** The markers, the file name, `Trail`, `read_trail`,
`trail_path`, `render_listing`, `rebuild` and `undeclared_new_documents` stay in
`drybag.docs.trail`, which `docs/commands.py` and this area's own tests reach directly: no
other area reads them, and an area that needs one grows this list, in a commit that says which
area and why.

The two checks above have no importer; they stay on an argument about shape — one call per
check rather than the machinery behind it — and on `tests/test_surfaces.py`'s floor, which
refuses a surface exporting no function or record at all. Whether this area publishes at all
is a structural decision and the owner's.
"""

from drybag.docs.hygiene import check_budgets, check_links

__all__ = [
    "check_budgets",
    "check_links",
]
