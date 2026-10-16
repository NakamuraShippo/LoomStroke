"""Outcome of the export guard (CT-16 §6 / §14)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["GuardDecision"]

GuardStatus = Literal["allow", "confirm_fatal", "stale", "no_report"]


@dataclass(frozen=True, slots=True)
class GuardDecision:
    """``allow``: proceed silently; ``confirm_fatal``: ask before exporting;
    ``stale`` / ``no_report``: proceed but tell the user a re-scan is due."""

    status: GuardStatus
    fatal_count: int = 0
    warning_count: int = 0

    @property
    def requires_confirmation(self) -> bool:
        return self.status == "confirm_fatal"
