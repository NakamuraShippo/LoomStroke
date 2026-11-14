"""Text: font size below the profile minimum (CT-16 §5 Text)."""

from __future__ import annotations

from typing import ClassVar

from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["MinFontSizeRule"]


class MinFontSizeRule(PreflightRule):
    rule_id: ClassVar[str] = "text.min_font_size"
    category: ClassVar[str] = "text"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if any(not page.hydrated for page in snapshot.pages):
            raise CapabilityMissing("page_content_not_loaded")
        minimum = float(profile.param("min_font_pt", 6.0) or 6.0)
        return tuple(
            self.issue(
                profile,
                "font_size_below_minimum",
                page_id=page.page_id,
                layer_id=text.layer_id,
                object_id=text.object_id,
                bounds=text.bounds,
                size=f"{text.font_size_pt:g}",
                minimum=f"{minimum:g}",
                recommended_actions=("increase_font_size",),
            )
            for page in snapshot.pages
            for text in page.texts
            if not text.is_empty and text.font_size_pt < minimum
        )
