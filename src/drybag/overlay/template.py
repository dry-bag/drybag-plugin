"""Where the shipped overlay tree is, and what `plan()` is handed for it.

`templates/overlay/` lives **under the module root**, beside `presets/`, and for the same
reason: it is read at *runtime* — `setup --overlay <path>` renders it — so the copy that has to
answer is the one an installed drybag carries. The conventional plugin layout puts the tree at
the plugin root, and this repository has already departed from that layout once, for
`presets/recommended.toml`, which `load_preset` reads out of the package. Two facts settle it
here. `uv_build` has no wheel includes at all: "all data files must either be under the module
root or in the appropriate data directory", so `source-include` reaches the sdist and nothing
else. And `setup --overlay <path>` renders it with no network call, the way to an overlay when the
template repository is out of reach, while the skills reference the CLI by name — so the copy that
runs is the one on `PATH`, and a render absent from the wheel is no way at all.
`hooks/` stays at the plugin root, because the *harness* reads it from there and Python never
does.

`template_root()` is still a function rather than a constant, because `resources.files` is what
answers for an installed package and a checkout alike. **There is no checkout fallback**, and
that is the point of the move: an earlier revision carried one, copying the scaffold engine's
checkout probe, since deleted, and its `parents[3]` arithmetic into this area — a second
spelling of one rule, across two areas, and after the move an unreachable one. A source
checkout is an `src/` layout, so `resources.files("drybag")` answers `src/drybag` there and
the tree is under it; there is no arrangement left in which the package probe misses and a
repository-root walk would have found it.
"""

from __future__ import annotations

from functools import partial
from importlib import resources
from pathlib import Path

from drybag.errors import Failure
from drybag.overlay.layout import OVERLAY_FILES
from drybag.scaffold import Template

OVERLAY = "overlay"


def template_root() -> Path:
    """The directory `drybag/templates/overlay/` resolves to for this installation.

    A path is always returned, existing or not, so a caller that cannot find the tree can name
    where it looked instead of handling a `None`. `templates()` is the one place that becomes a
    refusal a user can act on. `resources.files` answers a `Path` for every filesystem install,
    which is every install this project supports; `fsops` contains writes with `dir_fd=` and
    `O_NOFOLLOW`, so a zip-imported drybag could not write an overlay in any case.
    """
    package = resources.files("drybag").joinpath("templates", OVERLAY)
    return package if isinstance(package, Path) else Path(str(package))


def _render(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def templates() -> list[Template]:
    """One whole-file `Template` per shipped file, rendered from the tree at apply time.

    Every artifact is a whole-file template: the overlay is a repository drybag creates outright,
    so there is no file of somebody else's to merge a region or a keyed entry into. That is
    what makes `overlay upgrade` the engine's hash-and-skip rule rather than a second copy of
    it — an untouched file is refreshed, an edited one is skipped and named.
    """
    root = template_root()
    if not root.is_dir():
        raise Failure(
            f"the overlay template tree is not readable at {root}; this drybag was installed "
            "without it, so `setup --overlay <path>` cannot render one"
        )
    return [
        Template(
            id=relative,
            target=relative,
            source=f"{OVERLAY}/{relative}",
            render=partial(_render, root / relative),
        )
        for relative in OVERLAY_FILES
    ]
