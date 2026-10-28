"""Warn on bounds reaching trim without reaching the outer bleed edge."""

from __future__ import annotations

from ...bleed_geometry import bleed_short_sides
from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule


class BleedCoverageRule(PreflightRule):
    rule_id = "print.bleed_coverage"
    category = "print"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if profile.target_kind != "print":
            return ()
        issues, incomplete = [], False
        for page in snapshot.pages:
            if page.trim_bounds is None:
                continue
            incomplete = incomplete or not page.hydrated
            if page.bleed_layers is None:
                incomplete = True
                continue
            for layer in page.bleed_layers:
                if not layer.visible or layer.opacity <= 0:
                    continue
                if not layer.bleed_bounds_checked:
                    incomplete = True
                    continue
                if layer.bleed_content_bounds is None:
                    continue
                sides = bleed_short_sides(
                    layer.bleed_content_bounds, page.trim_bounds, (page.width, page.height)
                )
                if sides:
                    x, y, width, height = layer.bleed_content_bounds
                    issues.append(
                        self.issue(
                            profile,
                            "bleed_coverage_short",
                            page_id=page.page_id,
                            layer_id=layer.layer_id,
                            bounds=(x, y, x + width, y + height),
                            sides=",".join(sides),
                            recommended_actions=("extend_to_bleed",),
                        )
                    )
        if incomplete:
            raise CapabilityMissing("bleed_bounds_unavailable", issues=tuple(issues))
        return tuple(issues)
