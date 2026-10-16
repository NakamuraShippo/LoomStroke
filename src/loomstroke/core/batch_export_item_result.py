"""Per-item outcome of a multi-page export (CT-10 spec §9)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loomstroke.io.export_contract import ExportIssue

ItemStatus = Literal["success", "warning", "failed", "skipped", "cancelled"]


@dataclass(frozen=True, slots=True)
class BatchExportItemResult:
    """One page: where it went (or ``None``), how it ended, and its issues."""

    page_id: str
    destination: Path | None
    status: ItemStatus
    issues: tuple[ExportIssue, ...] = ()

    @property
    def file_name(self) -> str:
        return self.destination.name if self.destination is not None else ""

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)
