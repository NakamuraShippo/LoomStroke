"""Chapter / episode (CT-17 §7.1)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["StoryChapter"]


@dataclass(frozen=True, slots=True)
class StoryChapter:
    chapter_id: str
    order_key: str
    title: str
    deadline: str | None = None
    notes: str = ""
