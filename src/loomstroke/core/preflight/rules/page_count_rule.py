"""Document: page count sanity (CT-16 §5 Document)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["PageCountRule"]


class PageCountRule(PreflightRule):
    rule_id: ClassVar[str] = "document.page_count"
    category: ClassVar[str] = "document"
    default_severity: ClassVar[IssueSeverity] = "info"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        count = len(snapshot.pages)
        if count == 0:
            return (self.issue(profile, "no_pages", severity="fatal"),)
        issues: list[PreflightIssue] = []
        seen: set[str] = set()
        for page in snapshot.pages:
            if page.page_id in seen:
                issues.append(
                    self.issue(profile, "duplicate_page_id", page_id=page.page_id, severity="fatal")
                )
            seen.add(page.page_id)
        if profile.target_kind == "print" and count % 2 == 1:
            issues.append(
                self.issue(
                    profile,
                    "odd_page_count",
                    count=count,
                    recommended_actions=("add_blank_page",),
                )
            )
        return tuple(issues)
