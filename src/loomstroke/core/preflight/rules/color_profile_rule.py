"""Color: working ICC profile presence for print targets (CT-16 §5 Color)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["ColorProfileRule"]


class ColorProfileRule(PreflightRule):
    rule_id: ClassVar[str] = "color.icc"
    category: ClassVar[str] = "color"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if profile.target_kind != "print" or snapshot.has_color_profile:
            return ()
        return (
            self.issue(
                profile,
                "color_profile_missing",
                recommended_actions=("assign_color_profile",),
            ),
        )
