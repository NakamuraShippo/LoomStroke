"""Whole-batch result: per-target records, stop reason, re-run list (CT-20 §14)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .batch_target_record import BatchTargetRecord

__all__ = ["BatchPolicy", "BatchRunResult"]

BatchPolicy = Literal["stop", "continue", "skip_blocked"]


@dataclass(frozen=True, slots=True)
class BatchRunResult:
    policy: BatchPolicy
    records: tuple[BatchTargetRecord, ...] = ()
    cancelled: bool = False
    stopped_reason: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.cancelled
            and not self.stopped_reason
            and all(record.status in {"success", "skipped"} for record in self.records)
        )

    def remaining_targets(self) -> tuple[str, ...]:
        return tuple(record.target_id for record in self.records if record.is_pending)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts

    def outputs(self) -> tuple[str, ...]:
        return tuple(name for record in self.records for name in record.output_basenames())
