"""Print geometry: bleed / trim knowledge per page (CT-16 §5 Print geometry)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["BleedRule"]


class BleedRule(PreflightRule):
    rule_id: ClassVar[str] = "print.bleed"
    category: ClassVar[str] = "print"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if profile.target_kind != "print":
            return ()
        return tuple(
            self.issue(
                profile,
                "bleed_unknown",
                page_id=page.page_id,
                recommended_actions=("set_physical_layout",),
            )
            for page in snapshot.pages
            if not page.has_physical_layout
        )
