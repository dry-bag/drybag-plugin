"""Where drybag's own files sit inside a project, derived once from `[paths]`.

Every module asks this one for a derived location rather than spelling it.

A local run's default base is derived here (`local_base`), because the three areas that default
one cannot import each other: the paths are the core's to name, and the core may not import
`attach`; the crossings still pinned are listed in `tests/boundaries/test_delivery.py`.
"""

from __future__ import annotations

from drybag.config.schema import Config


def local_base(config: Config) -> str:
    """The base a local run compares against when none is given: the remote-tracking ref of
    `[project] base_branch`, named in full.

    Named in full because git resolves a short `origin/<b>` through `refs/tags/` first, so a tag of
    that spelling would stand in for the branch. The loader holds the branch to `BRANCH_NAME`, so
    the name is one git can have.
    """
    return f"refs/remotes/origin/{config.project.base_branch}"
