"""Presets ship inside the package so an installed wheel can read them."""

from __future__ import annotations

import copy
import re
import tomllib
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any, cast

from drybag.errors import Failure

# The rule a preset name is held to, and the same rule in words for the refusal a person reads.
# ASCII and nothing wider: `str.isalnum()` stood here and admitted every Unicode letter and digit,
# which no shipped name uses and the sentence below did not say.
NAME = re.compile(r"\A[A-Za-z0-9_-]+\Z")
NAME_RULE = "ASCII letters, digits, `-` and `_`"
# The preset everything builds on that names none, or names one this build does not ship: a
# document leaving `[drybag] preset` out, a session whose preset will not load, a repository with
# no `drybag.toml`, and `setup` with no `--preset`.
DEFAULT_PRESET = "recommended"


def shipped_presets() -> list[str]:
    """The preset names this build carries — drybag's own vocabulary, and so safe to print."""
    return sorted(
        entry.name.removesuffix(".toml")
        for entry in resources.files(__package__).iterdir()
        if entry.name.endswith(".toml")
    )


def load_preset(name: str, *, key: str = "[drybag] preset") -> dict[str, Any]:
    """The named preset's table, or a refusal that names `key` and never `name`.

    `name` is repository-authored when it comes from a clone's `drybag.toml`, and neither
    refusal may carry it: the first runs exactly for a value that failed the identifier check,
    so it can hold ESC and line breaks, and both reach a terminal and a model through the skills'
    relay. This is `config.loader._enum`'s ruling. The `setup --preset` path passes `key` and is
    held to the same rule rather than given an exception: the person who typed the value has it
    on their own screen, and the list of shipped presets is what they need to fix it.
    """
    if not NAME.match(name):
        raise Failure(f"{key} is not a plain identifier ({NAME_RULE}); {available()}")
    # Membership in the listing, not `joinpath(...).is_file()`: the filesystem answered that, and
    # on macOS's default case-insensitive one `"RECOMMENDED"` loaded where Linux refused it, so a
    # configuration checked on one machine failed CI on another. The identifier check above now
    # only chooses which refusal a value gets.
    if name not in shipped_presets():
        raise Failure(f"{key} names a preset this version of drybag does not ship; {available()}")
    # A copy of the one parse: a hook loads the preset more than once (the owner's layer, then the
    # project's), and a caller that edits what it is handed edits no other caller's.
    return copy.deepcopy(_parsed(name))


@cache
def _parsed(name: str) -> dict[str, Any]:
    """The shipped preset `name`, parsed once per process; `load_preset` has held the name to
    `shipped_presets()` before it asks."""
    resource = resources.files(__package__).joinpath(f"{name}.toml")
    return tomllib.loads(resource.read_text(encoding="utf-8"))


def defaults_of(preset: Mapping[str, Any]) -> dict[str, Any]:
    """The `[defaults]` of the loaded `preset`, by section: what a layer builds on where it sets
    nothing, `{}` for a preset with none. Not a copy: `load_preset` hands every caller its own."""
    return cast(dict[str, Any], preset.get("defaults", {}))


def available() -> str:
    """The clause every refusal of a preset name ends with: the names this build ships."""
    return f"available: {', '.join(shipped_presets()) or 'none'}"
