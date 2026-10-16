"""Local validation of a book plan (CT-22 §14: never silently auto-fix)."""

from __future__ import annotations

from typing import ClassVar

from .comic_book_plan import ComicBookPlan
from .comic_workflow_issue import ComicWorkflowIssue

__all__ = ["ComicBookPlanValidator"]


class ComicBookPlanValidator:
    MAX_PANELS_PER_PAGE: ClassVar[int] = 8

    def validate(self, plan: ComicBookPlan) -> tuple[ComicWorkflowIssue, ...]:
        issues: list[ComicWorkflowIssue] = []
        if not plan.pages:
            issues.append(ComicWorkflowIssue("book_no_pages", "pages"))
        if plan.reading_direction not in ("rtl", "ltr"):
            issues.append(ComicWorkflowIssue("reading_direction_invalid", "readingDirection"))
        seen_numbers: set[int] = set()
        for page in plan.pages:
            path = f"pages.{page.entry_id}"
            if page.page_number in seen_numbers:
                issues.append(ComicWorkflowIssue("page_number_duplicate", path))
            seen_numbers.add(page.page_number)
            if not page.panels:
                issues.append(ComicWorkflowIssue("page_no_panels", path))
            if len(page.panels) > self.MAX_PANELS_PER_PAGE:
                issues.append(ComicWorkflowIssue("panel_count_exceeds_max", path))
            if not page.is_linked:
                issues.append(ComicWorkflowIssue("page_document_unlinked", path, "warning"))
            orders = [panel.reading_order for panel in page.panels]
            if len(set(orders)) != len(orders):
                issues.append(ComicWorkflowIssue("panel_reading_order_duplicate", path))
            for panel in page.panels:
                if len(panel.dialogue_refs) != len(panel.dialogue_hashes):
                    issues.append(
                        ComicWorkflowIssue(
                            "panel_dialogue_refs_inconsistent", f"{path}.{panel.panel_id}"
                        )
                    )
                if panel.status == "accepted" and not panel.selected_artifact_id:
                    issues.append(
                        ComicWorkflowIssue(
                            "panel_accepted_without_artifact", f"{path}.{panel.panel_id}"
                        )
                    )
        return tuple(issues)

    @staticmethod
    def is_blocked(issues: tuple[ComicWorkflowIssue, ...]) -> bool:
        return any(issue.is_error for issue in issues)
