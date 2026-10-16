"""Text: fonts that are not installed (CT-16 §5 Text)."""

from __future__ import annotations

from typing import ClassVar

from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["MissingFontRule"]


class MissingFontRule(PreflightRule):
    rule_id: ClassVar[str] = "text.missing_font"
    category: ClassVar[str] = "text"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        texts = [(page, text) for page in snapshot.pages for text in page.texts]
        if texts and all(text.font_available is None for _page, text in texts):
            raise CapabilityMissing("font_database_unavailable")
        issues: list[PreflightIssue] = []
        reported: set[tuple[str, str]] = set()
        for page, text in texts:
            if text.font_available is not False or text.is_empty:
                continue
            family = ", ".join(text.missing_font_families) or text.font_family
            key = (page.page_id, family)
            if key in reported:
                continue
            reported.add(key)
            issues.append(
                self.issue(
                    profile,
                    "font_missing",
                    page_id=page.page_id,
                    layer_id=text.layer_id,
                    object_id=text.object_id,
                    bounds=text.bounds,
                    family=family or "?",
                    recommended_actions=("install_font", "replace_font"),
                )
            )
        return tuple(issues)
