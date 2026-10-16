"""A panel inside a page entry (CT-17 §7.1)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ComicPanelEntry"]


@dataclass(frozen=True, slots=True)
class ComicPanelEntry:
    panel_id: str
    entry_id: str
    reading_order: int
    summary: str = ""
    canvas_object_ref: str | None = None
    status: str = "name"
