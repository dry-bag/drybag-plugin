"""The note store a delivery row in `drybag doctor` reads, resolved at most once per area per
report.

`Answers` is what a delivery area's `doctor.py` creates in its `register()`, for its own rows, and
`register()` is called once per report, so nothing one run resolved reaches the next — the suite
runs many reports in one process. Each area that reads the store creates its own, so the store is
resolved once for each such area in a report and not once for the report: `memory`'s two rows
share one, and `attach`'s row has its own. Sharing one between areas would need a value both
`register()` calls reach, and the only value the report hands every area is its `Context`, which
is the core's and may hold nothing of this area. In overlay mode a resolution asks `git` for the
checkout's `origin`, so the count of resolutions is a count of launches, and
`tests/test_install_path.py` pins it. The overlay root is not here: it is a key of the machine
file, which the core owns, so the report resolves it once for every area (`Context.overlay_root`).

A module of its own rather than a name in `memory/doctor.py`, which discovery imports and which is
for that alone: `memory/api.py` publishes `Answers`, and every importer of that surface would
otherwise load this area's doctor rows with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drybag.errors import resolved_or_none
from drybag.memory.store import Store, resolve

if TYPE_CHECKING:
    from drybag.doctor.api import Context


class Answers:
    """The note store this project resolves, for one area's rows in one report, resolved on first
    use and never again.

    A one-element tuple is an answer and `None` is "not asked yet", so an answer that is itself
    `None` — no store — is remembered rather than asked again.
    """

    def __init__(self) -> None:
        self._store: tuple[Store | None] | None = None

    def store(self, context: Context) -> Store | None:
        """The note store this project resolves to, or `None` when it does not resolve.

        A resolution that fails is `None` (`errors.resolved_or_none`): a `drybag.toml` that
        makes the store refuse is a row's skip, never "this check could not run", which is red
        and gates the exit code for something the repository did not do wrong.
        """
        if self._store is None:
            # `attach_config`: a checkout attached with no `drybag.toml` resolves the store
            # `attach` composed.
            config = context.attach_config
            # With no project configuration there is no store to resolve.
            self._store = (
                None
                if config is None
                else resolved_or_none(
                    lambda: resolve(context.root, config, machine=context.machine)
                ),
            )
        return self._store[0]
