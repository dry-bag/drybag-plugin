"""What the overlay is called, directory by directory — names and nothing else.

A leaf module on purpose. `attach` needs to know where a project's record and a project's
notes sit inside the overlay, and it must be able to ask without importing a command module or
the scaffold engine; keeping the names here is what makes that possible.

`COMMON` and `PROJECTS` are **not** redefined here: they come from `drybag.config.plates`,
the core's one spelling of the overlay's directories, which `memory.store` and
`config.overlay_layers` read too.
"""

from __future__ import annotations

from drybag.config.plates import COMMON, PROJECTS

COMMON_MEMORY = f"{COMMON}/memory"
COMMON_CLAUDE = f"{COMMON}/claude"
COMMON_CODEX = f"{COMMON}/codex"
# The overlay's identity record, at its root: what makes a tree a drybag overlay
# (`identity.overlay_fault`), and where the floor `requires.requires_of` reads is declared. It
# replaced the plugin manifests the overlay carried while it was a plugin of its own.
IDENTITY_RECORD = "drybag-overlay.toml"
# The overlay's commit-time secret scan, and the hook `pre-commit install` writes for it: `setup
# --overlay` installs the hook when it makes or records the overlay, and `doctor`'s `pre-commit` row
# asks whether it is there. The hook's *name* only: where it lives is `gitenv.hooks_dir`'s answer
# and never `.git/hooks`, because an overlay with `core.hooksPath` set -- a common global dotfiles
# setting -- or one that is a worktree or a submodule, where `.git` is a file, keeps its hooks
# somewhere else entirely. A hardcoded path would find the scan missing and warn in `doctor` with a
# remedy that cannot clear it. `docs/cli.md`'s `setup --git-hooks` section states the rule: `git
# rev-parse --git-path hooks`, never `core.hooksPath`.
PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"
PRE_COMMIT_HOOK = "pre-commit"

# The two files an overlay carries that can grant a capability, and the reason `overlay upgrade`
# has a decision list at all: an upgrade diffs these and asks about them "regardless of hash",
# because a hash match is not consent for a permission or a hook entry. Named once, unpacked
# into OVERLAY_FILES below, and published as CAPABILITY_FILES: one spelling, so a rename here is
# a rename everywhere, where a list derived by filtering OVERLAY_FILES against a second spelling
# of the two names would let the two drift apart.
CAPABILITY_NAMES = (f"{COMMON_CLAUDE}/permissions.json", f"{COMMON_CLAUDE}/hooks.json")

# Every file `templates/overlay/` ships, in the order a render writes them. The list and the tree
# are two statements of one thing: `tests/overlay/test_template.py` asserts each way round, so a
# file deleted from the tree and a file added to it without a line here are both caught rather than
# one of them. The identity record is last, because a tree is an overlay exactly when it parses, so
# a render cut short before it is never one. A clone is written in the order git takes, which this
# list does not choose, so a clone cut short can carry the record and not everything else.
OVERLAY_FILES = (
    f"{COMMON_MEMORY}/_README.md",
    *CAPABILITY_NAMES,
    f"{COMMON_CODEX}/common.rules",
    f"{PROJECTS}/README.md",
    PRE_COMMIT_CONFIG,
    ".github/workflows/scan.yml",
    ".github/dependabot.yml",
    ".gitignore",
    "README.md",
    IDENTITY_RECORD,
)

CAPABILITY_FILES = CAPABILITY_NAMES
