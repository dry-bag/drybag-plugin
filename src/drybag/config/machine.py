"""Where the machine-level configuration lives; the file is optional.

**Neither variable that could name this file is read, and for one reason.** A committed
`.claude/settings.json` may carry an `env` block, which applies without a trust prompt in a
non-interactive session and can set `DRYBAG_CONFIG`; Claude Code ignores `XDG_CONFIG_HOME`
there (below), but direnv, mise or a devcontainer can set it from a file the repository commits.
So a repository able to redirect this path would declare its own overlay root and its own
pre-recorded trust hash — the two anchors that locating the note store and trusting in-repo
notes by hash rest on.

`DRYBAG_CONFIG` was once gated and `XDG_CONFIG_HOME` was not, which left the gate worth nothing:
the two variables reach the same file, and the second one costs a repository exactly one extra
path segment (`<dir>/drybag/config.toml` rather than the file itself). Gating one of a pair
of equivalent inputs is not a partial defence, it is a redirect with a longer name, so the
rule is the variable-independent one: **this file is `~/.config/drybag/config.toml` and
nothing else**, for every command and from a terminal too, so the file a person writes is the
file a hook reads; `--machine` names another, a path a person typed.

That is the XDG specification's own answer for an unset `XDG_CONFIG_HOME`, so a machine owner
who sets one really does lose it rather than getting a wrong answer quietly, and
`drybag doctor`'s `ignored-env` row says so.

**`~` is `HOME`**, as the harness and every other program on the machine read it, so a
devcontainer, a home-manager setup or a test's scratch `HOME` keeps drybag and the harness
under one home. An environment that sets `HOME` already runs code as this user, so it holds this
file whichever home drybag read.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

DEFAULT_CONFIG_DIR = Path(".config")


def passwd_home() -> Path | None:
    """This process's user's home directory as the password database records it, or `None` for a
    user the database does not list; read by `doctor` alone, to say where it differs from `HOME`."""
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return None


def owner_home() -> Path | None:
    """The machine owner's home directory: `HOME`, or the password database's entry where `HOME`
    is unset (`Path.home`). `None` where `HOME` is empty, which names no home and which
    `Path.home` reads as `/`; where it is relative, which would name a home under whatever
    directory a command runs from, a clone's on the hook path; and where neither names one."""
    value = os.environ.get("HOME")
    if value is not None and not os.path.isabs(value):
        return None
    try:
        return Path.home()
    except RuntimeError:
        return None


def in_owner_home(value: str) -> Path | None:
    """`value` with a leading `~` read as `owner_home`, or `None` when it begins with `~` and
    there is no such home, rather than a `~` left to be read as a directory name."""
    if value == "~" or value.startswith("~/"):
        home = owner_home()
        return None if home is None else home / value[2:]
    return Path(value).expanduser()


def machine_config_path() -> Path | None:
    """The machine configuration file under `owner_home`, or `None` where there is no home."""
    home = owner_home()
    if home is None:
        return None
    return home / DEFAULT_CONFIG_DIR / "drybag" / "config.toml"
