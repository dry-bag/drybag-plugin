"""The trust package's import surface: the machine's one record of approvals.

No command lists or withdraws an approval; withdrawing one is a hand edit of `trust.json`. The
`memory` area keeps its reviewed notes in the record under kind `reviewed`, with no approval kind
registered for it: it reads
`recorded`, with the `Approval` it answers in and the `UnreadableTrust` it refuses with, and
`memory promote` writes through `approve_all`. A consumer that needs another reader or writer
grows this list in a commit that names it.

`setup --approval-boundary` prints what keeps an agent's shell out of the record, and writes none
of it (`boundary`): `record_deny_rules` over the directory `record_path` names, `SANDBOX_ON` for
the user settings, and `ENFORCING_SETTINGS` for `managed_named`'s file. `sandbox_refused` is how
`setup` tells a write an agent's sandbox refused; `SANDBOX_REFUSED` is the one sentence every
writer gives such a write, `setup`'s as the record's, and `RUN_OUTSIDE` the remedy it gives in it.
"""

from __future__ import annotations

from drybag.trust.boundary import (
    ENFORCING_SETTINGS,
    RUN_OUTSIDE,
    SANDBOX_ON,
    SANDBOX_REFUSED,
    managed_named,
    record_deny_rules,
    sandbox_refused,
)
from drybag.trust.record import (
    Approval,
    UnreadableTrust,
    approve_all,
    record_path,
    recorded,
)

__all__ = [
    "ENFORCING_SETTINGS",
    "RUN_OUTSIDE",
    "SANDBOX_ON",
    "SANDBOX_REFUSED",
    "Approval",
    "UnreadableTrust",
    "approve_all",
    "managed_named",
    "record_deny_rules",
    "record_path",
    "recorded",
    "sandbox_refused",
]
