"""The setup area's import surface: everything another area may import from it.

No module imports from here but the tests.

An area that needs something absent from this list grows it deliberately, in a commit that says
which area and why — it does not import a private module of this area.

**`setup` and `SetupReport` have no importer outside this area, and the sentence that said they
did was false.** It named `tests/test_install_path.py` — "the walkthrough that runs the four
packages in the order a person does, drives `setup` itself" — and that walkthrough drives the
argument parser, `["setup", ...]`, which is the point of it: the commands' argv wiring is what
it exists to exercise. It reaches no name on this list. The claim is the same shape as the one
already removed from this very docstring, two paragraphs down, and it survived the first count
of this surface's importers because that count covered `src/` and `scripts/` and `tests/` and
never read the prose.

They stay, and the reason is the one `docs/api.py` records for its own list. Trimming to
`USER_SETTINGS` alone leaves a surface with no exported callable or record on it, and
`tests/test_surfaces.py`'s derived check refuses that outright — "the surface exports no function or
record, so this checked nothing". So what is left is not a trim of two names; it is the question of
whether this area publishes at all, which is the owner's and not a refactor's. `SetupReport` is here
because `setup` is: a return type absent from this list is a value a caller can hold and cannot
declare.

**Trimmed: `read_machine`, `write_machine` and `Written`.** This docstring used to claim
`doctor` "needs the machine reader and `USER_SETTINGS` by name"; `doctor` imported
`USER_SETTINGS` alone, so `read_machine` and `write_machine` left with the claim, and `Written`
— `write_machine`'s result, discarded at both of its call sites — left with them.
"""

from drybag.setup.machine import USER_SETTINGS
from drybag.setup.run import SetupReport, setup

__all__ = [
    "USER_SETTINGS",
    "SetupReport",
    "setup",
]
