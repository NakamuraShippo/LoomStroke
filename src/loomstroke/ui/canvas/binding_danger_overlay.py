"""Two-slot binding chrome, excluded from document rendering and export."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.binding_danger_geometry import binding_danger_bounds


def draw_binding_danger(painter: QPainter, state: Any, host: Any) -> None:
    settings = getattr(host, "settings", None)
    if settings is None or not settings.get("view/spread_binding_overlay", False):
        return
    pages = getattr(host, "pages", None)
    if pages is None:
        return
    painter.save()
    try:
        anchor_x, anchor_y = state.active_rect.x, state.active_rect.y
        for slot in state.geometry.rects:
            if slot.page_id is None:
                continue
            view = pages.get_page_view_state(slot.page_id) or {}
            guides = view.get("guides", [])
            if not isinstance(guides, list) or any(not isinstance(g, dict) for g in guides):
                continue
            danger = binding_danger_bounds(guides, slot.physical_side)
            if danger is not None:
                left, top, right, bottom = danger
                painter.fillRect(
                    QRectF(
                        left + slot.x - anchor_x,
                        top + slot.y - anchor_y,
                        right - left,
                        bottom - top,
                    ),
                    QColor(255, 90, 60, 55),
                )
        seam = state.geometry.left.right_edge - anchor_x
        pen = QPen(QColor(255, 100, 60, 220), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(seam, -anchor_y), QPointF(seam, state.geometry.height - anchor_y))
    finally:
        painter.restore()
