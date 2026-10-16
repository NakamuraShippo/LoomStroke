"""Non-printing overlay primitives for one assist, in page coordinates."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AssistOverlayGeometry"]

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class AssistOverlayGeometry:
    lines: tuple[tuple[Point, Point], ...] = ()
    ellipses: tuple[tuple[float, float, float, float], ...] = ()
    """``(cx, cy, rx, ry)``."""
    markers: tuple[tuple[float, float, str], ...] = ()
    """``(x, y, label)``."""
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.lines or self.ellipses or self.markers)
