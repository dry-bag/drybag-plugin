"""Areas discovered by name: the half `drybag.cli`, `drybag.hooks.registry` and
`drybag.doctor.registry` share.

Three submodules are discovered, each by the module that reads it: an area's `commands.py` gives
it a CLI group, its `hooks.py` hook handlers, and its `doctor.py` rows in `drybag doctor`'s
report. The CLI frame and the hook registry import through `area_modules`, so an area that
fails to import fails them; the report imports through `area_imports`, which hands that failure
over in the area's place, because the report must outlive any one area's broken code.

A leaf module on purpose. `cli.py` imports every area through this discovery, so an area that
imported back into `cli.py` would constrain what the frame may ever import; the aliases and
the loop live here instead, and `cli` re-exports the aliases.
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import drybag

if TYPE_CHECKING:
    SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    # Generic only in typeshed: subscripting the class at runtime raises TypeError.
    SubParsers = argparse._SubParsersAction

Registrar = Callable[[SubParsers], None]

# The sides, by the first part of a module's path under `src/drybag/`; a module neither side
# lists is the core's. CONTRIBUTING.md, "Areas", says what each holds and the rules between them,
# and `tests/boundaries/test_delivery.py` holds those rules. They read code, so they cannot see
# `templates/overlay/`, data only the `overlay` area reads, which sits in the core.
DELIVERY_AREAS: frozenset[str] = frozenset(
    {
        "attach",
        "hints",
        "memory",
        "overlay",
        "setup",
        "sync",
    }
)
PACK_PACKAGES: frozenset[str] = frozenset(
    {
        "docs",
        "guards",
    }
)
CORE_AREAS: frozenset[str] = frozenset(
    {
        "doctor",
        "hooks",
    }
)


def _has_submodule(area: str, submodule: str) -> bool:
    """A filesystem probe: it imports only the `<area>.<submodule>` modules that exist, instead
    of importing every area package merely to test whether one of them does.

    That saving is latent rather than realised on today's frame: `main()` runs
    `discover_registrars()` to build the full argparse parser before argparse can dispatch to
    `hook`, and building that parser already imports `drybag.config`, `drybag.config.schema`,
    `drybag.presets`, `drybag.release` and `drybag.release.pins` — so by the time this
    probe runs for `hooks`, those packages are already loaded. The probe would only pay off if
    discovery ever registered just the area named on the command line instead of the full set.
    """
    for root in drybag.__path__:
        directory = Path(root) / area
        if (directory / f"{submodule}.py").is_file():
            return True
        if (directory / submodule / "__init__.py").is_file():
            return True
    return False


def _qualified(submodule: str) -> list[str]:
    """The qualified name of each `drybag.<area>.<submodule>` that exists, in area-name
    order: the one probe both importers below read."""
    return [
        f"drybag.{entry.name}.{submodule}"
        for entry in sorted(pkgutil.iter_modules(drybag.__path__), key=lambda m: m.name)
        if entry.ispkg and _has_submodule(entry.name, submodule)
    ]


def area_modules(submodule: str) -> list[ModuleType]:
    """Every `drybag.<area>.<submodule>` that exists, in area-name order, no shared registry."""
    return [importlib.import_module(name) for name in _qualified(submodule)]


def area_imports(submodule: str) -> list[tuple[str, ModuleType | Exception]]:
    """Every `drybag.<area>.<submodule>` that exists, in area-name order, each under its
    qualified name with the module, or with the exception its import raised in the module's place.

    For a reader that must outlive an area's broken code: `doctor`'s report, which is what a user
    has left when everything else is broken, so one area's `doctor.py` that raises on import must
    cost that area's rows and not the report. The failure is handed over rather than judged here,
    because what it costs is the reader's to say. `Exception` and not `BaseException`, as for a
    check that raises: a `KeyboardInterrupt` during discovery stops the command.
    """
    found: list[tuple[str, ModuleType | Exception]] = []
    for name in _qualified(submodule):
        try:
            found.append((name, importlib.import_module(name)))
        except Exception as exc:  # an area's broken import is handed over, never raised here
            found.append((name, exc))
    return found
