"""The block `attach` keeps in the repository's `info/exclude`, and the questions it is built on.

`attach` puts machine-local files into the main checkout: the Codex rule copies under
`.codex/rules/` and `.claude/settings.local.json`. None of them is anything a collaborator should
see, and none was ignored, so every `git status` listed them and a `git add -A` committed the
owner's personal rules. `.gitignore` is the wrong place to
hide them: it is committed, so the hiding would itself be a change every collaborator gets, and
`attach` writes nothing there. `info/exclude` is the
repository's own exclude file, which no clone carries, and git resolves it in the common
directory, so one block covers the main checkout and every worktree.

**Only what the owner's own excludes do not hide already.** A path gets a line unless the two
files the owner holds, this repository's `info/exclude` and the global excludes file
(`core.excludesFile`), hide it on their own (`unhidden_by_owner`). A `.gitignore` in the checkout
is the repository's, and a pull can take the line out of it: counted as hiding the owner's links,
it left them out of the block and an upstream commit showed them, and the settings file, in
`git status` until the next attach. So a path both the owner's files and a `.gitignore` hide gets
no line, and a path only a `.gitignore` hides gets one. A checkout whose own excludes keep the
whole footprint out of git gets no block.

**And once the block stands, git is asked whether each path is ignored** (`unignored`, from
`write`), over the checkout as it is, every source counted: a `.gitignore` that re-includes a
path with a `!` line outranks the block, so that path would show however the block reads, and
`attach` refuses before writing it (`NOT_IGNORED`). Asked with `--no-index`, so a pattern decides
and the index does not: a tracked path is refused on its own, before any write (`tracked`).

Security ruling, in the order the template asks for it. **Preconditions**: every candidate path is
computed by drybag — `paths.memory` and each `memory.groups` entry are repository-authored, but
each candidate is held inside the project by `config.paths.contained` before it is asked about,
and a name no exclude line can hold as one line gets no line, so the check after the block refuses
the run unless the owner's own excludes hide it, rather than its being hidden by a line the
repository wrote: a line break, a NUL (git's reader ends the line there, so
the pattern would be a shorter path's), and every other character `str.splitlines` breaks at (a
reader that splits there would take one line for several). Of the characters left, git's
pattern syntax gives a meaning to `\\`, `*`, `?`, `[` and a space (a trailing one is dropped), and
each is escaped, so a group named `*` hides that one link and not the directory. The block is read
back the way git reads it, at `\\n` alone. **Anchor**: git's own ignore evaluation, the only
authority on what an exclude line hides, asked of this repository's git directory over an empty
work tree, where git reads `info/exclude` and `core.excludesFile` and no `.gitignore`, so only the
two files the owner holds stand in for a line of the block and a `.gitignore`, which the
repository authors, never does. **Write target**: `info/exclude` as
`git rev-parse --git-path` names it — `gitenv.git_path`, the resolver `setup --git-hooks` uses —
refused when it is a symlink, as `guards.githooks.install` refuses a symlinked hook, and when this
user cannot write the directory that holds it, both while the run is planned, and written with
`fsops.write_atomically`, whose rename replaces the name rather than writing through it.
**Who must not be refused**: a checkout whose own `info/exclude` or global excludes file already
hides everything, where `attach` changes nothing and so never needs the file at all, whatever
`.gitignore` lines in it hide the same paths. A `.gitignore` line that re-includes one, and a
settings file the repository tracks, are refused: each puts a file of the owner's where git shows
it.
"""

from __future__ import annotations

import contextlib
import os
import posixpath
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from drybag import fsops
from drybag.config.paths import contained
from drybag.errors import Refusal
from drybag.gitenv import git_path, git_run
from drybag.printed import quoted
from drybag.scaffold import RegionError, drop, extract, upsert

# What `git rev-parse --git-path` is asked for, and the region's name in it: the block reads
# `# drybag:attach:begin` ... `# drybag:attach:end`.
EXCLUDE = "info/exclude"
EXCLUDE_REGION = "attach"
EXCLUDE_NOTE = "# drybag attach: this machine's rules and settings, not a collaborator's."
# Two facts about the file around the block, recorded inside the block because the file is shared
# by every checkout while each ledger is one checkout's: whichever checkout's `detach` takes the
# block last reads them there and gives the file back byte for byte. `EXCLUDE_CREATED`: there was
# no file before the block, so a file the block leaves holding nothing is removed. `EXCLUDE_ENDED`:
# the owner's last line had no line ending and the block had to start on a line of its own, so the
# one it added is taken back when nothing follows the block.
EXCLUDE_CREATED = "# drybag attach: this file was created for this block."
EXCLUDE_ENDED = "# drybag attach: the line before this block had no line ending until attach."
# The third, for a repository made without git's templates, which has no `info/` directory until
# the block's write makes one: the detach that removes the file takes the directory back too.
EXCLUDE_DIRECTORY_CREATED = "# drybag attach: this file's directory was created for this block."
# The fourth: the owner's last line ended in a lone `\r`, which `attach` ended with `\n` alone.
# The bytes `X\r` + `\n` and `X` + `\r\n` are the same, so `detach` cannot tell which ending it
# added from the file, and reads it here.
EXCLUDE_ENDED_AFTER_CR = (
    "# drybag attach: the line before this block ended in a lone carriage return until attach."
)
_RECORDS = (EXCLUDE_CREATED, EXCLUDE_ENDED, EXCLUDE_DIRECTORY_CREATED, EXCLUDE_ENDED_AFTER_CR)
# The characters git's pattern syntax gives a meaning to inside a path, each escaped with a
# backslash so the line matches the one path it was written for. A space is escaped too: a
# trailing one is dropped by git unless it is.
_SPECIAL = frozenset("\\*?[ ")
UNANSWERED = (
    "git could not say whether the files `attach` places are tracked or ignored, so nothing past "
    "the exclude block was written; run it again once `git ls-files` and `git check-ignore` work "
    "in this repository"
)
# The path is git's answer about this checkout's own git directory, and it prints through
# `printed.quoted`: the checkout's directory name usually comes from the clone URL.
LINKED = (
    "{path} is a symlink, and `attach` writes the repository's exclude file only as a real file; "
    "nothing was written. Replace the link with the file it points at and run it again"
)
UNREADABLE = "{path} cannot be read ({reason}), so nothing was written"
# Asked of the directory the file is replaced in while the run is planned, so the common case (a
# `.git/info` this user cannot write) refuses before anything is written.
UNWRITABLE = (
    "{path} cannot be written, because this user cannot write the directory that holds it, so "
    "nothing was written"
)
# The write itself failing, which no check made while planning can rule out: by then `detach` may
# have withdrawn what comes before the block.
NOT_WRITTEN = "{path} could not be written ({reason})"
# Said when the index tracks a path `attach` places, or the file the path opens: no exclude line
# hides a tracked file. `{paths}` is what `write` names them by.
TRACKED = (
    "this repository tracks {paths}, which `attach` places for this machine alone, so nothing was "
    "written; attach a checkout where none of them is tracked"
)
# Said when git would still show a path `attach` places once the block is written: a `.gitignore`
# re-includes it with a `!` line, which outranks the exclude file, or its name is one no line of
# the block can spell (`pattern`). `{paths}` is what `write` names them by.
NOT_IGNORED = (
    "git would not ignore {paths}, which `attach` places for this machine alone, so nothing past "
    "the exclude block was written: a `.gitignore` line starting with `!` that re-includes a path "
    "outranks that block, and a memory group whose name holds a line break gets no line in it; "
    "take that line out of the `.gitignore`, or rename the group, and run `drybag attach` again"
)


@dataclass(frozen=True)
class ExcludeWrite:
    """The whole new text of the exclude file, computed before the first write; `None` when the
    file is to be removed, which only a file created for the block and left holding nothing is.

    The text is the file's bytes decoded with `surrogateescape`, so bytes that are not UTF-8 —
    the owner's own file, which git reads as bytes — go back out exactly as they came in.
    """

    path: Path
    text: str | None
    # With `text` `None`: the directory holding the file was created for the block as well, so it
    # goes too when the file's removal leaves it empty.
    directory_created: bool = False


def unignored(root: Path, relatives: Sequence[str]) -> tuple[str, ...]:
    """The members of `relatives` no ignore pattern hides, every source read, in their given order.

    `--no-index`: the question is whether a pattern hides the path, which a `.gitignore`'s `!`
    line decides, and not whether the index tracks it. `--stdin -z`: the paths go in
    NUL-separated and never as arguments, so none is read as an option, and come back unquoted,
    so the answer compares with what was asked. Exit 1 is "none matched", an answer; anything but
    0 and 1 is no answer, and a refusal, because treating it as "nothing is ignored" would refuse
    a checkout git might hide, and treating it as "everything is" would write where git shows.
    """
    return _unmatched(root, relatives, "check-ignore", "--no-index", "--stdin", "-z")


def tracked(root: Path, relatives: Sequence[str]) -> tuple[str, ...]:
    """The members of `relatives` git treats as tracked: the index holds the path under git's own
    view of case (`icase`, which decides whether git takes a file written there for a tracked one,
    there or not), or the path is there and the same file (`st_dev`, `st_ino`) as one the index
    holds directly in a placed path's directory.

    The second half asks the filesystem what a name opens rather than emulating how it folds
    names past git's ASCII folding: APFS folds U+017F (a long s) to `s`. A directory on the way
    that only the filesystem folds to the placed spelling is beyond both halves. One `ls-files`,
    `literal` so a group named `*` is that one path; a path the index holds files below is not
    tracked, a group moved into the overlay before committing. Exit other than 0 is a refusal."""
    if not relatives:
        return ()
    parents = {posixpath.dirname(relative) for relative in relatives}
    asked = [*relatives, *(f"{parent}/" if parent else "." for parent in parents)]
    code, out = git_run(root, "ls-files", "-z", "--", *(f":(icase,literal){x}" for x in asked))
    if code != 0:
        raise Refusal(UNANSWERED)
    names = [name for name in out.split("\0") if name]
    near = {_folded(parent) for parent in parents}
    files = {_identity(root / name) for name in names if _folded(posixpath.dirname(name)) in near}
    listed = {_folded(name) for name in names}
    return tuple(
        relative
        for relative in relatives
        if _folded(relative) in listed or _identity(root / relative) in files - {None}
    )


def _folded(name: str) -> bytes:
    """`name` as git's `icase` compares it: ASCII letters folded, every other byte as it is."""
    return name.encode("utf-8", "surrogateescape").lower()


def _identity(path: Path) -> tuple[int, int] | None:
    """The file at `path` itself, a link not followed, as `(st_dev, st_ino)`; `None` where the path
    reaches no file, or none this user may reach: what cannot be reached cannot be written, and the
    containment asked after this refuses that path in its own words."""
    try:
        status = os.lstat(path)
    except OSError:
        return None
    return status.st_dev, status.st_ino


def _unmatched(root: Path, relatives: Sequence[str], *asked: str) -> tuple[str, ...]:
    """The members of `relatives` the `check-ignore --stdin -z` in `asked` does not answer."""
    if not relatives:
        return ()
    code, out = git_run(root, *asked, stdin="\0".join(relatives))
    if code == 1:
        return tuple(relatives)
    if code != 0:
        raise Refusal(UNANSWERED)
    hidden = {name for name in out.split("\0") if name}
    return tuple(relative for relative in relatives if relative not in hidden)


def unhidden_by_owner(root: Path, relatives: Sequence[str]) -> tuple[str, ...]:
    """The members of `relatives` the owner's own two files, read alone, do not hide, in their
    given order: the ones the block must hold.

    The question is `check-ignore` asked of this repository's git directory with an empty
    temporary directory as the work tree, so git finds no `.gitignore` to read and answers from
    `info/exclude` and the global excludes file only, applying their negations itself. Each part
    of it is what keeps a `.gitignore` out: `--work-tree`, because with `--git-dir` alone the
    current directory, the checkout, is the work tree again; `--no-index`, because git reads a
    skip-worktree `.gitignore`, which a sparse checkout leaves out of the work tree, from the
    index when the file is not on disk; and both as options, since `git_run` drops `GIT_DIR` and
    `GIT_WORK_TREE` from the environment. A path that only a `.gitignore` hides gets a line, since
    a pull can take that line out, and a path the owner's files hide gets none, whatever the
    `.gitignore` says. What git shows in spite of the owner's files, a tracked file or one a
    `.gitignore` re-includes, is not asked about: no exclude line hides either, and a line for
    one would need the exclude file written, which is refused when it is a symlink.
    """
    if not relatives:
        return ()
    code, answer = git_run(root, "rev-parse", "--absolute-git-dir")
    if code != 0:
        raise Refusal(UNANSWERED)
    git_dir = answer.removesuffix("\n")
    around = (*_excludes_file(root), f"--git-dir={git_dir}")
    with tempfile.TemporaryDirectory() as empty:
        asked = (*around, f"--work-tree={empty}", "check-ignore", "--no-index", "--stdin", "-z")
        unheld = set(_unmatched(root, relatives, *asked))
    return tuple(relative for relative in relatives if relative in unheld)


def _excludes_file(root: Path) -> tuple[str, ...]:
    """`-c core.excludesFile=<path>` when the owner's setting is a relative path, else nothing.

    git opens a relative `core.excludesFile` from the top of the work tree, which for the owner's
    question is the empty directory, so there it would name no file and every path it hides would
    get a line. Made absolute against the checkout's top, it names the file git reads for the
    checkout. Exit 1 is "not set", where git reads `~/.config/git/ignore`, an absolute path.
    """
    code, answer = git_run(root, "config", "--type=path", "--get", "core.excludesFile")
    if code == 1:
        return ()
    if code != 0:
        raise Refusal(UNANSWERED)
    setting = answer.removesuffix("\n")
    if not setting or Path(setting).is_absolute():
        return ()
    return ("-c", f"core.excludesFile={root / setting}")


def pattern(relative: str) -> str | None:
    """The one exclude line matching exactly `relative` from the checkout's top, or `None`.

    Anchored with a leading `/`, which also means no line starts with `!` or `#`. `None` for a
    path no single line can hold: a line break, a NUL, at which git's reader ends the line, or
    any other character `str.splitlines` breaks at, which a reader splitting there would take
    for the end of a line and the start of another. That path is left visible.
    """
    if relative.splitlines() != [relative] or "\0" in relative:
        return None
    return "/" + "".join(f"\\{char}" if char in _SPECIAL else char for char in relative)


def _read(path: Path) -> str | None:
    """The exclude file's bytes as text that encodes back to them exactly, or `None` when there is
    no file. Never a refusal for its content: git reads the file as bytes, so a byte that is not
    UTF-8 is the owner's and is kept, not a reason to stop `attach` or `detach`."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refusal(UNREADABLE.format(path=quoted(str(path)), reason=fsops.said(exc))) from exc
    return raw.decode("utf-8", "surrogateescape")


def _patterns(body: str | None) -> list[str]:
    """The pattern lines of an earlier block, split where git splits them: at `\\n` alone, with
    the one `\\r` before it dropped, as git drops it.

    Never `str.splitlines`, which also breaks at U+2028, `\\x85`, a form feed and five more: a
    line an earlier drybag wrote with one of those in it came back as several, and a `!.env`
    among them un-hid a file the owner's own excludes hide (`gitenv.answer_lines` gives the same
    rule for git's answers).
    """
    return [line for line in _lines(body) if line and not line.startswith("#")]


def _lines(body: str | None) -> list[str]:
    return [line.removesuffix("\r") for line in (body or "").split("\n")]


def planned_block(root: Path, relatives: Sequence[str]) -> ExcludeWrite | None:
    """The exclude file with this attach's block, or `None` when nothing needs hiding.

    Asked above every write: containment, `check-ignore`, the resolution of the file and its
    symlink refusal all raise before `attach` has touched anything. The block is the union of
    what an earlier attach put there and what this one still finds visible, because a path an
    earlier block hides reads as ignored now, and a block rebuilt from this run alone would drop
    it and show it again.
    """
    for relative in relatives:
        # `allow_final_symlink`: a rule copy left as a link is replaced, never followed.
        contained(root, relative, allow_final_symlink=True)
    lines = [line for line in map(pattern, unhidden_by_owner(root, relatives)) if line is not None]
    if not lines:
        return None
    path = git_path(root, EXCLUDE, "exclude file")
    if fsops.is_symlink(path):
        raise Refusal(LINKED.format(path=quoted(str(path))))
    read = _read(path)
    current = read or ""
    earlier = _named(path, extract, current)
    if earlier is None:
        records = [
            *((EXCLUDE_CREATED,) if read is None else ()),
            *((EXCLUDE_DIRECTORY_CREATED,) if not fsops.is_dir(path.parent) else ()),
            # git's line end, `\n` alone: a last line ending in a lone `\r` is not ended.
            *((EXCLUDE_ENDED,) if current and not current.endswith(("\n", "\r")) else ()),
            *((EXCLUDE_ENDED_AFTER_CR,) if current.endswith("\r") else ()),
        ]
    else:
        records = [record for record in _RECORDS if record in _lines(earlier)]
    kept = _patterns(earlier)
    new = (line for line in lines if line not in kept)
    body = "\n".join([EXCLUDE_NOTE, *records, *kept, *new])
    updated = upsert(current, EXCLUDE_REGION, body)
    if updated == current:
        return None
    if not fsops.creatable(path):
        raise Refusal(UNWRITABLE.format(path=quoted(str(path))))
    return ExcludeWrite(path, updated)


def _named(
    path: Path,
    region: Callable[[str, str], str | None],
    text: str,
) -> str | None:
    """`extract` or `drop` of the block, with the file named in a refusal: "region 'attach' is
    opened or closed twice" said which region and never which file, and `.git/info/exclude` is
    not one a person opens often."""
    try:
        return region(text, EXCLUDE_REGION)
    except RegionError as exc:
        raise RegionError(f"{quoted(str(path))}: {exc}") from exc


def withdrawn_block(root: Path) -> ExcludeWrite | None:
    """The exclude file with this attach's block taken out, or `None` when there is none.

    Asked above `detach`'s first withdrawal, as `_checkouts` is: a block opened twice is a
    `RegionError` knowable at the start, and a directory this user cannot write the file into is
    `UNWRITABLE`, as it is for `attach`. A symlinked file is left alone
    rather than refused, because `attach` never writes through one, so a block behind a link is
    not one this command put there.

    Byte for byte, from what the block records (`EXCLUDE_CREATED`, `EXCLUDE_ENDED`,
    `EXCLUDE_ENDED_AFTER_CR`, `EXCLUDE_DIRECTORY_CREATED`): a file
    created for the block and left holding nothing is removed, and a line ending `attach` added
    before the block is taken back when nothing follows it. With the owner's own line after the
    block, taking it back would join two lines, so it stays.
    """
    path = git_path(root, EXCLUDE, "exclude file")
    if fsops.is_symlink(path) or not fsops.is_file(path):
        return None
    current = _read(path) or ""
    body = _named(path, extract, current)
    if body is None:
        return None
    # Asked here, above `detach`'s first withdrawal, as `planned_block` asks it above `attach`'s
    # first write: the block's withdrawal replaces or removes the file in its directory, and found
    # out by doing it, after the settings and the rule copies, the run stopped half
    # detached with the ledger still on disk.
    if not fsops.creatable(path):
        raise Refusal(UNWRITABLE.format(path=quoted(str(path))))
    records = _lines(body)
    remaining = drop(current, EXCLUDE_REGION)
    # Nothing follows the block exactly when what is left is what came before it.
    if EXCLUDE_ENDED in records and current.startswith(remaining):
        remaining = remaining.removesuffix(_added_ending(remaining))
    if EXCLUDE_ENDED_AFTER_CR in records and current.startswith(remaining):
        remaining = remaining.removesuffix("\n")
    if EXCLUDE_CREATED in records and not remaining:
        return ExcludeWrite(path, None, EXCLUDE_DIRECTORY_CREATED in records)
    return ExcludeWrite(path, remaining)


def _added_ending(text: str) -> str:
    """The line ending `attach` added at the end of `text`, the owner's file with it: `upsert`
    ends a line with `\r\n` in a file that already holds one, and with `\n` otherwise. The line
    it ended did not end in `\r` (that case is `EXCLUDE_ENDED_AFTER_CR`'s), so a `\r\n` at the
    end is the whole ending it added."""
    return "\r\n" if text.endswith("\r\n") else "\n" if text.endswith("\n") else ""


def write(planned: ExcludeWrite) -> None:
    """Replace the exclude file in one rename, or remove it; the path is git's answer, not a
    configured one. A write that fails is a refusal naming the file, never a bare `OSError`."""
    try:
        if planned.text is None:
            fsops.remove_within(planned.path.parent, planned.path.name)
            if planned.directory_created:
                # `rmdir`, so a directory anything else has written into since stays.
                with contextlib.suppress(OSError):
                    fsops.rmdir_within(planned.path.parent.parent, planned.path.parent.name)
            return
        fsops.write_atomically(planned.path, planned.text, errors="surrogateescape")
    except OSError as exc:
        raise Refusal(
            NOT_WRITTEN.format(path=quoted(str(planned.path)), reason=fsops.said(exc))
        ) from exc
