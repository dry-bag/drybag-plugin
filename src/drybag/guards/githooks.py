"""The chained `prepare-commit-msg` hook and its per-repository installer.

Installed into `git rev-parse --git-path hooks`, never through `core.hooksPath`: a global
setting is overridden by any repository that sets its own and silently competes with husky
elsewhere on the machine, while `--git-path` honours a `core.hooksPath` the repository already
has — computing `<common-dir>/hooks` by hand would install into a directory git never reads
when one is set. Hooks resolve from the common directory, so one installation covers the main
checkout and every worktree.

A foreign hook of the same name is kept as `<name>.local` and the shipped hook `exec`s it
last, so nothing that was already running stops running. `uninstall` puts it back.

This is the one place this area writes outside a project root on purpose: the hooks directory is
git's, and in a worktree it is not under the checkout at all. The writes are
`fsops.write_atomically` on the hook path and a rename of the foreign hook beside it, and both paths
are named in `docs/cli.md`'s **Writes** paragraph for `drybag setup --git-hooks`, as the
enumerated-writes rule (CONTRIBUTING.md#enumerated-writes) asks of every path a command writes.
"""

from __future__ import annotations

import os
import stat
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from drybag import fsops
from drybag.errors import Refusal
from drybag.gitenv import hooks_dir
from drybag.printed import quoted

HOOK_NAME = "prepare-commit-msg"
HOOK_MARKER = "# drybag:prepare-commit-msg"
LOCAL_SUFFIX = ".local"
# The mode the hook is given. Not a config key and not a bound on anything: git runs a hook
# only if it is executable, so any other value is a hook that silently never runs. Owner and
# group write are off for the same reason `fsops` picks conservative modes.
_EXECUTABLE = 0o755

_CHAIN_LINE = '    exec "$previous" "$@"'
# The chain below is spelled `"$0.local"` and names `prepare-commit-msg.local` nowhere, so it is
# safe from the check `install` makes of a predecessor's bytes for that name: `_ours` recognises
# drybag's own hook by its marker first, and such a hook is rewritten in place, never kept as
# `.local`, so `$0.local` never resolves to this file itself.
# Every line of the hook stays under ruff's line length: the formatter never reflows a string
# literal, and a lint-suppression comment inside one would ship as hook text, so the long
# messages are shell variables. `{}` are absent from the shell on purpose; the only braces are
# the f-string's, which is why the command is chosen into a plain variable rather than into a
# shell function.
HOOK_TEXT = f"""#!/usr/bin/env bash
{HOOK_MARKER}
# Strip AI/tool attribution trailers before the commit message is presented.
#
# prepare-commit-msg rather than commit-msg on purpose: `git commit --no-verify` bypasses
# pre-commit and commit-msg, but not this hook. It is still only the local convenience layer;
# the authoritative gate is `drybag commit check` in CI, which sees commits this machine
# never produced. Written by `drybag setup --git-hooks`, and removed again, with the hook it
# chains to put back, by `drybag setup --git-hooks --uninstall`.
#
# Chains to whatever hook was here before, kept beside this one as `<hook>.local`, so
# installing this never silently disables husky, pre-commit, or what the repository had.
set -euo pipefail

msg_file="$1"
gate="CI's commit check is the gate"
failed="drybag: commit strip failed; $gate"
absent="drybag: not installed for this shell; trailers are not stripped locally; $gate"
quiet='^nothing to strip'

# Expanded unquoted where it is used, so the two-word form splits into words. It is only ever
# one of the two literals assigned just below, and never anything a repository chose.
runner=""
if command -v drybag >/dev/null 2>&1; then
    runner="drybag"
elif python3 -c 'import drybag' >/dev/null 2>&1; then
    runner="python3 -m drybag"
fi

# `commit strip` prints one line on stdout: "stripped N ...", "nothing to strip", or, where the
# owner's own harness settings say what a commit carries, "kept N ..." with that reason. A
# rewrite and a kept trailer are worth a line on the terminal, so only the quiet one is filtered
# out and everything else is said. The branch is on the command's own status and never on
# `grep`'s: `grep -v` exits 1 when it selects no lines, which is exactly the quiet case, so a `||`
# hung off the pipe announces a failure on every clean commit. No outcome here may fail the commit.
if [ -z "$runner" ]; then
    printf '%s\\n' "$absent" >&2
elif out=$($runner commit strip "$msg_file"); then
    printf '%s\\n' "$out" | grep -v "$quiet" >&2 || true
else
    printf '%s\\n' "$failed" >&2
fi

previous="$0.local"
if [ -x "$previous" ]; then
{_CHAIN_LINE}
fi
"""


# What `install` says of a hook that would run itself. A hook it keeps as `.local` is `exec`ed
# last by drybag's own, so one whose bytes name `prepare-commit-msg.local` then `exec`s itself,
# and `git commit` runs until it is killed. `{path}` is the file read, `{local}` where it is or
# would be kept, both in git's own hooks directory, named absolutely as every line here is. The
# check is on the name and not on how the chain is spelled, so a `--git-common-dir` chain read
# under a `core.hooksPath` that git does not resolve to that directory is refused too; the remedy
# covers it. A chain that builds the name at run time is not seen (`docs/cli.md` says which).
CHAINS_TO_ITSELF = (
    "{path} names {name}: kept as {local}, which drybag's hook runs last, it would run itself "
    "until the commit is killed; remove its chain line, or move it aside; nothing was installed"
)
# What a re-install says of the `.local` beside drybag's hook when it names itself. There the
# loop is not a risk but what is on disk: the installed hook `exec`s that file last, so every
# commit already runs until it is killed. `{local}` is that file, `{path}` the installed hook,
# which the refusal leaves as it was.
ALREADY_RUNS_ITSELF = (
    "{local} names {name}: drybag's hook at {path} runs it last, so it runs itself and every "
    "commit runs until it is killed; remove its chain line, or move it aside; the installed hook "
    "was left as it was"
)
_CHAINED_NAME = HOOK_NAME + LOCAL_SUFFIX


class Installed(NamedTuple):
    path: Path
    preserved: Path | None
    replaced: bool


class Found(Enum):
    """What `uninstall` found at the hook path. One value, so no answer can say that it both removed
    drybag's hook and left a foreign one; a report built from `restored` alone said "removed" of
    all three. A hook that cannot be read is none of them: `uninstall` refuses it."""

    REMOVED = "removed"  # drybag's hook, which is gone
    ABSENT = "absent"  # no hook at all
    FOREIGN = "foreign"  # a hook drybag did not write, left as it was


class Removed(NamedTuple):
    """What `uninstall` found at the hook path, and the hook it put back there, if any."""

    path: Path
    restored: Path | None
    found: Found


# What `install` and `uninstall` say of a hook they cannot read. `{path}` is git's own hooks
# directory, which the reports name absolutely, as every other line of this module does, and
# through `printed.quoted`: git names it, from a `core.hooksPath` the repository sets or a
# submodule name `.gitmodules` chose, so its bytes are the repository's.
UNREADABLE = "{path} could not be read ({reason}); {outcome}"


class Hook(NamedTuple):
    """What `_ours` read at a hook path: whether the hook is drybag's, and its bytes, read once,
    or `None` for a regular file past the read cap, which is foreign and is not read."""

    ours: bool
    content: bytes | None


def _ours(path: Path, outcome: str) -> Hook:
    """Whether the hook at `path` is drybag's, by its marker, and the bytes that said so; bytes
    that are not UTF-8 are not drybag's.

    Read as a regular file only, to the read cap (`fsops.read_regular_bytes`): a FIFO there is
    never waited on, as `read_text` waited on it. A hook that cannot be read is neither ours nor
    foreign, and is refused, ending in `outcome`, what the caller did not do: read as foreign, it
    was "not drybag's" to `uninstall` and renamed and chained by `install` when it was
    drybag's own at mode 000. A regular file past the cap is the exception, and is foreign:
    drybag's own hook is a couple of kilobytes, so one that long is certainly not it.
    """
    try:
        content = fsops.read_regular_bytes(path)
    except fsops.TooLarge:
        return Hook(False, None)
    except OSError as exc:
        raise Refusal(
            UNREADABLE.format(path=quoted(str(path)), reason=fsops.said(exc), outcome=outcome)
        ) from exc
    try:
        return Hook(HOOK_MARKER in content.decode("utf-8"), content)
    except UnicodeDecodeError:
        return Hook(False, content)


def _refuse_a_self_chain(content: bytes | None, words: str, *, path: str, local: str) -> None:
    """Refuse, in `words` (`CHAINS_TO_ITSELF` or `ALREADY_RUNS_ITSELF`, filled in only then), a
    hook whose bytes name `prepare-commit-msg.local`, where it is or would be kept and from where
    drybag's hook `exec`s it: it would then `exec` itself. `path` and `local` are the hook and
    its `.local`, as the refusal prints them.

    `None` is a hook past the read cap, which is not judged: `install` keeps and chains it, as it
    always has (`docs/cli.md`). Bytes, not text, so a hook that is not UTF-8 is judged as well.
    """
    if content is not None and _CHAINED_NAME.encode() in content:
        raise Refusal(words.format(path=path, name=_CHAINED_NAME, local=local))


def _chained(local: Path) -> bytes | None:
    """The bytes of the `.local` beside drybag's installed hook, or `None` where they cannot be
    judged: past the read cap, or not a readable regular file. A hook git cannot read cannot run,
    so it cannot run itself either, and a re-install over it goes on as it always has."""
    try:
        return fsops.read_regular_bytes(local)
    except OSError:  # `fsops.TooLarge` included
        return None


def install(root: Path) -> Installed:
    directory = hooks_dir(root)
    target = directory / HOOK_NAME
    local = directory / (HOOK_NAME + LOCAL_SUFFIX)
    # Every refusal below names these two as `UNREADABLE` does, quoted: git chose the directory.
    shown_target, shown_local = quoted(str(target)), quoted(str(local))
    if fsops.is_symlink(target):
        raise Refusal(f"{shown_target} is a symlink; refusing to write through it")
    if fsops.is_dir(target):
        raise Refusal(f"{shown_target} is a directory; refusing to install a hook over it")
    found = _ours(target, "nothing was installed") if fsops.exists(target) else None
    replaced = found is not None and found.ours
    # A `.local` found where our hook is *not* installed was put there by somebody else, and
    # the shipped hook `exec`s whatever sits at that name, so installing over it would run a
    # stranger's file under drybag's name. Note the bound, which is the whole of the
    # contract: this fires only while our hook is absent. Once ours is installed, the `.local`
    # beside it is presumed to be the one we preserved, and a file that appears there
    # afterwards is vouched for by nothing here — see `uninstall`.
    if fsops.exists(local) and not replaced:
        raise Refusal(
            f"{shown_local} already exists and was not preserved by drybag; move it aside"
        )
    # A hook that names `prepare-commit-msg.local` is refused before it is kept there, and the
    # `.local` beside a hook being reinstalled is read for the same name: either way the chain
    # would `exec` itself. The check is made here, at install, and not by the shipped hook before
    # its `exec`, where a false positive would abort every commit.
    if found is not None and not replaced:
        _refuse_a_self_chain(found.content, CHAINS_TO_ITSELF, path=shown_target, local=shown_local)
    if replaced and fsops.exists(local):
        _refuse_a_self_chain(
            _chained(local), ALREADY_RUNS_ITSELF, path=shown_target, local=shown_local
        )
    preserved: Path | None = None
    if found is not None and not replaced:
        target.rename(local)  # beside the hook, in git's own directory, as `docs/cli.md` says
        # Its mode is kept as found: `chmod -x` is how a developer switches a hook off, and
        # the chain tests `-x` for exactly that reason.
        preserved = local
    fsops.write_atomically(target, HOOK_TEXT)
    os.chmod(target, _EXECUTABLE)  # a git hook must be executable to run at all
    return Installed(target, preserved, replaced)


def uninstall(root: Path) -> Removed:
    directory = hooks_dir(root)
    target = directory / HOOK_NAME
    local = directory / (HOOK_NAME + LOCAL_SUFFIX)
    if not os.path.lexists(target):
        return Removed(target, None, Found.ABSENT)
    # drybag writes only a regular file here, so a link, a directory or a FIFO is foreign as
    # surely as a regular file past the read cap is, and is left in place unopened.
    if not stat.S_ISREG(os.lstat(target).st_mode) or not _ours(target, "nothing was removed").ours:
        return Removed(target, None, Found.FOREIGN)
    target.unlink()
    # Whatever sits at `.local` is restored, and this does *not* check that install put it
    # there. It cannot: `install` refuses a stray `.local` only while our hook is absent, so a
    # file dropped beside an installed hook is unexamined — and the installed hook has been
    # `exec`ing it on every commit since, which is the larger fact. Restoring it is therefore
    # the honest end of that state rather than a new exposure, and it is deliberate: the
    # `.local` name is the contract `setup` relies on, and a provenance marker or an
    # unconditional refusal here would be this module inventing a different one.
    if fsops.exists(local):
        local.rename(target)
        return Removed(target, target, Found.REMOVED)
    return Removed(target, None, Found.REMOVED)
