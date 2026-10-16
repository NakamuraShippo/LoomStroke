"""Multi-page structured plan (CT-22 §8.1 ``ComicBookPlan``); script text never stored here."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from .comic_page_work_item import ComicPageWorkItem

__all__ = ["ComicBookPlan"]


@dataclass(frozen=True, slots=True)
class ComicBookPlan:
    plan_id: str
    project_ref: str
    pages: tuple[ComicPageWorkItem, ...] = ()
    reading_direction: str = "rtl"
    style_constraints: Mapping[str, Any] = field(default_factory=dict)
    source_revision: int = 0
    warnings: tuple[str, ...] = ()

    SCHEMA_VERSION: ClassVar[str] = "comic.book_plan.v1"

    @property
    def revision(self) -> str:
        digest = hashlib.sha256()
        digest.update(
            f"{self.project_ref}:{self.source_revision}:{self.reading_direction};".encode()
        )
        for page in self.pages:
            digest.update(f"{page.entry_id}:{page.page_number}:{page.script_hash};".encode())
            for panel in page.panels:
                digest.update(
                    f"{panel.panel_id}:{panel.reading_order}:{panel.dialogue_hashes};".encode()
                )
        return digest.hexdigest()[:16]

    def page(self, entry_id: str) -> ComicPageWorkItem | None:
        return next((p for p in self.pages if p.entry_id == entry_id), None)

    def with_page(self, page: ComicPageWorkItem) -> ComicBookPlan:
        pages = tuple(page if p.entry_id == page.entry_id else p for p in self.pages)
        return replace(self, pages=pages)

    def counts(self) -> dict[str, int]:
        panel_count = sum(len(page.panels) for page in self.pages)
        linked = sum(1 for page in self.pages if page.is_linked)
        return {"pages": len(self.pages), "panels": panel_count, "linkedPages": linked}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "planId": self.plan_id,
            "projectRef": self.project_ref,
            "revision": self.revision,
            "sourceRevision": int(self.source_revision),
            "readingDirection": self.reading_direction,
            "styleConstraints": dict(self.style_constraints),
            "counts": self.counts(),
            "warnings": list(self.warnings),
            "pages": [page.to_dict() for page in self.pages],
        }
