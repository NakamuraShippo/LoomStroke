"""View-only composition guides; never part of render snapshots or exports."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.composition_geometry import composition_lines, reference_rect


def draw_composition_overlay(painter: QPainter, widget: Any) -> None:
    host = getattr(widget, "_host_api", None)
    settings = getattr(host, "settings", None)
    if settings is None:
        return
    kinds = settings.get("view/composition_overlay_kinds", [])
    if not isinstance(kinds, (list, tuple)) or not kinds:
        return
    pages = getattr(host, "pages", None)
    guides = []
    if pages is not None:
        view = pages.get_page_view_state(pages.get_active_page_id())
        guides = view.get("guides", []) if isinstance(view, dict) else []
    if not isinstance(guides, (list, tuple)):
        guides = []
    bounds = getattr(getattr(host, "canvas", None), "_selection_bounds", None)
    selection = (
        None
        if bounds is None or bounds.isEmpty()
        else (bounds.x(), bounds.y(), bounds.width(), bounds.height())
    )
    rect = reference_rect(
        (widget.state.width, widget.state.height),
        settings.get("view/composition_overlay_reference", "canvas"),
        guides=(g for g in guides if isinstance(g, dict)),
        selection=selection,
    )
    if rect is None:
        return
    try:
        percent = float(settings.get("view/composition_overlay_safe_percent", 10.0))
    except (TypeError, ValueError):
        return
    lines = composition_lines(rect, kinds, percent)
    painter.save()
    try:
        painter.setClipRect(QRectF(*rect), Qt.ClipOperation.IntersectClip)
        pen = QPen(QColor(240, 190, 60, 180), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for start, end in lines:
            painter.drawLine(QPointF(*start), QPointF(*end))
    finally:
        painter.restore()
