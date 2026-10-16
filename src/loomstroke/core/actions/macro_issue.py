"""One validation / planning finding with a stable code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["MacroIssue"]


@dataclass(frozen=True, slots=True)
class MacroIssue:
    code: str
    step_id: str = ""
    message: str = ""
    severity: Literal["error", "warning"] = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"
