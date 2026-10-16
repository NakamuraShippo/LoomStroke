"""Document: page size consistency and minimum size (CT-16 §5 Document / Raster)."""

from __future__ import annotations

from typing import ClassVar

from ..document_preflight_snapshot import DocumentPreflightSnapshot
from ..preflight_issue import IssueSeverity, PreflightIssue
from ..preflight_profile import MangaPreflightProfile
from ..preflight_rule import PreflightRule

__all__ = ["PageSizeRule"]


class PageSizeRule(PreflightRule):
    rule_id: ClassVar[str] = "document.page_size"
    category: ClassVar[str] = "document"
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        if not snapshot.pages:
            return ()
        min_px = int(profile.param("min_page_px", 64) or 64)
        issues: list[PreflightIssue] = []
        sizes: dict[tuple[int, int], int] = {}
        for page in snapshot.pages:
            sizes[(page.width, page.height)] = sizes.get((page.width, page.height), 0) + 1
        # The most common size is the expected one; ties resolve to the first page.
        expected = max(
            sizes.items(),
            key=lambda item: (
                item[1],
                item[0] == (snapshot.pages[0].width, snapshot.pages[0].height),
            ),
        )[0]
        for page in snapshot.pages:
            if page.width < min_px or page.height < min_px:
                issues.append(
                    self.issue(
                        profile,
                        "page_too_small",
                        page_id=page.page_id,
                        severity="fatal",
                        width=page.width,
                        height=page.height,
                        minimum=min_px,
                    )
                )
            if (page.width, page.height) != expected and page.role != "spread":
                issues.append(
                    self.issue(
                        profile,
                        "page_size_mismatch",
                        page_id=page.page_id,
                        width=page.width,
                        height=page.height,
                        expected_width=expected[0],
                        expected_height=expected[1],
                        recommended_actions=("image_size",),
                    )
                )
        return tuple(issues)
