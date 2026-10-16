"""Scene inside a chapter (CT-17 §7.1)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["StoryScene"]


@dataclass(frozen=True, slots=True)
class StoryScene:
    scene_id: str
    chapter_id: str
    order_key: str
    summary: str = ""
    page_entry_ids: tuple[str, ...] = ()
    character_refs: tuple[str, ...] = ()
    notes: str = ""
