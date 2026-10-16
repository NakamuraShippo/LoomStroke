"""Validation / planning finding for the comic workflow (stable code + path)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["ComicWorkflowIssue"]


@dataclass(frozen=True, slots=True)
class ComicWorkflowIssue:
    code: str
    path: str = ""
    severity: Literal["error", "warning"] = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "severity": self.severity}
