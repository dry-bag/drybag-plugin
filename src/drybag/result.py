"""What a command returns; the frame turns it into one line or one JSON object."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
