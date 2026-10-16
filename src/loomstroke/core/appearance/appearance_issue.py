"""Validation finding for an appearance spec (stable code + parameter path)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["AppearanceValidationIssue"]


@dataclass(frozen=True, slots=True)
class AppearanceValidationIssue:
    code: str
    path: str = ""
    severity: Literal["error", "warning"] = "warning"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"
