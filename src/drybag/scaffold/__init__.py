"""The scaffold engine: manifest, regions, keyed entries, plan and apply.

Everything a consumer needs is re-exported here, including the primitives other areas reach for
directly, such as `drop`. The three refusals a consumer has to catch by name are here too: a caller
that cannot import `ManifestError`, `RegionError` or `EntriesError` from this list has no way to
tell a bad merge from a bug except by catching `Refusal` whole; `ParserLimitError`, an
`EntriesError` too, is the valid JSON past a limit of the parser that `attach` must tell from a
malformed document. Importing a private module of this package from another area is a review
finding; if an area needs something this list does not carry, the list grows deliberately.
"""

from drybag.scaffold.engine import (
    apply,
    plan,
)
from drybag.scaffold.entries import (
    EntriesError,
    ParserLimitError,
    mark,
    marker_id,
    settings_object,
    settings_text,
)
from drybag.scaffold.manifest import (
    FORMAT,
    MANIFEST_PATH,
    Location,
    ManifestError,
    Record,
    digest,
)
from drybag.scaffold.model import Action, Applied, Plan, Refused, Template, Verb
from drybag.scaffold.regions import RegionError, drop, extract, upsert
from drybag.scaffold.report import render_report

__all__ = [
    "FORMAT",
    "MANIFEST_PATH",
    "Action",
    "Applied",
    "EntriesError",
    "Location",
    "ManifestError",
    "ParserLimitError",
    "Plan",
    "Record",
    "Refused",
    "RegionError",
    "Template",
    "Verb",
    "apply",
    "digest",
    "drop",
    "extract",
    "mark",
    "marker_id",
    "plan",
    "render_report",
    "settings_object",
    "settings_text",
    "upsert",
]
