"""The grammar a `[paths]` value must match, read by the configuration, which refuses a value
outside it, and by `printed`, which prints a name by it.

A leaf that imports nothing from the package, so both read it and neither imports the other for it.
In `config.schema`, beside the configuration's other grammars, it made the configuration,
`findings` and `printed` import one another, since the configuration prints through `findings`.
"""

from __future__ import annotations

import re

# The grammar a `[paths]` value must match before it may be printed anywhere; `contained()`
# decides whether it may be written, and a shape rule cannot bound a charset.
#
# The segment shape is part of the grammar because the two readers of a path have to agree about
# what a path is. A charset alone admitted `docs//x.md`, `docs/x/` and `./docs`: `contained()`
# read them through `Path(relative).parts`, which drops an empty component, a trailing slash and
# a leading `./` without a word, so `plan()` reported no refusal — while `fsops` splits the raw
# string and refuses all three, so `apply()` raised part-way through a pass that had already put
# earlier artifacts on disk and whose `finally` had already persisted the manifest. The value
# was never writable; only the two spellings of "a path" disagreed about saying so.
#
# So the grammar says it of every segment: exactly one `/` between segments, no segment empty, and
# no segment that is `.` or `..`, which the charset alone cannot say. `.` and `..` are spelled
# entirely out of the charset the segments already use, so a segment rule without that lookahead
# admits `./docs`, `docs/../x` and `..` itself — measured, on the charset-plus-segments form this
# started from. `.hidden` and `..foo` are ordinary names and stay admitted: the lookahead refuses
# a segment that is one or two dots *and nothing else*. What is left is exactly the set
# `fsops.checked_components` accepts, intersected with the charset, and `contained()` asks that
# function for the component rule rather than keeping a second copy of it.
#
# The rules are lookaheads over the whole value, then the charset, and no group is repeated: `re`
# keeps a record for every pass of a repeated group it might give back, 62 MiB over a value of
# half a million segments, and a possessive repeat of the segment, which keeps none, admitted
# `docs/` and `docs/..` on Python 3.11.0 to 3.11.4, which end a failed pass of it where the pass
# stopped (CONTRIBUTING.md, "Tests"). Each lookahead scans the value once.
PATH_VALUE = re.compile(
    # A first segment, which opens on neither `/` nor `-`; no empty segment after it; no segment
    # that is `.` or `..`; and the charset.
    r"^(?![/-])"
    r"(?!.*//)(?!.*/\Z)"
    r"(?!(?:.*/)?\.\.?(?:/|\Z))"
    r"[A-Za-z0-9._/-]+\Z"
)
