"""The attach area's import surface: everything another area may import from it.

The list is chosen from what consumers outside this area actually reach for, and every name on
it carries the reason beside it — a surface that survives a trim with no explanation is what
made the trim necessary. An area that needs something absent from this list grows it
deliberately, in a commit that says which area and why — it does not import a private module of
this area.

No module under `src/` outside this area imports from here. `doctor` was the one consuming area,
and its `attached` row is now this area's own (`attach/doctor.py`), which reaches this area's
modules directly. What is left is what the suite
reads from outside the area:

- the ledger (`ledger`) and `AttachLedger`, because `ledger` returns it: a return
  type absent from this list is a value a consumer can hold and cannot declare, and
  `tests/test_surfaces.py` derives that rule rather than restating it; `Rendered`, the type of
  its `render`, for the same rule;
- the binding (`read_binding`, `Binding`). `Binding.state` is one of `memory.api`'s
  `BINDING_STATES`: the vocabulary and the one classifier that decides between its members are
  the memory area's, so this area publishes neither a copy of the set nor its own members;
- the file an earlier release merged the overlay's hook entries into (`LOCAL_SETTINGS`), which
  the doctor area's tests write those entries into;
- `attach` and `Attached`, which `setup --restore` runs on each checkout the overlay's projects
  file lists (`setup.restore`), and the type it returns, with `Carried`, the type of two of its
  fields (`added` and `removed`), which `tests/test_surfaces.py` derives that a surface carries.

**Trimmed when the `attached` row moved into this area: `overlay_entries`.** It was published for
`doctor`'s provenance column, and went once `attach` merged no hook entry.

**`setup` was not a consumer of `check`, and still is not.** This docstring used to say "`setup`
runs `attach --check` and reads the same `Result`"; `setup` imports nothing of `check`, and
`attach --check` is this area's own command module calling its own `attach.check`. (`setup
--restore` does import `attach` itself, which is the bullet above.) The
permission-diff half of the area — `check`, `diff_permissions` and the `PermissionDiff` it
returns — was published on the strength of that sentence and left when the sentence did.

**Trimmed once, and back for `setup --restore`: `attach` and `Attached`; `detach` and
`Detached` are trimmed again since the `uninstall` that ran `detach` was deleted.** They were
published against a second sentence of the same kind — "`tests/test_install_path.py`, the
walkthrough that runs the four packages in the order a person does, is the other consumer, and it
drives `attach` and
`detach`, whose returns are `Attached` and `Detached`". That walkthrough drives the argument
parser: `step("attach", "--store", ..., "--machine", ...)`, which is the point of it, because the
commands' argv wiring is what it exists to exercise. It imports two surfaces,
`drybag.doctor.api` and `drybag.memory.api`, and reaches no name on this list. The two verbs
are this area's own command module's, and their results are read where they are returned.

The claim is the same shape as the one this docstring corrected two paragraphs up, and as the
one `setup/api.py` corrected about its own `setup` and `SetupReport` — three of them now, all
naming a test that runs a command rather than a module that imports a name. A published surface
argued from prose is a surface nobody measured: the first count of this list's importers
covered `src/`, `scripts/` and `tests/` and never read the sentence, and the sentence is what
kept the names.

The ledger's place is the core's (`binding.LEDGER_FILE`, beside the binding id), because `doctor`
reads it too; nothing outside delivery imports this area but through the crossings pinned in
`tests/boundaries/test_delivery.py`.
"""

from drybag.attach.binding import Binding, read_binding
from drybag.attach.permissions import LOCAL_SETTINGS
from drybag.attach.render import Rendered
from drybag.attach.write import Attached, AttachLedger, Carried, attach, ledger

__all__ = [
    "LOCAL_SETTINGS",
    "AttachLedger",
    "Attached",
    "Binding",
    "Carried",
    "Rendered",
    "attach",
    "ledger",
    "read_binding",
]
