"""The import surface: everything a consumer may import from this area.

A module and not the package's `__init__`, for the reason every area's surface is one: area
discovery imports a package before the submodule it wants, and the package stays empty of imports.

The list is what consumers outside this area actually reach for: the git hook:

- `setup` offers and undoes the git hook (`install`, `uninstall`, `HOOK_NAME`), and `Installed`
  and `Removed` come with the two verbs, because a return type absent from this list is a value
  `setup` can hold and cannot declare; so does `Found`, what `uninstall` found, which its report
  says.
- `setup`'s own tests read the marker that makes a hook drybag's (`HOOK_MARKER`).

An area that needs something absent from this list grows it deliberately, in a commit that says
which area and why.

**What is not here.** The commit rules (`offending_lines`, `check_range`, `strip_message` and
their records) and the failure attribution stay in the modules that define them, each reachable
there: no other area imports them, and a name published for a consumer that does not exist is a
claim nothing checks.
`hooks_dir` and `git_path` are `drybag.gitenv`'s.
"""

from drybag.guards.githooks import (
    HOOK_MARKER,
    HOOK_NAME,
    Found,
    Installed,
    Removed,
    install,
    uninstall,
)

__all__ = [
    "HOOK_MARKER",
    "HOOK_NAME",
    "Found",
    "Installed",
    "Removed",
    "install",
    "uninstall",
]
