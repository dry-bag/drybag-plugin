"""What keeps an agent from writing the trust record itself, which is the harness's to keep.

An approval holds against repository bytes: drybag never writes `trust.json` from them, and a
clone, a pull or a pull request does not reach it, since it lies under the home the password
database records, outside every repository. A repository whose hooks the harness runs (a committed
`.claude/settings.json` hook in a folder the person trusted) is code running as that person, and
writes any file they can, the record included. Against an agent whose shell can write that file,
an approval holds nothing, and drybag neither enforces nor checks anything stronger. What stops
such an agent is the harness's own sandbox, configured by the person: under Claude Code, its
sandbox, which confines what the shell tool writes, and a `permissions.deny` rule over the record's
directory, which the sandboxed shell and the file tools both honour. `setup --approval-boundary`
prints both for the person to set, and writes nothing. This module holds the spellings it prints,
and nothing here reads or writes a file.

Every literal below was measured on Claude Code 2.1.296 (macOS) on 2026-10-16; the record is the
findings at the end of the 2026-10-15 floor, layers and trust plan in `docs/plans/`.

- **The sandbox.** A project's `.claude/settings.json` or `.claude/settings.local.json` setting
  `sandbox.enabled = false` turns a user's `sandbox.enabled = true` off for that project. The
  sandbox is admin-required, and then a repository's `false` is ignored, where
  `sandbox.allowUnsandboxedCommands = false` is set in managed settings or, unless managed settings
  set it `true`, in a file or JSON passed to Claude Code's own `--settings` flag on each start
  (the sandboxing reference; the flag was read there and not measured). The advice names managed
  settings, which hold on every start without a flag.
- **The rule.** `Edit(//<absolute path>/**)` stopped a sandboxed shell write and a file-tool write
  into that directory; one leading slash anchors the path at the working directory and stopped
  neither outside it, and a `Write(...)` rule stopped neither in any spelling.
- **Where an administrator's settings live:** `MANAGED_SETTINGS`.

**A write the sandbox refuses** (`sandbox_refused`): with Claude Code's sandbox on, an agent's shell
writes only the working directory and the temporary directory, so drybag's own writes to the
machine directory -- the trust record, the machine file -- and to the user settings fail there with
a permission error, whether or not the deny rule is in place. Claude Code's sandboxing reference
gives the error for such a write: `Operation not permitted` (`EPERM`) on macOS, and `Read-only file
system` (`EROFS`) on Linux and WSL2. macOS's was also measured here (with `sandbox-exec` on macOS 27
on 2026-10-16: an open and a `mkdir` in a denied directory each raised `PermissionError` `EPERM`);
Linux's was not. So `EPERM` and `EROFS` are read as that refusal, and every writer says so in one
sentence, `SANDBOX_REFUSED`, naming a person outside the agent's sandbox as the one to make the
write. A read-only mount and an immutable flag answer the same, so the two commands a person runs
again, `setup` and the trust record's writers, give the remedy `RUN_OUTSIDE`, which ends with
`OWN_TERMINAL_TOO`, lest a person already in their own terminal be sent there again. A
directory whose mode refuses the user answers `EACCES`, which is not the sandbox, and keeps each
writer's sentence naming the directory.
"""

from __future__ import annotations

import errno
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from drybag import fsops

# Claude Code's managed settings, by `sys.platform`: the file only an administrator writes, whose
# `sandbox` keys a repository's settings cannot override. Measured on Claude Code 2.1.296 (the
# module docstring); Linux's is WSL's too.
MANAGED_SETTINGS: Mapping[str, Path] = MappingProxyType(
    {
        "darwin": Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
        "linux": Path("/etc/claude-code/managed-settings.json"),
    }
)
# What a sentence names in managed settings' place on a platform where none was measured.
NO_MANAGED_PATH = "the managed settings file"
# The user's setting that turns the sandbox on, which a repository's settings can turn off again.
SANDBOX_ON = "sandbox.enabled = true"
# The managed keys of Claude Code's sandboxing reference's recipe for enforcing the sandbox, as a
# person sets them: on, no command run outside it, and no start where it cannot run.
# `failIfUnavailable` is named by the reference and was not measured here.
ENFORCING_SETTINGS = (
    f"{SANDBOX_ON}, sandbox.failIfUnavailable = true and sandbox.allowUnsandboxedCommands = false"
)
# The deny rule over a directory, measured on Claude Code 2.1.296: `Edit(/` and the absolute path,
# whose own leading slash makes the two the measurement needs.
DENY_RULE = "Edit(/{directory}/**)"


def deny_rule(directory: Path) -> str:
    """The `permissions.deny` rule that keeps the sandboxed shell and the file tools out of
    `directory`, an absolute path."""
    return DENY_RULE.format(directory=directory.as_posix())


def record_deny_rules(record: Path) -> tuple[str, ...]:
    """The deny rules over the directory the trust record at `record` is kept in: over it as
    named, and, where a symbolic link makes the two differ, over where it leads, since the harness
    compares a write against the path it resolves.

    As named but absolute: a relative `--machine` names a relative directory, which `deny_rule`
    spelled `Edit(/sub/**)` or `Edit(/./**)`, and one leading slash anchors a rule at the working
    directory, so the rule pasted into user settings denied a subtree of, or the whole of, every
    project and not the record's directory."""
    named = record.parent.absolute()
    return tuple(dict.fromkeys((deny_rule(named), deny_rule(fsops.resolved(named)))))


def managed_named(platform: str | None = None) -> str:
    """This platform's managed-settings file as a sentence names it: its path, or
    `NO_MANAGED_PATH` where none was measured."""
    managed = MANAGED_SETTINGS.get(sys.platform if platform is None else platform)
    return NO_MANAGED_PATH if managed is None else str(managed)


# The errors a sandboxed write fails with (the module docstring cites where each is given).
SANDBOX_ERRNOS = frozenset({errno.EPERM, errno.EROFS})


# The end of the sentence `setup` and the trust record give a refused write (the module docstring).
OWN_TERMINAL_TOO = (
    "; met in a person's own terminal, it is the directory itself that refuses writes, as a "
    "read-only mount or an immutable flag does"
)
# The one sentence for a write an agent's sandbox refused (the module docstring), whoever wrote:
# what did not happen, the directory that refused, and who makes the write instead. Constant once
# filled, since its likely reader is that agent, and speaking of the person, who runs it outside.
SANDBOX_REFUSED = (
    "{outcome}: {directory} refused the write, which Claude Code's sandbox does to every write an "
    "agent's shell makes outside the working directory, so {remedy}"
)
# `SANDBOX_REFUSED`'s remedy for a command a person runs again, which `{command}` names.
RUN_OUTSIDE = (
    "a person must run {command} in their own terminal, outside the agent's sandbox"
    + OWN_TERMINAL_TOO
)


def sandbox_refused(exc: OSError) -> bool:
    """Whether `exc` is the refusal an agent's sandbox gives a write outside the working directory
    (the module docstring says which errors those are)."""
    return exc.errno in SANDBOX_ERRNOS
