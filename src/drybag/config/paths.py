"""Root containment for the `[paths]` fields, and for nothing else yet.

`validate_paths` iterates `config.paths.as_dict()`, so the guard covers exactly the fields of
`schema.Paths` and no others. Two path-shaped, repository-writable fields never reach
`contained()`: `memory.groups` and `memory.index_extra`. A `drybag.toml` setting
`memory.index_extra = ["../../../../etc", "/etc/passwd"]` loads without a murmur, while the same
strings under `[paths]` are refused. Until that changes, `drybag.memory` must call `contained()`
itself on the fields it consumes; widening the guard here would change `Config`'s shape, so it
belongs with the module that first reads those fields.

Two rules, not one: `PATH_VALUE` is the grammar half — what a value must match before it may be
printed anywhere, a report included — and `contained()` is the root half, deciding whether the
value may be written *here*. The same two unguarded fields above are unguarded by both.

**The component rule is `fsops`', and this module borrows it rather than restating it.**
`contained()` used to split the value with `Path(relative).parts`, which normalises an empty
component, a trailing slash and a leading `./` out of existence; `fsops` splits the raw string
and refuses all three. So `plan()` found nothing wrong with `docs//roadmap-history.md` and
`apply()` raised on it after ten artifacts and the manifest were already written, leaving a
repository nothing would touch again. The two spellings agreed for four review rounds, which
is what a duplicated rule does until it does not.
`checked_components` is the single spelling now; the import goes subpackage-to-leaf, so `fsops`
stays the leaf the hook path depends on it being.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from drybag.config.machine import owner_home
from drybag.config.schema import Config
from drybag.errors import Failure, Refusal
from drybag.fsops import (
    REACHES_NO_FILE,
    UnsafePath,
    checked_components,
    names_component,
    names_control_directory,
    resolved,
    said,
)
from drybag.grammar import PATH_VALUE


class PathEscape(Refusal):
    """A configured path that leaves the project root or passes through a symlink, or one
    `contained()` cannot say either of (`PathUnasked`).

    `key` is the `[paths]` key `validate_paths` refused, `paths.<name>`, which the loader's NOTE
    path removes so the preset's value applies; `None` from any other caller of `contained()`.
    """

    def __init__(self, message: str, *, key: str | None = None) -> None:
        super().__init__(message)
        self.key = key


class PathUnasked(PathEscape):
    """A configured path with an ancestor no `lstat` could ask about, and no symlink above it.

    A `PathEscape`, so a caller that refuses an escape refuses this too: no path is contained
    until every ancestor has been asked. A caller that words the refusal its own way catches this
    first, because its words for an escape name a cause this path does not have. `fault` is the
    system's own words for what stopped the question, never the path.
    """

    def __init__(self, message: str, *, fault: str) -> None:
        super().__init__(message)
        self.fault = fault


def contained(
    root: Path,
    relative: str,
    *,
    allow_final_symlink: bool = False,
    resolved_root: Path | None = None,
) -> Path:
    try:
        # The empty path, an absolute path, `..`, `.` and an empty segment, all read off the
        # caller's own spelling — one rule, in `fsops`, so a value this function accepts is a
        # value the write can reach. `fsops` raises `UnsafePath`, an `OSError`, because a leaf
        # module owns no user-facing verdict; a configured path's verdict is a `Refusal`, and
        # the translation is all this line adds.
        parts = checked_components(relative)
    except UnsafePath as exc:
        raise PathEscape(str(exc)) from exc
    # Git's control directory is refused by the call above, at every depth and in any case, and
    # this is where that matters for a *configured* path: `contained()` is the function every
    # `[paths]` value and every caller-supplied path goes through, above the first write.
    # `validate_paths` asks the same question one key at a time so its refusal can name the key;
    # the rule itself is `fsops.names_control_directory` in both places, because a guard stated
    # twice is the defect this module was just repaired for.
    target = root.joinpath(*parts)
    # Asked from the root down, and no further than the first ancestor that reaches no file: every
    # path below one that reaches no file reaches none either, since its lookup fails at the same
    # component, so the walk ends where the answers stop telling it anything. It walked up from the
    # target over `[target, *target.parents]`, every one of them a fresh path built, compared with
    # the root and asked, so a value of twenty thousand components that reach nothing, forty
    # kilobytes, cost this function two and a half seconds; down from the root it asks one. An
    # ancestor that does exist is asked on the way down, and the walk still ends past the longest
    # path, where `lstat` answers `ENAMETOOLONG`. The answers are then judged from the deepest up,
    # as the walk up judged them.
    answers: list[tuple[Path, int | OSError]] = []
    ancestor = root
    for part in parts:
        ancestor = ancestor / part
        # Asked with `lstat` here and each fault given its answer, never left to
        # `Path.is_symlink()`, which raises on `ENAMETOOLONG` up to Python 3.13 and answers
        # `False` from 3.14: `attach` ended in an internal error on one interpreter and attached
        # on the other over the same overlay. A path that reaches no file holds no link to
        # follow. That includes one past the longest path, which only the descriptor walk every
        # write goes through reaches (`fsops.open_within`), and that walk refuses a link of its
        # own accord; a link above it whose target is too long is still asked, since `lstat`
        # does not follow the link it is asked about. Any other fault leaves the question
        # unanswered, which is a refusal: no path is contained until every ancestor is asked.
        try:
            answers.append((ancestor, os.lstat(ancestor).st_mode))
        except OSError as exc:
            if exc.errno in REACHES_NO_FILE:
                break
            answers.append((ancestor, exc))
    unasked: PathUnasked | None = None
    for ancestor, answer in reversed(answers):
        if isinstance(answer, OSError):
            # The deepest such ancestor is the one refused, and only once the walk has asked
            # every ancestor above it: a committed link into a directory nobody may search makes
            # every path through it unaskable, and the link is the cause to name. By its place
            # under the root and through `repr`, as the refusal below names its ancestor, and in
            # the fault's own words, never `str(exc)`, which carries the absolute path.
            if unasked is None:
                place = ancestor.relative_to(root).as_posix()
                unasked = PathUnasked(
                    f"{relative!r} cannot be checked for a symlink at {place!r} ({said(answer)})",
                    fault=said(answer),
                )
                unasked.__cause__ = answer
            continue
        if stat.S_ISLNK(answer) and not (allow_final_symlink and ancestor == target):
            # Both through `repr`, which escapes every line break and control character: in a
            # checkout the ancestor's name is the repository's, and a caller may show this refusal
            # to a terminal or a CI runner. Both by their place under the root: the ancestor's
            # absolute path said where this machine keeps the tree and nothing about the tree,
            # and a command relaying the refusal (a `left` line) printed that.
            # `ancestor` is below `root`, since the walk starts below it.
            named = ancestor.relative_to(root).as_posix()
            raise PathEscape(f"{relative!r} passes through a symlink at {named!r}")
    if unasked is not None:
        raise unasked
    # Defence in depth. The guards above refuse every escape a path string can express — the
    # empty path, an absolute path, any `..` component, and a symlink at any level between the
    # root and the target — so the comparison below is the net under them rather than the
    # guard itself. It is here for the path form nobody has anticipated; a caller validating
    # many paths against one root passes `resolved_root` so this resolve happens once. Through
    # `fsops.resolved`, so a loop at or above the root is an `OSError` naming the path on every
    # interpreter, where 3.11 and 3.12 raised `RuntimeError` past every caller's `except OSError`.
    # Compared by components, both paths resolved and so absolute and normal: `reached.parents`
    # built every parent of a deep target and compared each with the root, the quadratic cost the
    # walk above was rid of, which kept twenty thousand components at eight tenths of a second.
    if resolved_root is None:
        resolved_root = resolved(root)
    reached = resolved(target.parent) / target.name if allow_final_symlink else resolved(target)
    if reached.parts[: len(resolved_root.parts)] != resolved_root.parts:
        raise PathEscape(f"{relative!r} resolves outside the project root")
    return target


# drybag's own directory: `.drybag/local/` in a project (attach's ledger, the local-only note
# store), and the scaffold engine's manifest in an overlay. Spelled here, beside
# the loop that reserves it, because `config` imports no area and the scaffold engine, `attach`
# and `memory` all import `config`; `tests/config/test_paths.py` holds each of their paths to lie
# under it, so the two spellings cannot drift apart.
DRYBAG_DIRECTORY = ".drybag"


def names_drybag_directory(relative: str) -> bool:
    """Whether any component of `relative` is drybag's own directory, spelled in any case.

    `fsops.names_component`, the test `fsops.names_control_directory` asks too, and for the same
    two reasons. Any depth:
    a package inside a monorepo initialised on its own keeps its own `.drybag/local/`, and a
    parent project's `[paths]` value must not reach that either. Any case: the default
    filesystems on macOS and Windows fold case, so `.drybag/local/memory` is the same
    directory.
    """
    return names_component(relative, DRYBAG_DIRECTORY)


# A named cap: the longest `[paths]` value, in characters, which the grammar holds to ASCII and so
# to as many bytes. A value is joined below the project root, and 1024 is the longest path macOS
# opens by name (`PATH_MAX`; Linux takes 4096), so a longer one names nothing a person could have
# meant there, and no documentation path a project keeps comes near it. Every reader of a value
# pays for its length — the grammar, `contained()`'s walk and resolve, a refusal quoting it — and
# every `PreToolUse` hook reads every value, so a committed value of forty thousand characters cost
# each hook seconds before the walk stopped at the first component that is not there. Asked
# before the grammar, and the refusal names the key, never the value. No shipped file states it.
PATH_VALUE_LIMIT = 1024


# `PATH_VALUE` in words, for the refusal a person reads. Kept beside the one reader that prints
# it; a change to the grammar is a change to this sentence.
PATH_RULE = (
    "segments of letters, digits, `.`, `_` and `-` joined by single `/`, none of them `.` or "
    "`..`, the first not starting with `-`, and no `/` at either end"
)


def validate_paths(config: Config, root: Path) -> dict[str, Path]:
    """Every `[paths]` value contained in `root`, or a `PathEscape` keyed to the first that is
    not, so the loader's NOTE path can put that one key back to the preset's value."""
    return validate_path_values(config.paths.as_dict(), root)


def validate_path_values(values: Mapping[str, str], root: Path) -> dict[str, Path]:
    """`validate_paths` for `values`, `[paths]` values by their key's name: the loader's NOTE
    path asks it of a preset's value before it puts a key back to that value, so a value that
    does not hold in this repository either is named rather than reset to."""
    resolved_root = root.resolve()
    for name, relative in values.items():
        if len(relative) > PATH_VALUE_LIMIT:
            raise PathEscape(
                f"paths.{name} is longer than the {PATH_VALUE_LIMIT} characters a configured path "
                "may be",
                key=f"paths.{name}",
            )
        if not PATH_VALUE.match(relative):
            # Named and never quoted: this is the value a report would otherwise print.
            # The rule in words, not `PATH_VALUE.pattern`: the lookahead that closes `.` and `..`
            # as segments is correct and is no sentence a person can act on.
            raise PathEscape(
                f"paths.{name} is not a plain relative path: {PATH_RULE}", key=f"paths.{name}"
            )
        if names_control_directory(relative):
            # Named and never quoted, for the same reason. Nothing reserved git's control
            # directory: the grammar admits a leading dot, and `contained()` refused an
            # absolute path, `..` and a symlink but not a directory. So a clone could set
            # `agents_md = ".git/hooks/pre-commit"` — a `MANAGED_REGION`, exempt from the
            # engine's "exists and drybag did not write it" guard — and have its own
            # executable pre-commit hook rewritten in place, mode and all.
            #
            # The anchor is the project root the CLI resolved, and `.git` is git's own name
            # inside it; the clone authors this value and nothing else, so it cannot move the
            # directory being reserved. `.git` and not "a leading dot", because `.github/` is
            # an ordinary directory a project keeps its own files in.
            raise PathEscape(
                f"paths.{name} names git's control directory, which is git's and not "
                "drybag's to write into",
                key=f"paths.{name}",
            )
        if names_drybag_directory(relative):
            # Named and never quoted. `.drybag/` is where drybag keeps its manifest and, under
            # `.drybag/local/`, state git never sees: the local-only notes. A `[paths]` value is
            # committed, and while the scaffold inserted a `MANAGED_REGION` into whatever file
            # `agents_md` named, exempt from the "exists and drybag did not write it" guard, a
            # pulled commit setting `agents_md` to a file there had a scaffold refresh rewrite it
            # in a directory git cannot restore.
            #
            # The anchor is the project root the CLI resolved and `DRYBAG_DIRECTORY` above, a
            # constant in the installed package; the clone authors the value and nothing else.
            # No configured path belongs there: the preset puts none, and every file drybag
            # keeps in it is found by a constant, never through `[paths]`.
            raise PathEscape(
                f"paths.{name} names drybag's own directory {DRYBAG_DIRECTORY}, which holds "
                "its manifest and state git never sees and is not a place for a configured path",
                key=f"paths.{name}",
            )
    found: dict[str, Path] = {}
    for name, relative in values.items():
        try:
            found[name] = contained(
                root,
                relative,
                allow_final_symlink=(name == "memory"),
                resolved_root=resolved_root,
            )
        except PathEscape as exc:
            # `contained()` is every caller's and names no key; this caller knows which one.
            exc.key = f"paths.{name}"
            raise
    return found


# The machine's data directory under `HOME`, on every platform: the binding records
# (`drybag.binding`), and later the rendered delivery and the composed stores. No `XDG_*`
# variable is read, for the reason `config.machine` reads none for the machine configuration: an
# environment a repository can set would choose which records bind it.
DATA_DIRECTORY = Path(".local") / "share" / "drybag"
NO_HOME = (
    "HOME names no home directory, so this machine has no data directory to keep its binding "
    "records in; set HOME to your home directory"
)


def data_directory() -> Path:
    """`$HOME/.local/share/drybag`, or `Failure` where `HOME` names no home directory."""
    home = owner_home()
    if home is None:
        raise Failure(NO_HOME)
    return home / DATA_DIRECTORY
