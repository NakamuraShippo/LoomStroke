"""One panel of the book plan: dialogue refs (ids + hashes, never text), status, candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, ClassVar, Literal

__all__ = ["ComicPanelWorkItem", "PanelStatus"]

PanelStatus = Literal["planned", "generating", "review", "accepted", "rejected", "blocked"]


@dataclass(frozen=True, slots=True)
class ComicPanelWorkItem:
    panel_id: str
    reading_order: int
    dialogue_refs: tuple[str, ...] = ()
    dialogue_hashes: tuple[str, ...] = ()
    summary_hash: str = ""
    status: PanelStatus = "planned"
    candidate_artifact_ids: tuple[str, ...] = ()
    selected_artifact_id: str | None = None
    qa_state: str = "pending"

    STATUSES: ClassVar[tuple[str, ...]] = (
        "planned",
        "generating",
        "review",
        "accepted",
        "rejected",
        "blocked",
    )

    def with_status(self, status: PanelStatus) -> ComicPanelWorkItem:
        return replace(self, status=status)

    def with_candidates(self, artifact_ids: tuple[str, ...]) -> ComicPanelWorkItem:
        return replace(self, candidate_artifact_ids=tuple(artifact_ids), status="review")

    def with_selection(self, artifact_id: str | None) -> ComicPanelWorkItem:
        status: PanelStatus = "accepted" if artifact_id else self.status
        return replace(self, selected_artifact_id=artifact_id, status=status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "panelId": self.panel_id,
            "readingOrder": int(self.reading_order),
            "dialogueRefs": list(self.dialogue_refs),
            "dialogueHashes": list(self.dialogue_hashes),
            "summaryHash": self.summary_hash,
            "status": self.status,
            "candidateArtifactIds": list(self.candidate_artifact_ids),
            "selectedArtifactId": self.selected_artifact_id,
            "qaState": self.qa_state,
        }
