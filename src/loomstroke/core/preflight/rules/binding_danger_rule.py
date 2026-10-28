"""Warn on text and semantic balloon bodies inside configured binding margins."""

from __future__ import annotations

from ...binding_danger_geometry import bounds_intersect
from ..capability_missing import CapabilityMissing
from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule


class BindingDangerRule(PreflightRule):
    rule_id = "print.binding_danger"
    category = "print"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if profile.target_kind != "print":
            return ()
        if any(p.binding_danger is not None and not p.hydrated for p in snapshot.pages):
            raise CapabilityMissing("page_content_not_loaded")
        if any(
            p.binding_danger is not None and any(b is None for _, _, b in p.binding_objects)
            for p in snapshot.pages
        ):
            raise CapabilityMissing("binding_bounds_unavailable")
        issues = []
        for page in snapshot.pages:
            if page.binding_danger is None:
                continue
            for object_id, layer_id, bounds in page.binding_objects:
                if bounds is not None and bounds_intersect(bounds, page.binding_danger):
                    issues.append(
                        self.issue(
                            profile,
                            "binding_danger_content",
                            page_id=page.page_id,
                            layer_id=layer_id,
                            object_id=object_id,
                            bounds=bounds,
                            recommended_actions=("move_from_binding",),
                        )
                    )
        return tuple(issues)
