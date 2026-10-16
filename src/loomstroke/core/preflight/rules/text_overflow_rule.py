"""Text: area text that does not fit its frame (CT-16 §5 Text, CT-03 diagnostics)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["TextOverflowRule"]


class TextOverflowRule(PreflightRule):
    rule_id: ClassVar[str] = "text.overflow"
    category: ClassVar[str] = "text"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        issues: list[PreflightIssue] = []
        for page in snapshot.pages:
            for text in page.texts:
                if text.overflow is True:
                    issues.append(
                        self.issue(
                            profile,
                            "text_overflow",
                            page_id=page.page_id,
                            layer_id=text.layer_id,
                            object_id=text.object_id,
                            bounds=text.bounds,
                            recommended_actions=("enlarge_frame", "reduce_font_size"),
                        )
                    )
                elif text.overflow is None and text.text_mode == "area":
                    issues.append(
                        self.issue(
                            profile,
                            "text_layout_not_verified",
                            page_id=page.page_id,
                            layer_id=text.layer_id,
                            object_id=text.object_id,
                            bounds=text.bounds,
                            severity="info",
                        )
                    )
        return tuple(issues)
