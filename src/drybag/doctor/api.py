"""The doctor area's import surface: everything another area may import from it.

The command module in this area is its first consumer. An area that needs something absent from
this list grows it deliberately, in a commit that says which area and why — it does not import
a private module of this area.

Every name below is here for a reason written beside it, because a name kept with no reason is
one nobody can tell from a leftover:

- `run_checks` is the report, and `Check` is the row it is made of — a return type absent from
  this list is a value a consumer can hold and cannot declare, which is the one thing a surface
  exists to prevent. `tests/test_surfaces.py` derives that rule rather than restating it.
- `OK`, `WARN`, `RED`, `SKIP` and the `STATUSES` they belong to are `Check.status`'s closed
  vocabulary, and the set is the export rather than the members that have a caller today, for
  the reason `attach/api.py` gives about `Binding.state`: half a closed vocabulary cannot be
  read by the consumer that gets a value from it. `tests/test_install_path.py` branches on
  `RED` and `SKIP` through this surface already.

- `Row`, `Context` and `Contribution` are what an area's own `doctor.py` speaks: its
  `register()` returns a `Contribution` of `(name, check)` pairs, and each check takes the
  `Context` and answers a `Row`. They are defined in `doctor/model.py`, which imports nothing of
  any area, and published here because an area's `doctor.py` is a consumer like any other.
- `Status` is the type `Row.status` declares, for a check that decides its status before it
  builds its row, as an area's own check may: an annotation it could not import would be a type
  it could hold and not declare.
- `MachineUnread` is one of the answers `Context.overlay_root` gives, a machine file that could
  not be read: a row that reads the root cannot tell it from "no overlay recorded" without it,
  and told as that, `pre-commit`, `overlay-requires` and `attached` said a file nobody could read
  recorded nothing.
- `NO_CONFIG_SKIP` is the skip of a row that reads the project configuration in a repository with
  none: the areas' `attached` and `bundles` give it as the core's rows do, so one
  sentence says one state, through `configured`.
"""

from drybag.doctor.checks import run_checks
from drybag.doctor.model import (
    NO_CONFIG_SKIP,
    OK,
    RED,
    SKIP,
    STATUSES,
    WARN,
    Check,
    Context,
    Contribution,
    MachineUnread,
    Row,
    Status,
    configured,
)

__all__ = [
    "NO_CONFIG_SKIP",
    "OK",
    "RED",
    "SKIP",
    "STATUSES",
    "WARN",
    "Check",
    "Context",
    "Contribution",
    "MachineUnread",
    "Row",
    "Status",
    "configured",
    "run_checks",
]
