"""Resource: linked assets (CT-18) — not available yet, so ``not_checked``."""

from __future__ import annotations

from typing import ClassVar

from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["LinkedResourceRule"]


class LinkedResourceRule(PreflightRule):
    rule_id: ClassVar[str] = "resource.linked"
    category: ClassVar[str] = "resource"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        del snapshot, profile
        raise CapabilityMissing("linked_assets_not_available")
