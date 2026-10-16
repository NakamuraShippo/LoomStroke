"""Secret-free summary for PCT / MCP (CT-17 §5.3 / §7.2 / §12).

Counts, ids and status keys only: no dialogue text, no assignee labels,
no paths.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .comic_project_snapshot import ComicProjectSnapshot

__all__ = ["ProductionProjectionBuilder"]


class ProductionProjectionBuilder:
    SCHEMA: ClassVar[str] = "loomstroke.comic_project_summary.v1"

    def build(self, snapshot: ComicProjectSnapshot) -> dict[str, Any]:
        pages_by_stage: dict[str, int] = {}
        for entry in snapshot.page_entries.values():
            pages_by_stage[entry.status] = pages_by_stage.get(entry.status, 0) + 1
        review: dict[str, int] = {}
        for dialogue in snapshot.dialogues.values():
            review[dialogue.review_state] = review.get(dialogue.review_state, 0) + 1
        tasks: dict[str, int] = {}
        for task in snapshot.tasks.values():
            tasks[task.state] = tasks.get(task.state, 0) + 1
        counts = snapshot.counts()
        return {
            "schema": self.SCHEMA,
            "projectId": snapshot.project.project_id,
            "revision": snapshot.revision,
            "readingDirection": snapshot.project.reading_direction,
            "counts": counts,
            "pagesByStage": pages_by_stage,
            "dialoguesByReview": review,
            "tasksByState": tasks,
            "unplacedDialogueIds": [d.dialogue_id for d in snapshot.unplaced_dialogues()],
            "documentIds": [link.document_id for link in snapshot.project.document_links],
        }
