"""Stable-code layout issue for preflight / diagnostics (CT-03)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IssueSeverity = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class TextIssue:
    """``code`` is stable (translate from it); ``start``/``end`` are code-point offsets."""

    code: str
    message: str
    start: int = 0
    end: int = 0
    severity: IssueSeverity = "warning"
