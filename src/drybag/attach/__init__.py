"""Bind a repository to the machine owner's private overlay, and take back what binding wrote.

An area, discovered by name: `commands.py` gives it the `attach` and `detach` groups and
`api.py` is what another area may import. `detach` takes back what `attach` wrote and leaves the
overlay's record of the binding, `projects/<name>/project.toml`, where it is. It writes nothing
a repository chose — the overlay root comes from the machine file, the store is that overlay's
own directory for this project, and what the overlay grants is shown on one screen rather than
asked about.
"""

# The command that binds this checkout to its share of the overlay, which every remedy of this area
# names, so a reader is never sent to two spellings of one command. No `--store`: the store is
# derived from the machine-recorded overlay, and a placeholder an agent runs as written exits 2.
# Here and not in `doctor.py` because `hooks.py` names it too, and discovery imports `hooks.py`
# unguarded for every hook: an edge to the report's module would let a defect there stop every
# hook, and load the report on each one. Not annotated `Final`: an area's `__init__.py` imports
# nothing, `typing` included (`tests/test_surfaces.py`).
ATTACH_COMMAND = "drybag attach"
