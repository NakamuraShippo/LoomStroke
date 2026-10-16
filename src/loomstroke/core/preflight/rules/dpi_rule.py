"""Raster: effective resolution per page (CT-16 §5 Raster / Document)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["DpiRule"]


class DpiRule(PreflightRule):
    rule_id: ClassVar[str] = "raster.dpi"
    category: ClassVar[str] = "raster"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        minimum = float(profile.param("min_dpi", 72.0) or 72.0)
        issues: list[PreflightIssue] = []
        seen: set[tuple[float, float]] = set()
        for page in snapshot.pages:
            dpi = (float(page.dpi[0]), float(page.dpi[1]))
            seen.add(dpi)
            if min(dpi) < minimum:
                issues.append(
                    self.issue(
                        profile,
                        "dpi_below_minimum",
                        page_id=page.page_id,
                        dpi=f"{min(dpi):g}",
                        minimum=f"{minimum:g}",
                        recommended_actions=("image_size",),
                    )
                )
        if len(seen) > 1:
            issues.append(
                self.issue(
                    profile,
                    "dpi_mismatch",
                    severity="info",
                    values=", ".join(f"{d[0]:g}" for d in sorted(seen)),
                )
            )
        return tuple(issues)
