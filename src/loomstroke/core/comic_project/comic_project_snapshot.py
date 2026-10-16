"""Immutable view of one comic project (CT-17 §7.2 ``ComicProjectSnapshot``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .comic_page_entry import ComicPageEntry
from .comic_panel_entry import ComicPanelEntry
from .comic_project import ComicProject
from .dialogue_entry import DialogueEntry
from .production_task import ProductionTask
from .story_chapter import StoryChapter
from .story_scene import StoryScene

__all__ = ["ComicProjectSnapshot"]


def _frozen(mapping: Mapping | None) -> Mapping:
    return MappingProxyType(dict(mapping or {}))


@dataclass(frozen=True, slots=True)
class ComicProjectSnapshot:
    project: ComicProject
    chapters: Mapping[str, StoryChapter] = field(default_factory=lambda: _frozen({}))
    scenes: Mapping[str, StoryScene] = field(default_factory=lambda: _frozen({}))
    page_entries: Mapping[str, ComicPageEntry] = field(default_factory=lambda: _frozen({}))
    panels: Mapping[str, ComicPanelEntry] = field(default_factory=lambda: _frozen({}))
    dialogues: Mapping[str, DialogueEntry] = field(default_factory=lambda: _frozen({}))
    tasks: Mapping[str, ProductionTask] = field(default_factory=lambda: _frozen({}))

    def __post_init__(self) -> None:
        for name in ("chapters", "scenes", "page_entries", "panels", "dialogues", "tasks"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    @property
    def revision(self) -> int:
        return self.project.revision

    # -- ordered views -----------------------------------------------------------

    def chapters_in_order(self) -> tuple[StoryChapter, ...]:
        return tuple(sorted(self.chapters.values(), key=lambda c: (c.order_key, c.chapter_id)))

    def page_entries_for_chapter(self, chapter_id: str) -> tuple[ComicPageEntry, ...]:
        return tuple(
            sorted(
                (e for e in self.page_entries.values() if e.chapter_id == chapter_id),
                key=lambda e: (e.order_key, e.entry_id),
            )
        )

    def page_entries_in_order(self) -> tuple[ComicPageEntry, ...]:
        entries: list[ComicPageEntry] = []
        for chapter in self.chapters_in_order():
            entries.extend(self.page_entries_for_chapter(chapter.chapter_id))
        return tuple(entries)

    def panels_for_entry(self, entry_id: str) -> tuple[ComicPanelEntry, ...]:
        return tuple(
            sorted(
                (p for p in self.panels.values() if p.entry_id == entry_id),
                key=lambda p: (p.reading_order, p.panel_id),
            )
        )

    def dialogues_in_order(self) -> tuple[DialogueEntry, ...]:
        return tuple(sorted(self.dialogues.values(), key=lambda d: (d.order_key, d.dialogue_id)))

    def dialogues_for_entry(self, entry_id: str) -> tuple[DialogueEntry, ...]:
        return tuple(d for d in self.dialogues_in_order() if d.entry_id == entry_id)

    def entry_for_page(self, page_id: str) -> ComicPageEntry | None:
        for entry in self.page_entries.values():
            if entry.page_id == page_id:
                return entry
        return None

    # -- work queues -------------------------------------------------------------

    def unplaced_dialogues(self) -> tuple[DialogueEntry, ...]:
        return tuple(d for d in self.dialogues_in_order() if not d.is_placed)

    def dialogues_needing_review(self) -> tuple[DialogueEntry, ...]:
        return tuple(d for d in self.dialogues_in_order() if d.review_state == "needs_review")

    def search(self, query: str) -> tuple[DialogueEntry, ...]:
        needle = query.strip().casefold()
        if not needle:
            return self.dialogues_in_order()
        return tuple(
            d
            for d in self.dialogues_in_order()
            if needle in d.text.casefold() or needle in d.speaker.casefold()
        )

    def counts(self) -> dict[str, int]:
        return {
            "chapters": len(self.chapters),
            "pages": len(self.page_entries),
            "linked_pages": sum(1 for e in self.page_entries.values() if e.is_linked),
            "dialogues": len(self.dialogues),
            "placed": sum(1 for d in self.dialogues.values() if d.is_placed),
            "needs_review": len(self.dialogues_needing_review()),
            "tasks": len(self.tasks),
        }
