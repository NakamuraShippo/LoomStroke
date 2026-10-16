"""Aggregate outcome of a multi-page export (CT-10 spec §9 / §16)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .batch_export_item_result import BatchExportItemResult

JobStatus = Literal["completed", "partial_failure", "cancelled", "failed"]


@dataclass(frozen=True, slots=True)
class BatchExportResult:
    """Items in plan order plus the job-level status derived from them."""

    items: tuple[BatchExportItemResult, ...] = field(default_factory=tuple)

    @property
    def status(self) -> JobStatus:
        statuses = {item.status for item in self.items}
        written = {"success", "warning"}
        if "cancelled" in statuses:
            return "cancelled"
        if "failed" in statuses:
            return "partial_failure" if statuses & written else "failed"
        return "completed"

    def names(self, *statuses: str) -> list[str]:
        wanted = set(statuses)
        return [item.file_name for item in self.items if item.status in wanted and item.file_name]

    def count(self, *statuses: str) -> int:
        wanted = set(statuses)
        return sum(1 for item in self.items if item.status in wanted)

    @property
    def exported(self) -> list[str]:
        return self.names("success", "warning")

    @property
    def failed(self) -> list[str]:
        return self.names("failed")

    @property
    def skipped(self) -> list[str]:
        return self.names("skipped", "cancelled")

    @property
    def warnings(self) -> list[str]:
        return self.names("warning")

    def issue_codes(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            for code in item.issue_codes:
                counts[code] = counts.get(code, 0) + 1
        return counts
