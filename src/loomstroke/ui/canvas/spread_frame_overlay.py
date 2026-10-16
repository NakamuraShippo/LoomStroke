"""Page frames for the spread viewport (CT-06 B4-c2).

Two pages side by side with no border read as one drawing, so the edit
target has to be marked: the active page gets an accent frame, the facing
page a faint one that only says "this is a page, and it is not the one you
are editing" (spec §6.3).

Drawn as a Qt overlay rather than in the wgpu pass because it is chrome,
not content: it must not appear in exports, readbacks or thumbnails, and
two thin rectangles per frame cost nothing next to a composite.

The painter arrives already carrying the canvas transform, so rectangles
are given in canvas space with the active page at the origin.  Pen widths
are divided by the view scale so the frame stays one pixel-ish on screen
instead of growing with zoom.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen

__all__ = ["draw_spread_frames"]

ACTIVE_FRAME_COLOR = QColor(80, 160, 255, 220)
INACTIVE_FRAME_COLOR = QColor(140, 140, 140, 140)
ACTIVE_FRAME_WIDTH_PX = 2.0
INACTIVE_FRAME_WIDTH_PX = 1.0


def _pen(color: QColor, width_px: float, view_scale: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width_px / max(view_scale, 1e-6))
    pen.setCosmetic(False)
    return pen


def draw_spread_frames(
    painter: QPainter,
    *,
    active_rect: tuple[float, float, float, float],
    companion_rect: tuple[float, float, float, float] | None,
    view_scale: float = 1.0,
) -> None:
    """Outline the two slots of the spread in canvas space."""

    painter.save()
    try:
        painter.setBrush(QColor(0, 0, 0, 0))
        if companion_rect is not None:
            painter.setPen(_pen(INACTIVE_FRAME_COLOR, INACTIVE_FRAME_WIDTH_PX, view_scale))
            painter.drawRect(QRectF(*companion_rect))
        # The active frame is drawn last so it wins where the two touch.
        painter.setPen(_pen(ACTIVE_FRAME_COLOR, ACTIVE_FRAME_WIDTH_PX, view_scale))
        painter.drawRect(QRectF(*active_rect))
    finally:
        painter.restore()
