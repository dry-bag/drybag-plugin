"""The binding id in a repository's git common directory, and the machine's record of what it binds.

**The id is the anchor, and a secret.** A checkout is bound when this machine wrote an id into its
git common directory, `<common>/drybag/binding`, and a record of what that id is bound to under
its own data directory, `<data>/bindings/<id>.toml`. Repository bytes cannot write the common
directory, because `.git/` is in no tree, and a clone never carries it, so a clone that commits
the `[project] name` of one of the owner's projects has no id and nothing on this machine binds
it. The id never leaves the machine: it is not written into the overlay, which is pushed and
shared, nor into any tracked file, and it is printed only as `short`, its first eight characters.
The rendered delivery's names carry it, so a clone cannot name what it never saw. The agent in a
session is not this anchor's adversary.

**The common directory, never a per-worktree path.** It is `git rev-parse --path-format=absolute
--git-common-dir`, asked through `gitenv`, which scrubs `GIT_DIR` and `GIT_WORK_TREE`: `git
rev-parse --git-path` maps only git's own names to the common directory and resolves any other
per worktree, so an id kept there would leave every linked worktree unbound. Every worktree of one
repository reads the one id.

**The project comes from the record, never from the repository.** `bound_project` reads no
`drybag.toml`. What an id is bound to — the project's name, the `origin` at attach time and the
checkout's path — is the machine's own file, under `config.paths.data_directory`, which follows
`HOME` as the machine configuration does, so two machines attaching one overlay project each keep
their own id and their own record.
"""

from __future__ import annotations

import re
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops, tomlout
from drybag.config.paths import data_directory
from drybag.config.reading import UNPARSEABLE
from drybag.errors import Failure
from drybag.gitenv import GitUnavailable, answer_lines, git_answer, git_run, without_userinfo
from drybag.printed import quoted

# Where the id lives under the git common directory, and where each record and each render lives
# under the machine's data directory.
ID_FILE = "drybag/binding"
# `attach`'s ledger of what it added to the checkout, kept beside the id for the same reason: a
# branch cannot write the common directory, so nothing a branch commits can name what `detach`
# or a re-attach takes back. `LEDGER_NAME` is how a message calls it.
LEDGER_FILE = "drybag/attach.json"
LEDGER_NAME = f"`{LEDGER_FILE}` in the git common directory"
BINDINGS = "bindings"
DELIVERY = "delivery"
# Exactly what `secrets.token_hex(16)` draws; anything else at `ID_FILE` is no id.
_ID = re.compile(r"[0-9a-f]{32}")
# How many characters of the id are ever printed.
SHORT = 8
# The longest id file or record read. A named cap (CONTRIBUTING.md#named-caps): both are this
# machine's own files and an id is 32 bytes and a record a few hundred, so 64 KiB is far past any
# file drybag wrote; no shipped file states it.
_READ_LIMIT = 64 * 1024

# Said where a write under the data directory fails: the directory, the id's short form and the
# system's words, never the path, which ends in the whole id (`fsops.said`).
NOT_WRITTEN = "{0} could not take the files of binding {1} ({2})"
CANNOT_TELL = (
    "cannot tell which git repository {root} belongs to, so there is no binding id to read or "
    "write there"
)


@dataclass(frozen=True)
class Bound:
    """What this machine records a checkout's id as bound to, and the id. `origin` is
    repository-authored, the remote as it was when the binding was recorded; a caller that shows
    it prints it through `printed`. `id` is the whole binding id, which is never printed
    (`short`): the composed store is named by it (`memory.store.composed_directory`)."""

    project: str
    origin: str | None
    checkout: Path
    id: str


def common_directory(root: Path) -> Path:
    """`root`'s git common directory, or `Failure` ("cannot tell") when git cannot name it —
    `GitUnavailable` where git could not be asked at all."""
    answer = git_answer(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    said = CANNOT_TELL.format(root=quoted(str(root)))
    if answer.cause is not None:
        raise GitUnavailable(f"{said}: {answer.cause.value}", answer.cause)
    if answer.value is None:
        raise Failure(said)
    return Path(answer.value)


def main_checkout_or_none(root: Path) -> Path | None:
    """This repository's main checkout, resolved, from the first record of `git worktree list
    --porcelain`, or `None` in a bare repository, whose first record is the repository itself.

    Through `gitenv.git_run`, which scrubs `GIT_DIR` and `GIT_WORK_TREE`: an inherited one would
    name the main checkout of a different repository altogether, and `attach` would then write
    into it. Asked above every write, since a `git` that cannot answer is knowable at the start.
    """
    code, out = git_run(root, "worktree", "list", "--porcelain")
    if code != 0:
        raise Failure(
            "`git` could not list this repository's worktrees, so its main checkout cannot be "
            "named; the fault is on this machine — check that `git` runs here"
        )
    # Lines split where git ended them: `splitlines()` also broke a path at a `\r` it holds
    # (`gitenv.answer_lines`).
    lines = answer_lines(out.split("\n\n")[0])
    if "bare" in lines:
        return None
    found = [line[len("worktree ") :] for line in lines if line.startswith("worktree ")]
    if not found:
        raise Failure("`git worktree list` named no main checkout for this repository")
    return Path(found[0]).resolve()


def read_id(root: Path, *, common: Path | None = None) -> str | None:
    """The binding id of `root`'s repository, or `None` when there is none, `root` is in no
    repository, or what is there is not exactly 32 lowercase hex characters. Raises
    `GitUnavailable` where git could not be asked: "could not ask" is never "no id". `common` is
    the git common directory where the caller already has it from a `rev-parse` of its own, which
    saves asking again."""
    try:
        common = common_directory(root) if common is None else common
    except GitUnavailable:
        raise
    except Failure:
        return None
    try:
        text = fsops.read_regular_text(common / ID_FILE, newline="", limit=_READ_LIMIT)
    except (OSError, UnicodeDecodeError):
        return None
    return text if _ID.fullmatch(text) else None


def ensure_id(root: Path) -> str:
    """The binding id of `root`'s repository, drawn and written through `fsops` where there is
    none yet."""
    found = read_id(root)
    if found is not None:
        return found
    made = secrets.token_hex(16)
    fsops.write_within(common_directory(root), ID_FILE, made)
    return made


def short(id: str) -> str:
    """The id as it may be printed: its first eight characters."""
    return id[:SHORT]


def _record_path(id: str) -> Path:
    return data_directory() / BINDINGS / f"{id}.toml"


def delivery_directory(id: str) -> Path:
    """`<data>/delivery/<id>/`, derived from the id and nothing else: the render `attach` writes on
    every run and `detach` removes, which the id and its record both outlive."""
    return data_directory() / DELIVERY / id


def bound_project(root: Path, *, common: Path | None = None) -> Bound | None:
    """What this machine records `root`'s id as bound to, or `None` when the checkout has no id or
    the machine no record for it. Never reads `drybag.toml`.

    A record that cannot be read, or holds no project, binds nothing: it is this machine's own
    file, written only by `record`, so `attach` writes it again. Nor does a `HOME` that names no
    home, under which no record can be kept; `attach` refuses that home before it writes one."""
    id = read_id(root, common=common)
    if id is None:
        return None
    try:
        raw = tomllib.loads(fsops.read_regular_text(_record_path(id), limit=_READ_LIMIT))
    except (Failure, OSError, UnicodeDecodeError, *UNPARSEABLE):
        return None
    project, origin, checkout = raw.get("project"), raw.get("origin"), raw.get("checkout")
    if not (isinstance(project, str) and project and isinstance(checkout, str) and checkout):
        return None
    return Bound(project, origin if isinstance(origin, str) else None, Path(checkout), id)


def record(root: Path, project: str, origin: str | None) -> Bound:
    """Bind `root`'s id, drawn here where there is none, to `project` on this machine.

    `origin` is kept where a UTF-8 file can hold it; one git printed in other bytes is recorded as
    none rather than refusing the binding, since the remote decides nothing."""
    id = ensure_id(root)
    # Without the userinfo a remote can carry a token in (`gitenv.without_userinfo`).
    origin = without_userinfo(origin) if origin is not None else None
    kept = origin if origin is not None and fsops.utf_8_name(origin) else None
    fields: dict[str, object] = {"project": project, "checkout": str(root)}
    if kept is not None:
        fields["origin"] = kept
    # `write_atomically`, the plain-path form: the data directory is this machine's, found from
    # `HOME` and never from a repository, and a link the owner put on the way to it is theirs.
    try:
        fsops.write_atomically(_record_path(id), tomlout.dumps({"": fields}))
    except OSError as exc:
        raise unwritten(data_directory() / BINDINGS, id, exc) from None
    return Bound(project, kept, root, id)


def unwritten(directory: Path, id: str, error: OSError) -> Failure:
    """`NOT_WRITTEN` for a write of `id`'s files under `directory` that raised `error`."""
    return Failure(NOT_WRITTEN.format(quoted(str(directory)), short(id), fsops.said(error)))
