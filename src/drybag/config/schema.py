"""Typed sections of drybag.toml. Numbers come from the preset, never from code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar

# The future import above is load-bearing for the loader: it turns every annotation here into
# a string, which `config.reading.schema_types` resolves with `typing.get_type_hints` to decide
# how to coerce a value. Every name an annotation uses must stay resolvable from this module's
# globals at runtime, so a type imported only under `TYPE_CHECKING` would break the loader.

# The one grammar for a name drybag answers to: a project or an overlay owner. Each is
# one lowercase path segment whose leading class keeps it out of an option's position in an argv.
# Every other module derives from this spelling rather than keeping its own.
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\Z")
# `PROJECT_NAME` in words, for every refusal a person reads: the pattern itself ends in Python's
# `\Z`, which means nothing to a reader and something else to every other regex dialect.
NAME_RULE = (
    "one lowercase path segment: lowercase letters, digits, '.', '_' and '-', led by a letter "
    "or digit"
)
# The grammar an unknown *name* in a `drybag.toml` must match before a refusal may print it —
# a top-level section's, and a key's inside a known table. Both are repository-authored the same
# way a `[paths]` value is, and a refusal that echoed one back would print it whole, newlines
# and all. A TOML key is arbitrary quoted
# text, so the two are one grammar and not two: every name drybag itself answers to — every
# schema field, every `Budgets.NAMES` entry, every section — is lowercase words joined by
# underscores, and anything else is counted rather than quoted.
SECTION_NAME = re.compile(r"^[a-z][a-z_]*\Z")
# The one grammar for a branch name drybag reads or writes: `[project] base_branch`, which the
# loader holds to it, `--base-branch`, and a detected base branch. Each is repository-authored or
# typed, and each reaches a git ref or an output line, where quoting alone would still admit a
# newline. Inside that character set it refuses what git's own
# branch-name rules refuse (`git check-ref-format --branch`), so nothing names a branch no
# repository can have: `..`, `//`, a component starting with `.`, a component ending in `.lock`,
# a trailing `/` or `.`, and the name `HEAD` itself. `@{` and `-` at the start are outside the
# set already.
BRANCH_NAME = re.compile(
    r"^(?!HEAD$)(?!.*\.\.)(?!.*//)(?!.*/\.)(?!.*\.lock(?:/|$))(?!.*[./]$)"
    r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z"
)
# `BRANCH_NAME` in words, for a refusal a person reads; a change to the grammar changes this.
BRANCH_RULE = (
    "letters, digits, '.', '_', '-' and '/', led by a letter or digit, and one git accepts "
    "(no '..' or '//', no component starting with '.' or ending in '.lock', no trailing '/' or "
    "'.', and not HEAD)"
)
# The memory modes, each spelled here and nowhere else, since the areas that read the store branch
# on them: the agent harness's own memory, where drybag delivers nothing, which is the mode
# where no layer chooses one; notes in the private overlay, the one mode `attach` binds; notes
# committed with the code; and notes on this machine only, under `.drybag/local/`.
HARNESS_MODE = "harness"
OVERLAY_MODE = "overlay"
IN_REPO_MODE = "in-repo"
LOCAL_ONLY_MODE = "local-only"
MEMORY_MODES = (HARNESS_MODE, OVERLAY_MODE, IN_REPO_MODE, LOCAL_ONLY_MODE)


@dataclass(frozen=True)
class Drybag:
    version: str
    preset: str


@dataclass(frozen=True)
class Project:
    name: str
    base_branch: str


@dataclass(frozen=True)
class Paths:
    agents_md: str
    specs: str
    plans: str
    roadmap: str
    memory: str

    def as_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Memory:
    """`[memory]`. `mode` is `HARNESS_MODE` where no layer sets it: the preset chooses no store, so
    a repository that never chose one keeps the harness's own memory."""

    groups: tuple[str, ...]
    index_extra: tuple[str, ...]
    mode: str = HARNESS_MODE


@dataclass(frozen=True)
class Budgets:
    """The preset's budgets and the ones a layer set (`configured`): the base branch's
    `drybag.toml` sets a gate's budget and the owner's machine file a session's, either way
    above the preset or below it."""

    NAMES: ClassVar[tuple[str, ...]] = (
        "agents_md_lines",
        "agents_md_words",
        "status_lines",
        "roadmap_prose_lines",
        "roadmap_prose_words",
        "memory_index_words",
        "startup_rules_words",
        "volatile_notes_words",
        "volatile_ttl_days",
    )

    preset: dict[str, int]
    configured: dict[str, int] = field(default_factory=dict)

    def effective(self, name: str) -> int:
        """The configured value when a layer set one, else the preset's."""
        if name not in self.NAMES:
            raise KeyError(name)
        if name in self.configured:
            return self.configured[name]
        return self.preset[name]


@dataclass(frozen=True)
class NativeCaps:
    """Platform limits in their own units; consumers of a bounded thing read them."""

    NAMES: ClassVar[tuple[str, ...]] = (
        "memory_index_lines",
        "memory_index_bytes",
        "hook_output_chars",
    )

    memory_index_lines: int
    memory_index_bytes: int
    hook_output_chars: int


@dataclass(frozen=True)
class CommitMessages:
    attribution_check: bool


@dataclass(frozen=True)
class Personal:
    """Machine-level parameters; never read from a repository."""

    preset: str


@dataclass(frozen=True)
class Config:
    drybag: Drybag
    project: Project
    paths: Paths
    memory: Memory
    budgets: Budgets
    native_caps: NativeCaps
    commit_messages: CommitMessages
    personal: Personal
