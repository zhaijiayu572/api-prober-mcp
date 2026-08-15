"""Stable error envelopes used by all public tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProberError(Exception):
    """An expected, safe-to-return tool failure."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    next_action: str = "Check the request and try again."

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "next_action": self.next_action,
        }
