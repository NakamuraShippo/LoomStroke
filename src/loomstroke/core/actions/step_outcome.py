"""Execution outcome of one planned step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["StepOutcome", "StepRunStatus"]

StepRunStatus = Literal["success", "failed", "skipped", "blocked", "cancelled"]


@dataclass(frozen=True, slots=True)
class StepOutcome:
    step_id: str
    action_id: str
    status: StepRunStatus
    reason_code: str = ""
    detail: str = ""
    outputs: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "success"
