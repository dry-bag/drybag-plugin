"""Refresh an overlay after a release, and name the two files nobody may refresh silently.

The refresh itself is the scaffold engine's, unchanged: an untouched skeleton file is updated,
a hand-edited one is skipped and named, and the oracle is the digest the manifest recorded. An
overlay is where the owner's own rules live, so a silent overwrite here destroys the only copy
of something — which is exactly why this module calls the engine rather than reimplementing the
rule.

**Why a wrapper type rather than a bare `Plan`.** An upgrade gives two files an exception to the
hash rule: `common/claude/permissions.json` and `common/claude/hooks.json` are named *regardless
of hash*, because they are the two that can grant capability and a hash match is not consent
for those. The scaffold engine's `Plan` has no verb for "needs a decision", and that contract is
shared by every area that scaffolds, so the answer is a type *around* the plan rather than a new
`Verb` inside it.

**What `decisions` actually is, stated plainly because an earlier account of it was not.** The
write is **unconditional**: `apply()` runs before `decisions` is computed, so there is no moment
at which a decision could be taken and nothing here waits on one. `decisions` is a *post-hoc
notice* — these two files were refreshed, go and look at them — and not a gate.

That is safe for exactly one reason, and it is a property of the shipped template rather than of
this function: the template cannot grant anything. `common/claude/permissions.json` ships a
`permissions` table with no rule in it at all and `common/claude/hooks.json` is `{"hooks": {}}`,
held by `tests/overlay/test_template.py::test_the_template_ships_no_allow_rule_anywhere`,
`::test_the_template_ships_permissions_that_are_neither_granted_nor_pretended` and
`::test_the_template_ships_no_hook_entry`, the first with a mutation behind it. So refreshing
either file can widen nothing, and a notice is enough.

The permissions file used to ship two `deny` rules, and this paragraph used to call it
"deny-only" as though that were the reason it was inert. It was not: `attach.permissions`
reads `permissions.allow` and never `permissions.deny`, so those two rules reached nothing, and
the live copy of that protection is `presets/recommended.toml`'s `[deny] global`, which `setup`
merges into `<home>/.claude/settings.json`. The file is inert because it grants nothing, which
is the property this argument actually needs.

**It stops being enough the day a third capability file lands** — or the day one of those two
ships a non-empty body. A change that adds one has to move the ask in front of `apply()`, or give
the caller a way to decline; this paragraph is the record that the current shape depends on the
template being inert, and not on the order of the two statements below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from drybag.overlay.identity import require_overlay
from drybag.overlay.layout import CAPABILITY_FILES, OVERLAY_FILES
from drybag.overlay.template import templates
from drybag.scaffold import Plan, apply, plan

# What `--root` has to name, said once. `--root` defaults to `.`, so the directory this command
# is pointed at is ordinarily the one the agent happens to be sitting in.
# The count is `len(OVERLAY_FILES)` and not a word, for the reason `layout.py` gives about
# `CAPABILITY_NAMES`: one spelling, so a change that ships a file ships the sentence with it. A
# hand-written "fifteen" outlived the fifteenth file by exactly one release.
NOT_AN_OVERLAY = (
    f"`drybag overlay upgrade --root` must name an overlay. It refreshes an overlay's own "
    f"{len(OVERLAY_FILES)} files — its identity record, its secret scan's configuration and a "
    f"GitHub Actions workflow among them — so pointed at anything else it creates them there "
    f"instead"
)


@dataclass(frozen=True)
class OverlayUpgrade:
    plan: Plan
    decisions: tuple[str, ...]


def upgrade(root: Path, *, dry_run: bool) -> OverlayUpgrade:
    """What a release would change in this overlay, and which files the caller is told to read.

    **`root` is checked to be an overlay before anything is planned, let alone written.** It was
    checked nowhere: in a directory holding a `README.md` and a `src/main.py`, `overlay upgrade
    --root .` created every shipped file but one — both manifests, `hooks/hooks.json`,
    `common/**`, `.gitignore` and `.github/workflows/scan.yml` among them; `README.md` was
    skipped only because that directory had one — reported them as work done and exited 0.
    (The measurement was taken when the tree shipped fifteen files; `OVERLAY_FILES` is the
    count today, which is why the refusal above derives it rather than spelling it.) Writing
    a workflow file into a repository the owner may then commit is the concrete harm, and
    `--root` defaulting to `.` is what made it a plausible typo rather than an exotic one. The
    probe is `identity.require_overlay`, the same one `setup --overlay` records a root through:
    one question, asked one way, in the one module that knows what an overlay is.

    Only the files this release ships are planned; one an earlier release shipped is left alone.

    `decisions` is computed after `apply()` and names the two capability files unconditionally;
    it is a notice, not a gate. The module docstring says why that is safe here and what would
    make it unsafe.
    """
    require_overlay(root, because=NOT_AN_OVERLAY)
    shipped = templates()
    planned = plan(root, shipped)
    if not dry_run:
        apply(root, planned)
    decisions = tuple(item.id for item in shipped if item.id in CAPABILITY_FILES)
    return OverlayUpgrade(planned, decisions)
