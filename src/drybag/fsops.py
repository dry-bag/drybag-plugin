"""One writer for every file drybag replaces in place, and one way to reach it safely.

Two problems, one module.

*Atomicity, and durability.* A note, an index and a scaffolded artifact are each a file a
person may be editing, and a bare `write_text` truncates before it writes: a crash in between
leaves an empty file where the only copy of a note was. `mkstemp` plus `os.replace` makes the
replacement atomic, and the mode of the replaced file is carried over, because a fresh
temporary is 0600 and silently tightening `.gitignore`, `AGENTS.md` or a workflow file is a
defect of its own. Atomicity is not durability, and the crash argument above asks for both:
`os.replace` guarantees that a reader sees the old file or the new one and never a half-written
one, and guarantees nothing about what survives power loss. So the bytes are `fsync`ed before
the rename and the directory is `fsync`ed after it — without the first, the rename can be
durable while the data is not, which is the zero-length note this module exists to prevent.

*Containment that survives the write.* Validating a path string and then writing to it leaves
a window in which a component can become a symlink, and the clone may be running a process of
its own. `open_within` walks the path one component at a time with `O_NOFOLLOW`, so a symlink
anywhere along it fails the open rather than redirecting it, and returns a directory
descriptor the write then happens relative to. The string is never resolved again.

**`open_within` contains, and the name is the contract.** The walk refuses symlinks; for a
while it refused nothing else, so `..` walked out of the root one component at a time and an
absolute `relative` restarted the walk at `/` — `openat` ignores its `dir_fd` for an absolute
path. On Linux, where `/etc` and `/var` are real directories, that completed and yielded a
descriptor outside the root; macOS refused it only incidentally, because those two happen to
be symlinks there. No caller reached it — `scaffold.engine` calls `contained()` first — but
the seam is the point: every area that puts a file into a repository calls it instead of writing
files of its own, and the name of this function is what those callers read as the guarantee. It
is the guarantee now.

`contained()` in `config.paths` is still the right first call for a *configured* string: it
answers about the project root, reports a `Refusal` a user can act on, and catches a committed
symlink before any descriptor is opened. This is the floor under it, not a replacement for it.

A leaf module: it imports nothing from `drybag`, so the hook path pays no area import to
reach it. That is also why the write helpers below raise `OSError` and `UnsafePath` rather
than `drybag.errors.Refusal` — the area that owns a user-facing verdict translates them.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

# The mode a file drybag creates asks for. It is a request, not a decision: the temporary is
# created with it and the kernel subtracts the process umask, exactly as `open()` does for any
# other program. Forced with `fchmod`, it would write a world-readable `trust.json` into
# `~/.config` under `umask 077`, overriding a choice the machine owner made deliberately. A file
# that already exists keeps its own mode instead; see `_mode_of`.
NEW_FILE_MODE = 0o644
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_PARENT = ".."
_HERE = "."
# Git's control directory, reserved at every depth and in any case.
#
# **Where the anchor comes from.** The rule is "no component of a path *relative to a root* may
# be git's control directory", and the root is the checkout a person handed the CLI — resolved
# by `project.detect`, or passed on the command line. `.git` is git's own name inside that
# root. So the party being contained is the clone, which authors the path string and can author
# nothing else here: it cannot move `.git`, cannot choose the root, and cannot rename the
# directory out from under this check.
#
# **Case-insensitively**, because the default filesystem on macOS is case-insensitive and
# `.GIT/hooks/pre-commit` reaches the same file there; **at every depth**, because a `.git`
# below the top is a submodule's control directory and is exactly as off-limits.
#
# **`.git` and not "a leading dot".** `.drybag/` is drybag's own directory, its local state
# and the scaffold engine's manifest, so a leading-dot rule would refuse drybag's own files.
# `.gitignore`, `.gitattributes` and `.gitkeep` are ordinary files and are untouched by an
# equality test on the whole component.
#
# The one place drybag does write inside `.git` is `guards.githooks.install`, and it does not
# come through here: git itself names the directory (`rev-parse --git-path hooks`), the result
# is an absolute path this process computed, and the write is `write_atomically`, the plain-path
# form for a caller that already holds a trusted path. No repository-authored string reaches it.
CONTROL_DIRECTORY = ".git"


class UnsafePath(OSError):
    """A component of the path is a symlink, is not a directory, or leaves the root."""


def said(error: OSError) -> str:
    """What an `OSError` says about a file without naming it: `str(error)` carries the path as it
    was opened — the absolute one, under this machine's own layout — so a report that names the
    file relative to the root says the error beside it as its `strerror`, or its class's name.

    `UnsafePath` alone is said whole, decided by its type: it is drybag's own refusal, raised
    with a message and no errno, and the message names the path relative to the root. Any other
    error's message may carry a path, whether or not it has a file name."""
    if isinstance(error, UnsafePath):
        return str(error)
    return error.strerror or type(error).__name__


def unreadable(label: object, error: OSError | UnicodeDecodeError) -> str:
    """What a reader says of a file it could not read, named as `label`: that it cannot be read,
    and why in `said`'s words, or that it is not UTF-8 text. The one wording, so a file named by the
    project and never by the path it was opened by is named so in every refusal of it."""
    if isinstance(error, UnicodeDecodeError):
        return f"{label} is not UTF-8 text"
    return f"{label} cannot be read ({said(error)})"


@contextmanager
def reading(label: object, refusal: Callable[[str], Exception]) -> Iterator[None]:
    """Raise `refusal` with `unreadable`'s sentence for a read inside it that fails, an `OSError`
    chained to it and a decoding error not: the frame every reader of a file that answers in a
    refusal of its own puts around its read. An arm that answers a missing file otherwise goes
    inside it, and re-raises what it does not answer."""
    try:
        yield
    except OSError as exc:
        raise refusal(unreadable(label, exc)) from exc
    except UnicodeDecodeError as exc:
        raise refusal(unreadable(label, exc)) from None


def names_component(relative: str, name: str) -> bool:
    """Whether any component of `relative` is `name`, spelled in any case.

    The one test behind `names_control_directory` and `config.paths.names_drybag_directory`:
    at any depth, and in any case, because the default filesystems on macOS and Windows fold
    case and `.GIT` or `.drybag` reaches the same directory there. `name` is lower-case.
    """
    return any(part.lower() == name for part in relative.split("/"))


def names_control_directory(relative: str) -> bool:
    """Whether any component of `relative` is git's control directory, spelled in any case.

    Public and separate from the walk, because two callers need the same answer and must not
    each write their own version of it: `checked_components` refuses on it, and
    `config.paths.validate_paths` asks it a key at a time so its refusal can name the key
    without printing the repository-authored value. See `CONTROL_DIRECTORY` for the rule and
    for where its anchor comes from.
    """
    return names_component(relative, CONTROL_DIRECTORY)


def utf_8_name(name: str) -> bool:
    """Whether a name the filesystem gave is UTF-8 on disk, so a UTF-8 file can hold it.

    A name held in other bytes — latin-1, on Linux — reaches Python with surrogate escapes, and
    writing it into a UTF-8 file raises `UnicodeEncodeError`, which would end `docs trail`,
    `memory index` or `attach` as an internal error. The bytes are asked and not the `str`,
    because under a latin-1 filesystem codec every byte decodes and the escapes that would show
    it never appear.
    """
    try:
        os.fsencode(name).decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


# The faults of a `stat` that say its path reaches no file: nothing there, a component that is not
# a directory, a symbolic link loop, and a name or a whole path longer than the system takes. The
# kernel refuses the last before it looks anything up, so it gets the answer a dangling link and a
# loop get: nothing a reader that names the path whole can open is there. A clone reaches it with a
# committed link whose target holds a name longer than a file name may be, which makes every path
# through the link one, and a repository-chosen string joined into a path reaches it too.
REACHES_NO_FILE = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.ENAMETOOLONG})


def _reached_mode(path: Path, *, follow: bool) -> int | None:
    """The mode `stat` (or `lstat`, when not `follow`) finds at `path`, or `None` when the path
    reaches no file: one of `REACHES_NO_FILE`, or a NUL, which names no file anywhere. Any other
    fault is raised: a path that cannot be asked about is the caller's to classify."""
    try:
        return (os.stat(path) if follow else os.lstat(path)).st_mode
    except OSError as exc:
        if exc.errno in REACHES_NO_FILE:
            return None
        raise
    except ValueError:
        return None


# `Path.is_file()`, `is_dir()`, `exists()` and `is_symlink()`, answered alike on every interpreter.
# `pathlib`'s own answer `False` for a path that names no file and raise any other fault up to
# Python 3.13, and answer `False` for every fault from 3.14 (`os.path`'s rule), so one path a clone
# can shape was an internal error on one interpreter and a quiet "nothing there" on the other, and
# the suite proved only the interpreter it ran on. These choose: `REACHES_NO_FILE` is nothing there,
# as it was on 3.14, and any other fault -- a directory that cannot be searched, an I/O error --
# raises, as it did up to 3.13, because "cannot tell" is not "no". Each follows links as its
# `pathlib` namesake does: `is_symlink` asks `lstat`, the other three `stat`.


def is_file(path: Path) -> bool:
    """Whether `path`, followed through links, reaches a regular file; see above."""
    mode = _reached_mode(path, follow=True)
    return mode is not None and stat.S_ISREG(mode)


def is_dir(path: Path) -> bool:
    """Whether `path`, followed through links, reaches a directory; see above."""
    mode = _reached_mode(path, follow=True)
    return mode is not None and stat.S_ISDIR(mode)


def exists(path: Path) -> bool:
    """Whether `path`, followed through links, reaches anything; see above."""
    return _reached_mode(path, follow=True) is not None


def is_symlink(path: Path) -> bool:
    """Whether `path` itself, not followed, is a symbolic link; see above."""
    mode = _reached_mode(path, follow=False)
    return mode is not None and stat.S_ISLNK(mode)


def vacant(path: Path) -> bool:
    """Whether nothing is at `path`, or an empty directory that is no link: what a clone or a
    render may go into. A link, dangling or not, leads either wherever it points, so it is never
    vacant. Whether this user may write there is the caller's to ask; a directory that cannot be
    listed raises, as every predicate here does for a fault that leaves the question open."""
    if is_symlink(path):
        return False
    if not is_dir(path):
        return not exists(path)
    with os.scandir(path) as entries:
        return next(entries, None) is None


def linked_directory(path: Path) -> Path | None:
    """The directory a symbolic link at `path` leads its file into, or `None` where `path` is not
    one or that cannot be told: what a failed write through the link names, since the write went
    there and not beside the link."""
    try:
        return resolved(path).parent if is_symlink(path) else None
    except OSError:
        return None


def creation_directory(path: Path) -> Path:
    """The directory `path` would be made in: the one it lives in, or, while that is not there
    yet, the nearest one above it that is, where `mkdir(parents=True)` would start. What
    `creatable` asks about, and so what a refusal on its answer names."""
    directory = path.parent
    while not exists(directory) and directory != directory.parent:
        directory = directory.parent
    return directory


def creatable(path: Path) -> bool:
    """Whether this user can create or replace `path`: `write_atomically` renames a new file in
    beside it, and `mkdir` makes it, so `creation_directory(path)` must be writable and
    searchable.

    `os.access` answers for the real uid, so under root it says yes whatever the mode: a caller
    that refuses on this answer keeps a floor under the write itself for the case it cannot see.
    """
    return os.access(creation_directory(path), os.W_OK | os.X_OK)


def _meets_a_loop(path: Path) -> None:
    """Raise the system's own `ELOOP` for `path`, naming it as given, when following it meets a
    symbolic-link loop; return otherwise, whatever else `stat` says."""
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise


def resolved(path: Path) -> Path:
    """`path.resolve()`, with a symbolic-link loop answered alike on every interpreter: the
    `OSError` (`ELOOP`) `stat` raises for `path`, naming it as given, which every caller already
    answers as a path it cannot ask about.

    Python 3.11 and 3.12 raise `RuntimeError` for a loop met while resolving, which no
    `except OSError` catches, so a loop a clone commits, or one at a path the command line names,
    ended in `internal error` there; 3.13 and later answer a path with the loop left in it, so the
    same input was a quiet answer on one interpreter and a traceback on the other. Both are asked
    of `stat`, which meets the loop again and raises; a path that is not there yet, which this is
    asked for as `resolve` is, raises something else and is answered as before. A `RuntimeError`
    `stat` cannot confirm — a link changed between the two questions — is still a loop `resolve`
    met, and is the same `ELOOP`, in words of its own. No caller asks for `strict`, whose loop
    every interpreter already raises as an `OSError`.
    """
    try:
        answer = path.resolve()
    except RuntimeError as exc:
        _meets_a_loop(path)
        raise OSError(errno.ELOOP, "a symbolic link loop", str(path)) from exc
    _meets_a_loop(path)
    return answer


class NotRegularFile(OSError):
    """A path a reader was handed names something other than a regular file. An `OSError`, so
    every reader's existing "could not be read" arm is its answer without a branch of its own."""


class TooLarge(OSError):
    """A regular file longer than `REGULAR_READ_LIMIT`, refused unread past the limit. An `OSError`
    for the reason `NotRegularFile` is one."""


# A named cap: the most bytes `read_regular_bytes` and `read_regular_text` read of one file. A
# regular file can be endless: on Linux `/proc/self/pagemap` is `S_ISREG` with a size of 0 and
# reads on for as long as anyone asks, and a committed link to it passes every check above the
# read. So the read asks for one byte past the cap, and a file that has it is refused as too
# large. Far above any file these readers take -- a note, `MEMORY.md` (whose
# own budget, `memory_index_bytes`, is 25,600), a plan, a settings file, `.gitignore`, each a few
# kilobytes -- and far under what would exhaust a machine's memory. A file drybag writes
# itself is held under it too: a JSON document written back indented grows by its depth on every
# line, so `jsonobject.json_text` refuses a write-back past the cap before anything is written,
# and `attach` refuses a settings file whose write-back would pass it (`permissions`). No shipped
# file states it.
REGULAR_READ_LIMIT = 64 * 1024 * 1024
# A named cap (CONTRIBUTING.md#named-caps): how many directory entries a walk over a repository's
# tree that looks for something lists, one total across everything that walk lists, before it stops
# and says it could not tell. One number for every walk that has that answer, each reading it here
# when it starts: `doctor`'s walk of the note store (`memory.doctor`). `docs/cli.md` states it in
# `doctor`'s `memory` row.
WALK_ENTRIES = 500_000

# The open `open_regular` makes: it follows a link at the last component, because `attach` links
# each memory group and `MEMORY.md` into the overlay in overlay mode and those are read through
# the link; it never waits, because a FIFO swapped in after the check would otherwise block the
# open on a writer that never comes; and a terminal swapped in is never made this process's
# controlling one.
_REGULAR_OPEN = os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC


def open_regular(path: Path) -> BinaryIO:
    """`path` opened for reading in binary, if it names a regular file, followed through
    symbolic links; `NotRegularFile` if it names anything else.

    For a reader of files a repository commits, which globs a directory and reads what it found:
    a committed link to `/dev/zero` read until memory ran out, one to `/dev/stdin` waited on a
    terminal for good, and a FIFO left there waited on a writer. The path is asked with `stat`
    before anything is opened, so a device the path names is never opened. A path repointed
    between that check and the open can still be opened -- without blocking and without becoming
    the controlling terminal -- and is refused unread, because the descriptor must be the same
    file the check saw. A link to a regular file is read, as before. No bound on what is then
    read: `read_bounded` holds the read to a bound, and `read_regular_bytes` and
    `read_regular_text` to `REGULAR_READ_LIMIT`.
    """
    checked = os.stat(path)
    if not stat.S_ISREG(checked.st_mode):
        raise NotRegularFile(errno.EINVAL, "not a regular file", str(path))
    descriptor = os.open(path, _REGULAR_OPEN)
    try:
        # The file that was checked, and so a regular one: anything else opened in its place,
        # a device or another file, is a different `(st_dev, st_ino)`.
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (checked.st_dev, checked.st_ino):
            raise NotRegularFile(errno.EINVAL, "not a regular file", str(path))
    except BaseException:
        os.close(descriptor)
        raise
    # Outside the `try`: once `fdopen` has the descriptor it closes it on its own failure, and a
    # second close here would raise `EBADF` in place of the real error.
    return os.fdopen(descriptor, "rb")


# The open `read_bounded_within` makes of the last component under a root: `_REGULAR_OPEN`'s, and a
# link there is refused as every component above it is.
_WITHIN_OPEN = _REGULAR_OPEN | getattr(os, "O_NOFOLLOW", 0)


@contextmanager
def open_directory(path: Path) -> Iterator[int]:
    """A descriptor for the directory `path`, its last component not followed if it is a link,
    closed when the block ends: the `root` `read_bounded_within` reads several files under with
    one open."""
    descriptor = os.open(path, _DIR_FLAGS)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _opened_within(root: Path | int, relative: str) -> int:
    """A descriptor for `root/relative` opened for reading, no component of it followed if it is a
    symbolic link, the file included; `NotRegularFile` if it is anything else. `root` an open
    directory descriptor takes one name and no path below it."""
    if isinstance(root, int):
        parts = checked_components(relative)
        if len(parts) != 1:
            raise UnsafePath(f"{relative!r} is not one name in the directory it is read from")
        descriptor = os.open(parts[0], _WITHIN_OPEN, dir_fd=root)
    else:
        with open_within(root, relative) as (dir_fd, name):
            descriptor = os.open(name, _WITHIN_OPEN, dir_fd=dir_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NotRegularFile(errno.EINVAL, "not a regular file", relative)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_upto(descriptor: int, size: int) -> bytes:
    """Up to `size` bytes from `descriptor`, fewer only at its end: unbuffered, since a header of a
    few bytes costs a buffer's allocation per file otherwise, and looped, since one `read` may
    return less than it was asked for."""
    chunks: list[bytes] = []
    while size > 0:
        chunk = os.read(descriptor, size)
        if not chunk:
            break
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _bounded(content: bytes, limit: int) -> tuple[bytes, bool]:
    """`content`, read to one byte past `limit`, cut to `limit`, and whether it ran past it."""
    return content[:limit], len(content) > limit


def read_bounded(path: Path, limit: int) -> tuple[bytes, bool]:
    """At most `limit` bytes of a regular file, and whether it holds more than that.

    The one reader of a file held to a bound, whatever the bound is, and `read_bounded_within` the
    one under a root. `path` is opened by `open_regular`'s rules: followed through symbolic links,
    and a regular file only. The open never waits on a FIFO, anything but a regular file is
    `NotRegularFile`, and one byte past `limit` is asked for and no more, so a file that reads on
    for as long as anyone asks costs `limit + 1` bytes and is over.
    """
    with open_regular(path) as stream:
        return _bounded(stream.read(limit + 1), limit)


def read_bounded_within(root: Path | int, relative: str, limit: int) -> tuple[bytes, bool]:
    """`read_bounded` of `relative` under `root`, reached as `open_within` reaches a file.

    No component is followed if it is a symbolic link, the file included, and the descriptor
    opened is asked whether it is a regular file, so the open never waits on a FIFO either. `root`
    may be a directory `open_directory` opened, and `relative` then one name in it, so a reader of
    many files in one directory opens it once.
    """
    descriptor = _opened_within(root, relative)
    try:
        return _bounded(_read_upto(descriptor, limit + 1), limit)
    finally:
        os.close(descriptor)


def read_regular_bytes(path: Path, *, limit: int | None = None) -> bytes:
    """The bytes of a regular file, reached as `read_bounded` reaches it, and `TooLarge` for one
    longer than `limit`, or than `REGULAR_READ_LIMIT` where a reader holds its files to no cap of
    its own."""
    content, over = read_bounded(path, REGULAR_READ_LIMIT if limit is None else limit)
    if over:
        raise TooLarge(errno.EFBIG, "larger than this reader reads", str(path))
    return content


def read_regular_text(path: Path, *, newline: str | None = None, limit: int | None = None) -> str:
    """`read_regular_bytes(path, limit=limit)` decoded as UTF-8, a `UnicodeDecodeError` where it
    is not.

    `newline` as `open` takes it, for the two spellings readers use: `None` translates every CRLF
    and lone CR to LF, as `Path.read_text` does, and `""` keeps the file's own line endings.
    """
    text = read_regular_bytes(path, limit=limit).decode("utf-8")
    if newline is None:
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def checked_components(relative: str) -> tuple[str, ...]:
    """The path's components, or `UnsafePath` for any spelling that could leave the root.

    **Public, and the one place this rule lives.** `config.paths.contained()` calls it rather
    than carrying a copy, because a copy written against `Path(relative).parts` agrees with it
    only about the values nobody has to think about. Those parts are normalised, so `docs//x.md`,
    `docs/x/` and `./docs` would reach such a copy as `('docs', 'x.md')`, `('docs', 'x')` and
    `('docs',)`: a configured path that `plan()` reports no refusal for and that the walk below
    then refuses at the write, part-way through a pass, with earlier artifacts already on disk.
    That is also why this is not private: `contained()` is in a subpackage and this is a leaf
    module, so the call goes this way and the leaf stays a leaf.

    The split is on the **raw string**, not on `PurePosixPath(relative).parts`. Those parts are
    already normalised — `.` and empty segments are dropped, a trailing slash disappears — so a
    guard written against them can only ever see what pathlib chose to keep, and the one
    component it does keep is the one that escapes. Reading the caller's own spelling means
    every refusal below is a refusal of something a caller can actually write:

    * the empty path, and any path that is only separators;
    * an absolute path — `openat` ignores its `dir_fd` for one, so the walk would restart at
      the filesystem root;
    * `..`, which walks out one component at a time; and `.`, and an empty segment (`a//b`, a
      trailing slash), which are merely odd rather than dangerous — refused because this
      function's answer is what every caller reads as "contained", and a surface that quietly
      rewrites its argument is a surface whose guarantee has to be restated per caller;
    * a component holding a NUL, which names no file anywhere and which every path call meets
      with a `ValueError` rather than an `OSError`;
    * and git's control directory, at any depth and in any case — see `CONTROL_DIRECTORY`.
      Staying inside the root is not the whole of containment for a repository-scoped tool:
      `.git/hooks/pre-commit` is inside every root drybag is ever handed, and a clone that
      pointed a `MANAGED_REGION` artifact at it had the developer's executable hook rewritten
      in place, because `_mode_of` carries an existing file's 0755 onto the replacement. This
      is the last line before the write, under `contained()` rather than instead of it, and it
      covers the callers that never had a configured string to check — `attach`, `overlay` and
      `hooks` all pass paths that `contained()` never sees.
    """
    if relative.startswith("/"):
        raise UnsafePath(f"{relative!r} is absolute; a path here must stay inside the root")
    if not relative:
        raise UnsafePath(f"{relative!r} names no file")
    parts = tuple(relative.split("/"))
    for part in parts:
        if part == "":
            raise UnsafePath(f"{relative!r} has an empty path component")
        if "\x00" in part:
            # A NUL names no file on any system, and every path call meets it with a
            # `ValueError` no caller of this rule catches.
            raise UnsafePath(f"{relative!r} holds a NUL, which no path can")
        if part in (_PARENT, _HERE):
            raise UnsafePath(
                f"{relative!r} contains {part!r}; a path here must stay inside the root"
            )
    if names_control_directory(relative):
        raise UnsafePath(
            f"{relative!r} names {CONTROL_DIRECTORY!r}; git's control directory is not a "
            "repository-scoped tool's to write into"
        )
    return parts


@contextmanager
def open_within(root: Path, relative: str) -> Iterator[tuple[int, str]]:
    """Yield `(directory descriptor, final name)` for `root/relative`, following no symlink.

    The caller writes through the descriptor, so nothing between this walk and the write can
    redirect it: `os.replace(..., src_dir_fd=fd, dst_dir_fd=fd)` never re-resolves the parent.

    `relative` must stay inside `root` by its own spelling — see `checked_components`.
    """
    parts = checked_components(relative)
    fd = os.open(root, _DIR_FLAGS)
    opened = [fd]
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                # O_NOFOLLOW on a symlink reports ELOOP on Linux and ENOTDIR on macOS when the
                # link points at a directory; both mean the same thing here.
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise UnsafePath(
                        f"{relative!r}: {part!r} is a symlink or not a directory"
                    ) from exc
                raise
            opened.append(nxt)
            fd = nxt
        yield fd, parts[-1]
    finally:
        for handle in reversed(opened):
            os.close(handle)


def check_within(root: Path, relative: str) -> None:
    """Walk to `root/relative` as every write and removal here does, and touch nothing.

    For a caller that must know before its first write whether a later one would be refused: it
    raises the same `UnsafePath` the write or removal would, from the same walk, so the two cannot
    disagree about which components count. A component that is not there is not an error, since
    a removal through it has nothing to remove.
    """
    with contextlib.suppress(FileNotFoundError), open_within(root, relative):
        pass


def _mode_of(dir_fd: int, name: str) -> int | None:
    """The mode to carry over, and `None` for a file that is not there to carry one.

    `None` rather than `NEW_FILE_MODE` so the caller can tell "keep this file's mode" from
    "this is a new file": the second is the case the umask gets to decide, and answering it
    with a number made the two indistinguishable.

    `lstat` on a symlink reports `0o777`, and carrying that onto the replacement would make it
    world-writable. `contained()` refuses a symlink at the final component before any caller
    here runs, so this is a floor under a guard that lives in another module — one caller
    away, and not one this module can see.
    """
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return stat.S_IMODE(info.st_mode) if stat.S_ISREG(info.st_mode) else None


def _sync_directory(dir_fd: int) -> None:
    """Make the rename itself durable, and never fail a completed write because it was not.

    The data is already on disk by the time this runs and the rename has already happened, so
    a filesystem that refuses `fsync` on a directory (some do, with EINVAL) has cost this call
    its durability guarantee and nothing else. Raising here would turn a successful write into
    an exception the caller would report as a failed one.
    """
    with contextlib.suppress(OSError):
        os.fsync(dir_fd)


def write_atomically_at(
    dir_fd: int,
    name: str,
    text: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    mode: int | None = None,
) -> None:
    """Replace `name` inside the already-opened directory, keeping the mode it had, or giving it
    `mode` where the caller names one: a copy that keeps its source's mode.

    `errors` is the codec's: `surrogateescape` writes back the bytes a file was read with under
    the same handler, for a caller that must keep bytes that are not UTF-8 exactly."""
    mode = _mode_of(dir_fd, name) if mode is None else mode
    temporary = f".drybag-{os.getpid()}-{name}.tmp"
    create = NEW_FILE_MODE if mode is None else 0o600
    handle = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, create, dir_fd=dir_fd)
    try:
        with os.fdopen(handle, "w", encoding=encoding, errors=errors) as stream:
            stream.write(text)
            if mode is not None:
                # `os.fchmod` on the descriptor rather than
                # `os.chmod(..., follow_symlinks=False)`. The temporary was just created
                # O_CREAT|O_EXCL, so it cannot be a symlink and the flag bought nothing; and
                # that form is accepted only where `os.chmod in os.supports_follow_symlinks`,
                # which is a runtime property of the platform rather than a guarantee. Setting
                # the mode on the open descriptor needs no such support and closes the
                # create-to-chmod window. A *new* file is not chmod-ed at all: it was created
                # with `NEW_FILE_MODE` and the kernel has already applied the umask to it.
                os.fchmod(stream.fileno(), mode)
            # Userspace to kernel, then kernel to disk. `flush` alone was the whole of the
            # durability story here, and it is only the first half: it moves the bytes out of
            # Python's buffer into the page cache, where a power loss still loses them.
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        _sync_directory(dir_fd)
    except BaseException:
        _unlink_quietly(dir_fd, temporary)
        raise


def _unlink_quietly(dir_fd: int, name: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(name, dir_fd=dir_fd)


def write_atomically(
    path: Path, text: str, *, encoding: str = "utf-8", errors: str = "strict"
) -> None:
    """Replace `path` with `text` in one step, keeping the mode it already had.

    The plain-path form, for callers that already hold a trusted absolute path: the memory
    store's own notes and index, whose directory the resolver has already validated. It is the
    same write as `write_atomically_at` — the mode rule, the `fsync` pair and the temporary's
    lifetime are one implementation, reached through a descriptor opened on `path.parent`,
    rather than a second copy that can drift from it. It drifted once: this half used
    path-based `os.chmod`, reopening by name exactly the create-to-chmod window its sibling
    carries seven lines of comment about closing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        write_atomically_at(dir_fd, path.name, text, encoding=encoding, errors=errors)
    finally:
        os.close(dir_fd)


# The flags of the one appending write: a regular file created 0600 where none is, written at its
# end, and never through a link at its last component.
# `O_NONBLOCK`, which changes nothing for a regular file: a FIFO left at the path with no reader
# would make the open wait for one for ever, and without blocking it is `ENXIO` instead.
_APPEND_FLAGS = (
    os.O_WRONLY
    | os.O_APPEND
    | os.O_CREAT
    | getattr(os, "O_NOFOLLOW", 0)
    | os.O_CLOEXEC
    | os.O_NONBLOCK
)
APPENDED_FILE_MODE = 0o600


def append_at(dir_fd: int, name: str, payload: bytes) -> None:
    """Append `payload` to the file `name` in the directory `dir_fd` (one `open_within` yielded),
    in one `O_APPEND` write, creating it `APPENDED_FILE_MODE` where it is not there.

    The one appending write, which the hook sink's diagnostics reach.
    One `write` of the whole line, so two processes appending at once never interleave inside a
    line on a local filesystem, `O_NOFOLLOW`, so a link put where the file goes refuses the open
    rather than redirecting the write, and `O_NONBLOCK`, so a FIFO there with no reader refuses it
    (`ENXIO`) rather than waiting for one. Raises `OSError` as the open or the write does.
    """
    handle = os.open(name, _APPEND_FLAGS, APPENDED_FILE_MODE, dir_fd=dir_fd)
    try:
        os.write(handle, payload)
    finally:
        os.close(handle)


def mkdirs_within(root: Path, target: str) -> None:
    """Create `target`'s parent directories without ever leaving the root.

    `Path.mkdir(parents=True)` cannot do this job: it takes a string and it follows symlinks,
    so a component that became a link after `contained()` passed makes it create directories
    outside the root — and it creates them before the `O_NOFOLLOW` walk gets a chance to
    refuse. Each component here is created relative to a descriptor the walk just opened, so a
    symlink anywhere along the path refuses with nothing created.

    Public, and here rather than in `scaffold.engine` where it was written, because it is the
    subtle half of the surface rule that every area putting a file into a repository calls it
    instead of writing files of its own. `attach`, `setup`, `overlay` and `hooks` all write
    files that are not `Template`s; a private helper leaves each of them to re-derive this, and
    the failure mode of getting it wrong is silent.
    """
    # `checked_components` and not `PurePosixPath(target).parts`, so the whole target is
    # refused by
    # its own spelling before any directory is created, rather than one branch at a time.
    parts = checked_components(target)[:-1]
    for depth in range(len(parts)):
        branch = "/".join(parts[: depth + 1])
        with open_within(root, branch) as (dir_fd, name), contextlib.suppress(FileExistsError):
            os.mkdir(name, dir_fd=dir_fd)


def write_within(
    root: Path, target: str, text: str, *, encoding: str = "utf-8", mode: int | None = None
) -> None:
    """Create the parents and replace `root/target`, reaching it through the `O_NOFOLLOW` walk;
    `mode` is `write_atomically_at`'s."""
    mkdirs_within(root, target)
    with open_within(root, target) as (dir_fd, name):
        write_atomically_at(dir_fd, name, text, encoding=encoding, mode=mode)


# The flags of the one exclusive create: a regular file made only where nothing is at the name.
# `O_CREAT | O_EXCL` fails with `EEXIST` on any existing name -- a file, a directory, a FIFO or a
# link, dangling or not -- and follows no link at the last component, so nothing more is asked.
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC


def create_within(root: Path, target: str, data: bytes, *, mode: int) -> bool:
    """Create `root/target` holding `data` with exactly `mode`'s bits, and say whether it did.

    The walk is `open_within`'s, and the create is `O_CREAT | O_EXCL`, so a name that
    is already there -- a file, a directory or a link, one pointing nowhere included -- is left as
    it is and answers `False`, and nothing is ever written through a link. The parents are not
    created. `mode` is set on the descriptor, which the umask does not narrow; a write that fails
    after the create removes the file it created and raises.
    """
    with open_within(root, target) as (dir_fd, name):
        try:
            handle = os.open(name, _CREATE_FLAGS, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            return False
        try:
            with os.fdopen(handle, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
        except BaseException:
            _unlink_quietly(dir_fd, name)
            raise
    return True


def remove_within(root: Path, target: str) -> None:
    """Unlink `root/target` through the same walk; an absent file is not an error.

    `contextlib.suppress` is entered after `open_within` has yielded, so it covers the unlink
    and not the walk — a refused walk must still raise.
    """
    with open_within(root, target) as (dir_fd, name), contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dir_fd)


def rmdir_within(root: Path, target: str) -> None:
    """Remove the empty directory `root/target` through the same walk; an absent one is not
    an error.

    `remove_within` cannot do this job and cannot be made to: `unlink` on a directory is EPERM
    on macOS and EISDIR on Linux, so a caller that reached for it got an `OSError` it was most
    likely already swallowing, and a tree that quietly never shrank.

    Public, and here rather than private to its caller, for the reason `mkdirs_within` gives one
    function above: "a private helper leaves each of them to re-derive this, and the failure mode of
    getting it wrong is silent". The hook sink asked for it — its marker tree is keyed by session
    and must be pruned, one of the two removals driven by a directory listing that the
    enumerated-writes rule (CONTRIBUTING.md#enumerated-writes) permits — and it is the whole of the
    difference from `remove_within`, so a later hardening of that walk reaches this too instead of
    leaving a copy behind.
    """
    with open_within(root, target) as (dir_fd, name), contextlib.suppress(FileNotFoundError):
        os.rmdir(name, dir_fd=dir_fd)


class NotASymlink(OSError):
    """A real file or directory was found where a symlink was asked about; it is left alone."""


def _type_of(dir_fd: int, name: str) -> int | None:
    """`S_IFMT` of the entry, or `None` when nothing is there — the link questions' `lstat`.

    Separate from `_mode_of`, which answers "what permission bits does the replacement carry
    over" and deliberately returns `None` for anything that is not a regular file, with the
    type bits stripped: `stat.S_ISLNK(_mode_of(...))` is False for every value it can return.
    """
    try:
        return stat.S_IFMT(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return None


def readlink_within(root: Path, target: str) -> Path | None:
    """The target of the symlink at `root/target`; `None` when nothing is there.

    Through the same walk as every other primitive, so the question is asked of the entry
    the descriptor names and not of whatever a re-resolved path would reach. A real entry
    raises `NotASymlink`: the callers treat it as somebody else's and never remove it.
    """
    with open_within(root, target) as (dir_fd, name):
        kind = _type_of(dir_fd, name)
        if kind is None:
            return None
        if kind != stat.S_IFLNK:
            raise NotASymlink(f"{target!r} is not a symlink")
        return Path(os.readlink(name, dir_fd=dir_fd))


def symlink_within(root: Path, target: str, source: Path) -> None:
    """Create `root/target -> source` through the walk; parents are created the same way.

    `FileExistsError` when anything is already there — the caller reads first
    (`readlink_within`) and removes first (`unlink_within`); this never replaces.
    """
    mkdirs_within(root, target)
    with open_within(root, target) as (dir_fd, name):
        os.symlink(str(source), name, dir_fd=dir_fd)


def unlink_within(root: Path, target: str, *, pointing_at: Path | None = None) -> bool:
    """Remove the symlink at `root/target`; report whether anything was removed.

    A real entry raises `NotASymlink`. With `pointing_at`, only a link to exactly that path
    is removed — the mirror of `_link`'s "a symlink pointing anywhere else belongs to
    somebody else".
    """
    with open_within(root, target) as (dir_fd, name):
        kind = _type_of(dir_fd, name)
        if kind is None:
            return False
        if kind != stat.S_IFLNK:
            raise NotASymlink(f"{target!r} is not a symlink")
        if pointing_at is not None and Path(os.readlink(name, dir_fd=dir_fd)) != pointing_at:
            return False
        os.unlink(name, dir_fd=dir_fd)
        return True
