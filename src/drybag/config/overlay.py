"""Which overlay root the machine file records: one key of a file the core owns.

The overlay itself is the private layer's, and this module reads nothing of it: only
`[overlay] root`, the machine file's record of where it is, which the note store, `attach`,
and `doctor` all ask for. Beside `config.reading` rather than in
`config.machine`, because `config.reading` is what reads the file (`read_machine_toml`), for this
reader and the owner's layer alike.
"""

from __future__ import annotations

from pathlib import Path

from drybag.config.machine import in_owner_home, machine_config_path
from drybag.config.reading import read_machine_toml


def overlay_root(machine: Path | None) -> Path | None:
    """The overlay root this machine records, or `None` when it records none.

    `None` means **not recorded**, and nothing else: the message the note store prints for it is
    "no overlay root is recorded in the machine configuration; run `drybag setup`", which is
    wrong advice for a broken file.

    So a file that cannot be read or parsed raises `config.reading`'s `MachineConfigError`, in its
    words for the same file (`read_machine_toml`), and the three shapes that genuinely
    record no overlay answer `None`: an absent file (the ordinary state before `setup` has run),
    no `[overlay]` table, and a table with no usable `root`.

    With no file named, the file is the one no variable can move (`machine_config_path`): the
    root anchors where the note store may resolve, and a committed `.claude/settings.json` can
    set a variable in a session no person is watching. A machine with no home
    (`config.machine.owner_home`) has no such file, and records no overlay.
    """
    path = machine_config_path() if machine is None else machine
    raw = None if path is None else read_machine_toml(path)
    if raw is None:
        return None
    section = raw.get("overlay")
    if not isinstance(section, dict):
        return None
    value = section.get("root")
    # A leading `~` is the home this file lives under (`config.machine.owner_home`).
    return in_owner_home(value) if isinstance(value, str) and value else None
