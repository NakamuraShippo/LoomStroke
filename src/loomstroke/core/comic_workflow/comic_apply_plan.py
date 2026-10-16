"""Per-page editable apply plan (CT-22 §8.1 ``ComicApplyPlan``): revision-guarded, previewable."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ComicApplyPlan"]


@dataclass(frozen=True, slots=True)
class ComicApplyPlan:
    entry_id: str
    page_id: str | None
    base_document_revision: str
    changeset_id: str
    operation_summary: Mapping[str, Any] = field(default_factory=dict)
    operation_count: int = 0
    warnings: tuple[str, ...] = ()
    blocked_reason_codes: tuple[str, ...] = ()

    @property
    def is_ready(self) -> bool:
        return not self.blocked_reason_codes and self.operation_count > 0

    def revision_matches(self, current_revision: str | None) -> bool:
        if not self.base_document_revision or self.base_document_revision == "unknown":
            return True
        return current_revision == self.base_document_revision

    def to_dict(self) -> dict[str, Any]:
        return {
            "entryId": self.entry_id,
            "pageId": self.page_id,
            "baseDocumentRevision": self.base_document_revision,
            "changesetId": self.changeset_id,
            "operationSummary": dict(self.operation_summary),
            "operationCount": int(self.operation_count),
            "warnings": list(self.warnings),
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "ready": self.is_ready,
        }
