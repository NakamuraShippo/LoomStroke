"""One page of the book plan: its panel work items, document link and status."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from .comic_panel_work_item import ComicPanelWorkItem

__all__ = ["ComicPageWorkItem", "PageStatus"]

PageStatus = Literal["planned", "generating", "review", "ready", "applied", "blocked"]


@dataclass(frozen=True, slots=True)
class ComicPageWorkItem:
    entry_id: str
    page_number: int
    panels: tuple[ComicPanelWorkItem, ...] = ()
    page_id: str | None = None
    document_id: str | None = None
    role: str = "body"
    status: PageStatus = "planned"
    dependencies: tuple[str, ...] = ()
    script_hash: str = ""

    @property
    def is_linked(self) -> bool:
        return bool(self.page_id)

    def panel(self, panel_id: str) -> ComicPanelWorkItem | None:
        return next((p for p in self.panels if p.panel_id == panel_id), None)

    def with_panel(self, panel: ComicPanelWorkItem) -> ComicPageWorkItem:
        panels = tuple(panel if p.panel_id == panel.panel_id else p for p in self.panels)
        return replace(self, panels=panels)

    def with_status(self, status: PageStatus) -> ComicPageWorkItem:
        return replace(self, status=status)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for panel in self.panels:
            counts[panel.status] = counts.get(panel.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "entryId": self.entry_id,
            "pageNumber": int(self.page_number),
            "pageId": self.page_id,
            "documentId": self.document_id,
            "role": self.role,
            "status": self.status,
            "dependencies": list(self.dependencies),
            "scriptHash": self.script_hash,
            "panels": [panel.to_dict() for panel in self.panels],
        }
