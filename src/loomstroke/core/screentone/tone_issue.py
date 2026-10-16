"""Stable-code issue raised by screentone validation (CT-02)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

IssueSeverity = Literal["warning", "error"]


@dataclass(frozen=True, slots=True)
class ToneIssue:
    """The GUI / preflight translate from ``code``; ``message`` is for logs."""

    code: str
    message: str
    severity: IssueSeverity = "warning"
