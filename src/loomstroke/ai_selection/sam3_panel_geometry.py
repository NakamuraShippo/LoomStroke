"""One comic panel bbox in canvas space with reading order and provenance (CT-23 §9.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Sam3PanelGeometry"]


@dataclass(frozen=True, slots=True)
class Sam3PanelGeometry:
    panel_id: str
    bbox: tuple[int, int, int, int]
    reading_order: int = 0
    object_ref: str | None = None

    @property
    def area(self) -> int:
        return max(0, self.bbox[2]) * max(0, self.bbox[3])

    def overlaps(self, other: Sam3PanelGeometry) -> bool:
        ax, ay, aw, ah = self.bbox
        bx, by, bw, bh = other.bbox
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    def to_dict(self) -> dict[str, Any]:
        return {
            "panelId": self.panel_id,
            "bbox": list(self.bbox),
            "readingOrder": int(self.reading_order),
            "objectRef": self.object_ref,
        }
