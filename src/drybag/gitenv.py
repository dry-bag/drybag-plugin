"""Which `git` this project runs, the environment it is given, and the bound on how long it may
take.

One module because every caller needs the same rule, and being the same for all of them is the
point: a git answer must be a real one about the repository asked, never one an inherited
`GIT_DIR` produced. The hook path's toplevel query feeds the hook's project root
(`checkout_root` below), which every hook decision is derived from, so a query that inherited
the session's `GIT_DIR` or `GIT_WORK_TREE` would make every handler in the process answer for a
different repository than the one the user is sitting in; the memory store's queries decide
whose notes a session reads, and the same variable would choose another project's.

A leaf module: it imports `drybag.errors`, `drybag.fsops` and `drybag.processes`, three
leaves, and nothing else from `drybag`, so the hook path pays no area import to reach it, and
no caller has to import another's area to share the constants.

`git` is the one on `PATH`, handed the environment's `PATH` and `HOME`, so that the machine
owner's `git`, `safe.directory` and excludes answer, and the ssh agent and proxy variables a push
or a clone needs (`scrubbed_env`): the one environment every git drybag starts is given, the
`Runner`'s included. Every other variable is dropped, every `GIT_CONFIG_*`, `GIT_SSH_COMMAND` and
`XDG_CONFIG_HOME` among them, so no inherited variable names another repository, a configuration
git would otherwise not read, or a program to run. Inside a `named` block it is also handed
`GIT_DIR` and `GIT_WORK_TREE`, naming the one checkout the block's caller chose.

`git_run` is the one runner for every question this project asks git about a repository it works on
— the hook path's toplevel, the three-valued answer (`git_answer`) the memory store and
`origin_remote` read and its usability probe, the hooks directory, the dirty count and the commit
range included. One decoding boundary, and a lossless one, so no byte of git's answer can raise out
of a command and each site decides only what an answer means to it. `git_call` is the same git for
a caller that reads its stderr and its timeout too and lays settings of its own over the
environment (the note sync), started through `processes.run_bounded`, which ends its whole group.
The `Runner` seam in `drybag.runner`, which launches the owner's own commands — `git clone`
among them — for their exit code and a message, is the other.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from drybag import fsops
from drybag.errors import Failure, Refusal
from drybag.processes import Ran, run_bounded

# Everything else is dropped, an inherited `GIT_DIR` and `GIT_WORK_TREE` above all. The ssh agent
# and the proxy variables curl reads are kept: a push and a clone need them, and neither changes
# where git reaches, only how it gets there.
GIT_ENV_KEEP = (
    "PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "SSH_AUTH_SOCK",
    "http_proxy", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY",
)  # fmt: skip

# Wall-clock bound on one `git` call: a named cap (CONTRIBUTING.md#named-caps), and no shipped file
# changes with it. It is the bound for a local, argument-free, read-only query against the
# environment below (`rev-parse`, `remote get-url`, `--version`), which neither touches the network
# nor grows with the repository: it guards against a `git` binary that hangs outright, and is
# generous for that without leaving a hook blocked for long. A caller whose query is not that shape
# passes its own wider bound instead and says why beside it —
# `QUERY_TIMEOUT_SECONDS` below for one that is merely slow, since a `log --all` over a long history
# is not a five-second `rev-parse`. Tune this number for the hang, not for a remote and not for a
# long history.
GIT_TIMEOUT_SECONDS = 5
# The wider bound on one local query that grows with the repository — a `log --all`, a `grep`
# over a long history, a listing of every tracked file — which neither reaches the network nor
# may be read as "nothing found" when it runs out: the note sync's job records a timeout instead.
QUERY_TIMEOUT_SECONDS = 30

# The one input that raises the least bound `git_run` gives a call, whatever bound its caller
# asked for. Unset, there is no floor, and each bound above, and each caller's own, is the one that
# applies. drybag's own test suite sets it (`tests/conftest.py`), in its own process and in every
# `drybag` it starts, so that a verdict does not turn on how loaded the machine running it is; a
# test about a bound running out removes it and passes a small bound of its own.
#
# **Why a variable here is safe.** It is read from this process's environment and from nothing a
# repository commits: no `drybag.toml` key, no machine-file key, no argument names it. It can
# only raise: `bound_floor` never answers below zero, and `git_run` takes the larger of it and the
# caller's bound, so no value of it shortens any bound, and one that is not a positive number is
# ignored. A repository reaches a process's environment only through something that applies a file
# it commits — a harness's `.claude/settings.json` `env` block, which applies without a trust
# prompt in a non-interactive session, or a `direnv`, `mise` or devcontainer environment, and
# whatever sets this variable can set `PATH` as readily, which chooses that `git` outright; so a
# repository that sets it gains a longer wait,
# and never an answer it could not have had without it. In a hook that wait can outlast the
# harness's own timeout on the entry, which ends the hook unanswered where the bound would have
# ended git with no answer, so the wait is capped, at `FLOOR_CEILING_SECONDS`.
FLOOR_VARIABLE = "DRYBAG_GIT_FLOOR_SECONDS"
# Ten minutes: far above what any load makes a local `git` take, and far below a timeout that
# `subprocess` cannot represent — `timeout=1e300` raises `OverflowError`, which `git_run` does
# not catch, so an uncapped value would end every caller as an internal error.
FLOOR_CEILING_SECONDS: float = 600

# What `git_run`'s `(-1, "")` means, in one clause a caller's message can build on. `git_run`
# does not say which of the three it was, because none of them is an answer about the
# repository: a caller that words `-1` as one cause — "git is not installed", "the fetch timed
# out" — is wrong about the other two. `git_answer`, whose refusal has to name the cause, reads
# it from `_ask`, where it is known. What git printed is never one of them: its output is
# decoded losslessly, so an answer is always read.
NO_ANSWER = "git could not be run, ran past its time limit, or could not be given its input"


def bound_floor() -> float:
    """The least bound `git_run` gives a call now: what `FLOOR_VARIABLE` says when it says a
    positive number, never past `FLOOR_CEILING_SECONDS`, and zero otherwise.

    Read at every call rather than once at import, so a test that removes the variable is under
    the product's own bounds from its next call, in this process and in any it starts.
    """
    try:
        asked = float(os.environ.get(FLOOR_VARIABLE, ""))
    except ValueError:
        return 0.0
    # Written so that `nan`, which compares false with everything, falls to zero with the rest.
    if not asked > 0:
        return 0.0
    return min(asked, FLOOR_CEILING_SECONDS)


def in_work_tree(root: Path) -> bool:
    """Whether `root` or a directory above it holds a `.git` entry, which is how git itself finds
    the repository: a directory in a clone, a file in a worktree or a submodule.

    Read off the disk and never asked of git, because git refuses that question the same way it
    refused the one before it: a `rev-parse` that times out, or that exits 128 on a checkout it
    judges of dubious ownership (`safe.directory`) or on a worktree whose gitdir is gone, reads as
    "no repository" — and a guard that took that answer let its caller through. No inherited
    `GIT_DIR` reaches a `git` this project runs, so the `.git` entry is the one git would use;
    inside a `named` block git uses the named checkout's, at a top every root asked there is below.
    """
    return any(os.path.lexists(directory / ".git") for directory in (root, *root.parents))


# The checkout the `named` block names; `None` outside it.
_named: Path | None = None


@contextmanager
def named(checkout: Path | None) -> Iterator[None]:
    """Inside the block every `git_run` answers for `checkout`'s own repository, its `.git` with
    `checkout` the work tree, whatever directory it is asked in: git looks for no repository.
    The directory asked still places the question inside the work tree, so `--show-prefix` is
    its path below `checkout`. `None` names nothing, and git finds the repository as it does
    outside a block."""
    global _named
    saved, _named = _named, checkout
    try:
        yield
    finally:
        _named = saved


def scrubbed_env() -> dict[str, str]:
    """The variables `GIT_ENV_KEEP` names, as this process has them, `PATH` with its absolute
    entries alone, no terminal prompt, and inside a `named` block the repository it names: what
    every git drybag starts is handed.

    Every git runs in a repository whose contents a clone chose, so an empty or `.` entry of
    `PATH` (a dotfile slip) would find a `git`, or git an `ssh`, planted at its root."""
    env = {key: os.environ[key] for key in GIT_ENV_KEEP if key in os.environ}
    if "PATH" in env:
        env["PATH"] = os.pathsep.join(
            entry for entry in env["PATH"].split(os.pathsep) if os.path.isabs(entry)
        )
    env["GIT_TERMINAL_PROMPT"] = "0"
    if _named is not None:
        env |= {"GIT_DIR": str(_named / ".git"), "GIT_WORK_TREE": str(_named)}
    return env


def pipe_encoding() -> str:
    """The codec `git_run` decodes git's output and encodes its `stdin` with: the filesystem's,
    the one `os.fsdecode`, `Path.iterdir` and `sys.argv` use for the same names.

    Not the locale's. The two agree on Linux, where the filesystem codec follows the locale, and
    differ on macOS, where it is UTF-8 whatever `LC_ALL` says: under a latin-1 locale there, the
    locale's codec turns git's `café.md` into mojibake and sends a name on `stdin` to git as bytes
    that are not its own, so every comparison between git's answer and a path would miss — a
    gitignored document would read as not ignored. The error handler is `surrogateescape`, the
    filesystem's own on POSIX.

    Named once so `answer_bytes` undoes exactly what `git_run` did: where the codec is latin-1
    every byte decodes, so an answer carries no surrogate escape to show it was not UTF-8, and
    only re-encoding it with the same codec recovers the bytes git printed.
    """
    return sys.getfilesystemencoding()


def answer_lines(answer: str) -> list[str]:
    """A line-oriented `git_run` answer, split where git ended each line: at `\\n` alone.

    Never `str.splitlines()`, which also breaks at `\\r`, `\\v`, `\\f`, `\\x1c` to `\\x1e`,
    `\\x85` and the Unicode line and paragraph separators — characters git prints raw inside a
    path — so a worktree at `…/wt\\rx` would be listed as `…/wt`, a directory that is not it. A
    single-line answer is `answer.removesuffix("\\n")` for the same reason, and never `strip()`,
    which takes a trailing space or `\\r` off a path that ends in one. No answer is no lines.
    """
    lines = answer.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def answer_bytes(answer: str) -> bytes:
    """The bytes git printed for a `git_run` answer, for a caller that must read them as UTF-8
    whatever the locale: decoding is lossless both ways and nothing translates a line ending,
    so this is exact.
    """
    return answer.encode(pipe_encoding(), "surrogateescape")


def git_run(
    root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS, stdin: str | None = None
) -> tuple[int, str]:
    """`(returncode, stdout)` of `git -C root args`; `(-1, "")` when git gave no answer.

    The one place this project runs `git` to ask it something: every argument list is built
    from constants by the caller, every pathspec follows `--` or `--end-of-options`, and no
    configuration value reaches this list without `contained()` having refused the `-`-shaped
    ones, and git is handed `scrubbed_env`. A non-zero exit is
    returned, not collapsed — `check-ignore` answers 1 for "nothing matched", and that is an
    answer.

    **Decoded with `surrogateescape`, both ways.** git speaks bytes, and a worktree path, a
    common directory, a name in `ls-files` or a ref can hold one the filesystem's codec cannot
    decode — a latin-1 filename on Linux. Strict decoding would raise `UnicodeDecodeError` out
    of every caller as an internal error, and reading such output as no answer would throw a
    real answer away, which a caller that takes "no answer" for "nothing" turns into a pass of
    what it should refuse. Escaped, and in the
    filesystem's codec (`pipe_encoding`), a byte comes back as the same `str` `os.listdir` and
    `sys.argv` give for it, so an answer is compared with, and opens, the path it names, and a
    name read off the disk goes back to git on `stdin` as its own bytes.

    **In bytes, and decoded here rather than by `subprocess`.** Text mode translates line endings
    on the way in: each `\\r\\n` and each lone `\\r` in git's answer would come back as `\\n`, so
    a name holding a carriage return, printed raw by every `-z` query, would come back as a
    different name, and a gitignored plan so named would read as not ignored. Nothing is
    translated, either way, and git's own stderr, which nobody reads, is never decoded.

    The answer is lossless rather than a placeholder, so every caller still decides about the
    path that is really there. What it does not make safe is writing that `str` into a UTF-8
    file or parsing it as UTF-8 text: a caller whose answer ends up in one checks it itself.

    No answer is three things, and `NO_ANSWER` names all three: git could not be launched, it
    ran past `timeout`, or `stdin` held a
    character the filesystem's codec has no bytes for: a name from a note or a file written in
    UTF-8, asked on Linux under a locale that is not. A name read off the disk never does,
    because it was decoded with that same codec.
    """
    asked = _ask(root, *args, timeout=timeout, stdin=stdin)
    return (-1, "") if isinstance(asked, _NoAnswer) else asked


# What `git_call` gives back: both streams decoded as `git_run` decodes its answer.
Called = Ran[str]


def git_call(
    root: Path,
    *args: str,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> Called:
    """`git <args>` in `root`, given `scrubbed_env()` with `env` laid over it, for no less than
    `bound_floor()` and no more than the larger of it and `timeout`; its whole group ended past
    that (`processes.run_bounded`). Both streams are decoded in `pipe_encoding()` with
    `surrogateescape`, so a path git prints goes back into the next argv as its own bytes.
    `stdin` is bytes or closed. A `git` that cannot be started is code 127."""
    codec = pipe_encoding()
    ran = run_bounded(
        ["git", *args],
        cwd=root,
        env=scrubbed_env() | dict(env or {}),
        timeout=max(timeout, bound_floor()),
        stdin=stdin,
    )
    out, err = (data.decode(codec, "surrogateescape") for data in (ran.out, ran.err))
    return Called(ran.code, out, err, ran.timed_out)


class _NoAnswer(Enum):
    """Which of `NO_ANSWER`'s three causes `_ask` met, kept where it is known. `git_run` flattens
    all three into `-1` for the callers that read them as one; `git_answer` must not, because its
    refusal says what to do, and a timeout and a `git` that cannot start need different words."""

    UNLAUNCHABLE = "unlaunchable"
    TIMED_OUT = "timed out"
    UNENCODABLE_INPUT = "unencodable input"


def _ask(
    root: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS, stdin: str | None = None
) -> tuple[int, str] | _NoAnswer:
    """`git_run`'s question, answered as `(returncode, stdout)` or as why there was no answer.

    The one place a `git` process is started to ask something; everything `git_run`'s docstring
    says of the call is true of this one. A `returncode` below zero is a `git` a signal ended,
    which `subprocess` reports as the signal's negative.
    """
    codec = pipe_encoding()
    try:
        given = None if stdin is None else stdin.encode(codec, "surrogateescape")
        completed = subprocess.run(  # noqa: S603 - see `git_run`'s docstring
            ["git", "-C", str(root), *args],  # noqa: S607 - the `git` on PATH
            input=given,
            capture_output=True,
            check=False,
            timeout=max(timeout, bound_floor()),
            env=scrubbed_env(),
        )
    except subprocess.TimeoutExpired:
        return _NoAnswer.TIMED_OUT
    except UnicodeEncodeError:
        return _NoAnswer.UNENCODABLE_INPUT
    except (OSError, subprocess.SubprocessError):
        return _NoAnswer.UNLAUNCHABLE
    answer = completed.stdout.decode(codec, "surrogateescape")
    return completed.returncode, answer


def _git_toplevel(cwd: Path) -> Path | None:
    """The checkout git names for `cwd`, as the path on disk, or `None` when git named none.

    Through `git_run`, which scrubs the environment because the answer must be a real git answer,
    not one an inherited `GIT_DIR` produced, and `checkout_root` feeds *every* hook decision, so
    an inherited `GIT_DIR` or `GIT_WORK_TREE` would make every handler answer for a different
    repository than the session is in. It also decodes
    the answer losslessly, so on Linux a checkout under a directory named in latin-1 bytes is
    that directory; decoded strictly, it would make every hook an internal error. The line ending
    alone is taken off, so a path that ends in a space is still that path.
    """
    code, out = git_run(cwd, "rev-parse", "--show-toplevel")
    top = out.removesuffix("\n")
    return Path(top) if code == 0 and top else None


def _walk_to_git_root(cwd: Path) -> Path | None:
    """`.git` is a directory in a clone and a file in a worktree or a submodule; both count.

    `git rev-parse --show-toplevel` resolves symlinks in `cwd` before it reports the toplevel,
    so the walk must too: otherwise the same repository reached through its real path and
    through a symlink to it would report two different roots where git collapses them into one.

    Asked through `fsops.exists`, never `Path.exists()`: within a few characters of the longest
    path, `<cwd>/.git` is past it, which `Path.exists()` raised on up to Python 3.13 and answered
    `False` from 3.14. Under Codex, where `cwd` is the payload's, every hook in such a directory
    was an internal error on one interpreter. `fsops`
    reads it as nothing there, as 3.14 did, so the walk goes on to the parents, as git itself,
    which cannot open the path either, would have to.
    """
    if not cwd.is_absolute():
        return None
    for directory in [cwd, *cwd.parents]:
        if fsops.exists(directory / ".git"):
            return directory.resolve()
    return None


def checkout_root(cwd: Path) -> Path | None:
    """The checkout `cwd` sits in: a walk for `.git`, else git itself; `None` if neither names one.

    The hook's project root when no registered harness's variable names one, which makes this
    Codex's hot path: `drybag hook` runs as a subprocess on every tool call, and `git
    rev-parse --show-toplevel` costs about 8 ms of a 33 ms invocation where the walk costs about
    0.004 ms. git stays behind the walk for what a walk cannot see, such as `GIT_DIR` and a bare
    repository. It reads no harness variable: those are the registry's
    (`harnesses.Harness.project_dir_env`), and `hooks.dispatch.read_event` asks them first.
    """
    return _walk_to_git_root(cwd) or _git_toplevel(cwd)


def git_path(root: Path, name: str, what: str) -> Path:
    """Where git keeps `name` for `root`, as `git rev-parse --git-path` names it.

    The one resolver for a file inside git's own directory: `hooks` for `setup --git-hooks`
    and `doctor`, and `info/exclude` for `attach` and `detach`. `--git-path` is what
    maps a name to the common directory every worktree shares, and honours a `core.hooksPath`
    for `hooks`, which a path computed by hand gets wrong either way. `name` is always one of
    the callers' constants, so nothing here is a value to close off; `what` is the caller's
    word for it in a refusal. No `--` after `name`: measured, git prints a literal `--` as a
    second line there.
    """
    code, out = git_run(root, "rev-parse", "--git-path", name)
    # Neither refusal carries a byte this module did not compute, for the reason
    # `guards.commit.commits_in` states beside its own: `rev-parse`'s stderr is
    # repository-authored — it quotes the offending CONFIG VALUE, `core.hooksPath` included — and
    # is never read. `root` is the caller's own path and is the actionable part; it is all that is
    # printed.
    if code == -1:
        raise Refusal(f"git could not name the {what} of {root} ({NO_ANSWER})")
    answer = out.removesuffix("\n")
    if code != 0 or not answer:
        raise Refusal(f"git could not name the {what} of {root}; run it yourself to see why")
    path = Path(answer)
    return path if path.is_absolute() else root / path


def hooks_dir(root: Path) -> Path:
    """The directory git runs `root`'s hooks from, as git names it: `core.hooksPath` when set.

    Through `git_path`, so a `core.hooksPath` in any bytes is the directory git names, and
    `setup --git-hooks` and `doctor` each read it as that directory.
    """
    return git_path(root, "hooks", "hooks directory")


class GitUnavailable(Failure):
    """`git` could not be run at all, or answered with an error.

    Distinct from "git ran and said no", and the distinction is the whole point of the class.
    A reader that took "could not ask" for "the answer is nothing" tells the user to run
    `drybag attach` when the real fault is their `git` — the state a macOS machine is in when
    `/usr/bin/git` is the Xcode shim with an unaccepted licence, since `GIT_ENV_KEEP` scrubs
    `DEVELOPER_DIR`.

    `cause` is the `GitAnswer.cause` the refusal was built from, so a caller walking several
    checkouts can tell a directory that went away (`Unasked.GONE`) from a fault it has to report.
    """

    def __init__(self, message: str, cause: Unasked | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class Unasked(Enum):
    """Why `git_answer` has no answer. Each value is the sentence a refusal ends with, and ends in
    what to do about it, so a refusal names the cause it found rather than "check that `git` runs
    here" for every one: on a machine at a load average of 140, one `rev-parse` among 65 worktrees
    outlived its five seconds, and the owner was sent to check a `git` that worked.
    """

    TIMED_OUT = (
        "`git` gave this question no answer within its time limit, which a heavily loaded "
        "machine causes; run the command again"
    )
    ENDED = (
        "`git` was ended by a signal before it answered, which a machine out of memory causes; "
        "run the command again"
    )
    GONE = (
        "the directory it was asked in no longer exists, which a worktree removed while the "
        "command ran causes"
    )
    NOT_STARTED = "`git` could not be started here; check that `git` runs here"
    BROKEN = "`git` fails here even for `git --version`; check that `git` runs here"


def _unanswered(asked: _NoAnswer) -> Unasked:
    """The cause a question `_ask` got no answer to is refused for. An input `git` could not be
    given is a `git` never started, and `git_answer` gives none, so it reads as one."""
    return Unasked.TIMED_OUT if asked is _NoAnswer.TIMED_OUT else Unasked.NOT_STARTED


@dataclass(frozen=True)
class GitAnswer:
    """git's one-line answer to one question, in three values: an answer, no answer, or could
    not ask.

    `value` is the answer when there is one. `cause` is set only when `git` could not be asked,
    and says why (`Unasked`). An empty stdout from a successful run, and a refusal from a `git`
    that works, are "no answer", which is a fact about the repository rather than about the
    machine, and carry no cause.
    """

    value: str | None
    cause: Unasked | None = None

    def require(self, refusal: str) -> str | None:
        """The answer, raising `GitUnavailable` rather than answering `None` when `git` could not
        be asked. `refusal` is the caller's, because only the caller knows what went unanswered
        and what that stops; what follows it is the cause, which says what to do."""
        if self.cause is not None:
            raise GitUnavailable(f"{refusal}: {self.cause.value}", self.cause)
        return self.value


def git_answer(root: Path, *args: str) -> GitAnswer:
    """git's one-line answer to `args` asked in `root`, through `_ask`, `git_run`'s own call.

    Scrubbed and decoded losslessly as `git_run` is, so an `origin` URL holding a byte that is not
    UTF-8 is still an answer `attach` can read: the answer is what git
    printed, a path as the filesystem spells it, less its line ending alone — never `strip()`, for
    `answer_lines`'s reason.

    **When there is no answer, why**, read where it is known rather than guessed afterwards. No
    process at all is `_ask`'s own cause: ran out of time, or could not be started. A negative exit
    is a `git` a signal ended — no answer, as `ForkUnknown.of` reads it, and never "git said no",
    which made a checkout's own path the answer for a `git` the OOM killer ended. A positive exit is
    "git declined" or "git is broken", and two questions tell them apart, asked only after it.
    First whether `root` is still a directory: `git -C <gone>` exits 128 for `--version` too, so a
    worktree removed mid-walk read as a broken `git`. Then `git --version` from the same `root`,
    which needs no repository: the Xcode shim with an unaccepted licence exits non-zero for every
    invocation, and a `git` that answers it declined the question. That probe is the only second
    process, and it runs only after `git` has exited on its own.
    """
    asked = _ask(root, *args)
    if isinstance(asked, _NoAnswer):
        return GitAnswer(None, _unanswered(asked))
    code, out = asked
    if code == 0:
        return GitAnswer(out.removesuffix("\n") or None)
    if code < 0:
        return GitAnswer(None, Unasked.ENDED)
    if not fsops.is_dir(root):
        return GitAnswer(None, Unasked.GONE)
    probe = _ask(root, "--version")
    if isinstance(probe, _NoAnswer):
        return GitAnswer(None, _unanswered(probe))
    if probe[0] != 0:
        return GitAnswer(None, Unasked.BROKEN)
    return GitAnswer(None)


# A URL's scheme and the userinfo before the authority's last `@`: `[^/?#]*` stops at the path, so
# an `@` in the path (`…/p@v1`) is not userinfo, and a password holding `@` is taken whole.
_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<userinfo>[^/?#]*)@")


def without_userinfo(url: str) -> str:
    """`url` as it may be printed or recorded: a remote can carry a token as its userinfo
    (`https://user:token@host/…`), and `attach` prints the origin and keeps it on this machine.

    Every scheme loses its userinfo but ssh's, whose user (`ssh://git@host/…`) is no secret and is
    part of how one remote is told from another (`memory.store`'s spellings); there only a
    `:password` goes. The scp form (`git@host:path`) has no scheme and carries no password, so it
    is left as it is."""
    found = _USERINFO.match(url)
    if found is None:
        return url
    scheme, userinfo = found["scheme"], found["userinfo"]
    rest = url[found.end() :]
    if "ssh" in scheme.lower():
        user = userinfo.split(":", 1)[0]
        return f"{scheme}{user}@{rest}" if user else f"{scheme}{rest}"
    return f"{scheme}{rest}"


def repository_name(url: str) -> str:
    """The name a remote `url` gives its repository: the last segment of its path, `:` read as the
    scp form's `/`, lower-cased and with one `.git` taken off. `attach` binds a repository with no
    `drybag.toml` under it by this name, and holds it to the name rule itself."""
    segment = url.rstrip("/").replace(":", "/").rsplit("/", 1)[-1]
    # Lower-cased first, so `Widget.GIT` loses its suffix as `Widget.git` does.
    return segment.lower().removesuffix(".git")


def origin_remote(root: Path) -> str | None:
    """This checkout's `origin` URL, or `None` when `git` ran and there is no such remote.

    Public because `attach` notes it beside the binding and derives a name
    from it, and may not reach for a `subprocess.run` of its own: `git_run` scrubs `GIT_DIR`
    and `GIT_WORK_TREE`, and an inherited one would make the answer one about a different
    repository than the session is in. Two areas asking one question two ways is how they stop
    agreeing.

    A URL in bytes that are not UTF-8 is answered, as the filesystem's codec spells it, and not
    raised: it never equals a URL read out of a TOML file, and `attach`, which would write it into
    one, records it as none. The answer is
    what git printed less its line ending alone, never `strip()`, for `answer_lines`'s reason.

    Raises `GitUnavailable` rather than answering `None` when `git` could not be asked at all.
    "No origin remote" is a fact about the repository and reads as *not this one*, while "could
    not ask" is a fault on this machine, and collapsing them tells the user to run `drybag
    attach` about their own `git`. A non-zero exit is either, so `git_answer` asks a second
    question that needs no repository from the same `root`: git answers "no such remote" with an
    exit of its own, and a broken install exits non-zero for everything.

    The value is repository-authored (principle 5): a clone chooses its own remote URL, so a caller
    that shows it wraps it first.
    """
    return git_answer(root, "remote", "get-url", "origin").require(
        "`git` could not read this repository's origin remote — the fault is on this "
        "machine rather than in the repository"
    )


SHALLOW = (
    "this clone is shallow, so the commits HEAD forked from can be cut off and an older one "
    "stand in for them"
)
DISJOINT = "HEAD and the base share no commit"


@dataclass(frozen=True)
class ForkUnknown:
    """Why the commits HEAD forked from a base at are not known: `cause` in words, and whether
    git answered at all, since a git that gave no answer sends a reader to a different remedy
    than a clone that is shallow or a base it refused."""

    cause: str
    answered: bool

    @classmethod
    def of(cls, code: int) -> ForkUnknown:
        """A question git exited `code` on. Any negative code is no answer: `git_run`'s `-1`,
        and a git a signal ended, which `subprocess` reports as the signal's negative."""
        if code < 0:
            return cls(NO_ANSWER, answered=False)
        return cls(f"git exited {code}", answered=True)


def fork_points(root: Path, base: str) -> list[str] | ForkUnknown:
    """Every commit HEAD forked from `base` at — `git merge-base --all <base> HEAD`, each best
    common ancestor, in git's order — or why they are not known.

    Every one, because a history the change shapes itself can have several, and the one git
    picks alone, the newest by date, is not the fork point in any sense the others are not: a
    reader of one answers about a tree the change can choose. Whether the clone is shallow is
    asked first, and a question git does not answer is not "not shallow": in a shallow clone the
    fork point can be cut off and an older commit be what git names. A shallow clone, one git
    will not answer about, and a base that shares no commit with HEAD are all not known. What
    that means, and what to do about it, each caller says in its own words; the base is refused
    by the caller when it is shaped like an option, before this is asked.
    """
    code, out = git_run(root, "rev-parse", "--is-shallow-repository")
    if code != 0:
        return ForkUnknown.of(code)
    if out.strip() == "true":
        return ForkUnknown(SHALLOW, answered=True)
    code, out = git_run(root, "merge-base", "--all", base, "HEAD")
    forks = out.split()
    if code == 1 and not forks:
        return ForkUnknown(DISJOINT, answered=True)
    if code != 0 or not forks:
        return ForkUnknown.of(code)
    return forks
