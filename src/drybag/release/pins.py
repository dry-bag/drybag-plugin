"""What a release is called.

`tag_for` is how a release is called: the repository's own release check (`scripts/release.py`)
holds a tag to the version the tree carries by it, and `scripts/publish_plugin.py` holds the tag's
`plugin.json` version to the tag by it, so none of them can disagree about a release's name.
"""

from __future__ import annotations

PACKAGE = "drybag"


def tag_for(version: str) -> tuple[str, str]:
    """The two tags one release carries: the workflow's `vX.Y.Z` and the platform's own."""
    return f"v{version}", f"{PACKAGE}--v{version}"
