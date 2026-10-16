"""Outcome of a cancel request against a submitted preview / inpaint job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Sam3CancelResult"]


@dataclass(frozen=True, slots=True)
class Sam3CancelResult:
    ok: bool
    status: str
    reason_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "status": self.status, "reasonCode": self.reason_code}
