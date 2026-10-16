"""Secret-free per-target result of a batch run (CT-20 §8.1 ``BatchRunRecord``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .macro_run_result import MacroRunResult

__all__ = ["BatchTargetRecord", "BatchTargetStatus"]

BatchTargetStatus = Literal[
    "success", "partial", "failed", "blocked", "skipped", "cancelled", "not_started"
]


@dataclass(frozen=True, slots=True)
class BatchTargetRecord:
    target_id: str
    status: BatchTargetStatus
    reason_code: str = ""
    run: MacroRunResult | None = None

    @property
    def is_pending(self) -> bool:
        return self.status in {"not_started", "cancelled"}

    def output_basenames(self) -> tuple[str, ...]:
        return self.run.outputs() if self.run is not None else ()
