"""How one version orders against another, read the one way every reader of a version does.

`doctor`'s `versions` row and a session's note on a newer configuration point by direction: one
comparison, so the two cannot disagree about which way a recorded version lies. The overlay's
`requires` reader reads a floor through the same component grammar and decides the floor's own
triple by `pre_release`, the reading `later` orders a release after, so a pre-release does not meet
the floor of the release it comes before.

A leaf module: it imports nothing from `drybag`.
"""

from __future__ import annotations

import re

# Nine ASCII digits a component, and both halves of that bound are load-bearing.
#
# *Bounded*, because `int()` is not total. CPython 3.11 caps integer-string conversion at 4300
# digits, so an unbounded `(\d+)` let a manifest declare `>=999…9.0.0` and made `satisfies`
# raise `ValueError` out of a function whose docstring promises `None` for anything it cannot
# read. `doctor` rendered that as "this check could not run: ValueError", and the session
# handler's `except Exception` swallowed it whole — taking `NOT_ATTACHED` and every other line
# of the same result with it, so an overlay manifest silenced the one line that says the
# project is not attached. Nine digits is past every version anyone releases and far under the
# cap, and the value that exceeds it is now unreadable rather than fatal.
#
# *ASCII*, because `\d` is not. It matches every Unicode decimal digit and `int()` accepts
# them, so a floor spelled in Eastern Arabic-Indic numerals validated, compared as `>=1.0.0`,
# and printed back verbatim into a session line and a `doctor` row — `requires_of` returns the
# overlay's own bytes, and "the one owner-authored string that prints" is meant to be a
# version, not an arbitrary numeral system. `tests/test_semver.py` and
# `tests/overlay/test_requires.py` spell the digits.
COMPONENT = r"([0-9]{1,9})"
# A version's leading `X.Y.Z`, whatever follows it: the triple `later` orders versions by first,
# for any caller that compares a version by its release.
VERSION = re.compile(rf"^{COMPONENT}\.{COMPONENT}\.{COMPONENT}")
# What may follow an `X.Y.Z` in a pre-release of it: PEP 440's `a`, `b`, `rc` and `dev` segments
# and their spellings, with an optional separator (`1.0.0rc1`, `1.0.0-rc.1`, `0.2.0.dev0`). The
# whole suffix must be one of them, so `1.0.0rc1.post2` is not read as a pre-release.
_PRE_RELEASE = re.compile(
    r"\A[-_.]?(?:a|alpha|b|beta|c|rc|pre|preview|dev)[-_.]?[0-9]{0,9}"
    r"(?:[-_.]?dev[-_.]?[0-9]{0,9})?\Z",
    re.IGNORECASE,
)


# What PEP 440 lets follow a release's leading `X.Y.Z`: its appendix's pattern less the epoch and
# those three components. Further release components, then at most one pre-release segment (`a`,
# `b`, `rc` and their spellings), one post-release (`post`, `rev`, `r`, or `-N`) and one
# development segment (`dev`), in that order and each with or without a separator before it, then
# a local label after `+`, which is no segment of the version's. Read as one grammar because a
# search for each segment as a word of its own kept missing spellings PEP 440 allows: segments run
# together with no separator (`1.0.0.post1dev2`, `1.0.0rc1post2`) were read as other segments, or
# as none.
_SUFFIX = re.compile(
    r"\A(?P<release>(?:\.[0-9]+)*)"
    r"(?P<pre>[-_.]?(?:alpha|a|beta|b|preview|pre|c|rc)[-_.]?[0-9]*)?"
    r"(?P<post>-[0-9]+|[-_.]?(?:post|rev|r)[-_.]?[0-9]*)?"
    r"(?P<dev>[-_.]?dev[-_.]?[0-9]*)?"
    r"(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?\Z",
    re.IGNORECASE,
)


def pre_release(version: str) -> bool:
    """Whether `version` orders before the release its leading `X.Y.Z` names, by its suffix: the
    one reading of a pre-release here.

    The suffix is read by PEP 440's grammar (`_SUFFIX`), and orders before the release where it
    has a pre-release segment (`1.0.0rc1`, `1.0.0-rc.1`, and `1.0.0rc1.post2`, which comes before
    `1.0.0` as `1.0.0rc1` does), or a development segment with no post-release before it
    (`1.0.0.dev3+g1234abc`), while `1.0.0.post1.dev2`, spelled with or without its separators, is a
    development release of a post-release, which comes after. A further release component other
    than zero names a later release (`1.0.0.1rc1`), and the local label after `+` is no segment
    (`1.0.0+local.rc1` is not one). A suffix outside that grammar, and a version with no leading
    `X.Y.Z`, are no pre-release this module reads: the triple decides.
    """
    found = VERSION.match(version)
    if found is None:
        return False
    parts = _SUFFIX.match(version[found.end() :])
    if parts is None:
        return False
    if parts["release"].replace(".", "").strip("0"):
        return False
    if parts["pre"] is not None:
        return True
    return parts["dev"] is not None and parts["post"] is None


def later(version: str, than: str) -> bool | None:
    """Whether `version` is later than `than`, by the leading `X.Y.Z` and then by what follows it.

    Both are read by their leading three components (`VERSION`), compared as integer tuples,
    which decide whenever they differ, so `1.0.0-rc1` is later than `0.9.9`. `None` when either
    has no leading `X.Y.Z` (`v1.0.0`, an empty string): the direction is unknown, and a caller
    that would move a value in one direction must not guess it. `doctor`'s `versions` row says to
    write such a recorded version by hand.

    **Equal triples are not equal versions.** Read by the triple alone, `later('1.0.0',
    '1.0.0rc1')` was `False`, so a pre-release build moved a project recording the release down
    to the pre-release and re-pinned it. With the triples equal, a bare version is later than one
    carrying a pre-release suffix (`_PRE_RELEASE`: `a`, `b`, `rc`, `dev` and their spellings),
    and one pre-release is never later than the bare release. Any other pair of different
    strings — two pre-releases, or a suffix that is not a pre-release, such as `.post1` or
    `+local`, which is later than the bare version — is `None`: this reader orders no suffix it
    does not have to, and a caller refuses rather than guess. A version is never later than
    itself, so `later(v, v)` is `False` exactly when `v` is readable.
    """
    first = VERSION.match(version)
    second = VERSION.match(than)
    if first is None or second is None:
        return None
    ours = tuple(int(part) for part in first.groups())
    theirs = tuple(int(part) for part in second.groups())
    if ours != theirs:
        return ours > theirs
    suffix, other = version[first.end() :], than[second.end() :]
    if suffix == other:
        return False
    if not suffix and _PRE_RELEASE.match(other):
        return True
    if not other and _PRE_RELEASE.match(suffix):
        return False
    return None
