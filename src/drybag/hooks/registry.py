"""Every `drybag.<area>.hooks.register()` result, in area-name order, with no shared list.

Nothing keys or de-duplicates handlers by their own name: two areas may register the same
handler name and both run. What discovery does enforce is the event vocabulary, so a mistyped
`event` fails loudly here instead of shipping a guard that never fires.
"""

from __future__ import annotations

from drybag.areas import area_modules
from drybag.errors import Refusal
from drybag.hooks.api import EVENTS, Handler


class UnknownHookEvent(Refusal):
    """A handler registered for an event name `api.EVENTS` does not carry."""


def discover() -> list[Handler]:
    handlers: list[Handler] = []
    for module in area_modules("hooks"):
        for handler in module.register():
            if handler.event not in EVENTS:
                raise UnknownHookEvent(
                    f"{module.__name__} registered handler {handler.name!r} for event "
                    f"{handler.event!r}, which no harness emits; "
                    f"known events: {', '.join(EVENTS)}"
                )
            handlers.append(handler)
    return handlers
