"""Text: empty text objects left on a page (CT-16 §5 Text)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["EmptyTextRule"]


class EmptyTextRule(PreflightRule):
    rule_id: ClassVar[str] = "text.empty"
    category: ClassVar[str] = "text"
    default_severity: ClassVar[IssueSeverity] = "info"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        return tuple(
            self.issue(
                profile,
                "empty_text",
                page_id=page.page_id,
                layer_id=text.layer_id,
                object_id=text.object_id,
                bounds=text.bounds,
                recommended_actions=("delete_object",),
            )
            for page in snapshot.pages
            for text in page.texts
            if text.is_empty
        )
