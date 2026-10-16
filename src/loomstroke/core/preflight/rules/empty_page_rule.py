"""Raster: pages without any visible content (CT-16 §5 Raster)."""

from __future__ import annotations

from typing import ClassVar

from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["EmptyPageRule"]


class EmptyPageRule(PreflightRule):
    rule_id: ClassVar[str] = "raster.empty_page"
    category: ClassVar[str] = "raster"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if any(not page.hydrated for page in snapshot.pages):
            raise CapabilityMissing("page_content_not_loaded")
        issues: list[PreflightIssue] = []
        for page in snapshot.pages:
            inspected = [layer for layer in page.layers if layer.has_pixels is not None]
            if not inspected and not page.texts:
                issues.append(
                    self.issue(
                        profile, "page_content_not_inspected", page_id=page.page_id, severity="info"
                    )
                )
                continue
            has_text = any(not text.is_empty for text in page.texts)
            if not page.visible_content_layers and not has_text:
                issues.append(self.issue(profile, "empty_page", page_id=page.page_id))
        return tuple(issues)
