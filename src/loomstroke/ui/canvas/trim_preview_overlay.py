"""Trim view chrome, independent of render/export scene snapshots."""

from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter

from ...core.bleed_geometry import trim_bounds


def draw_trim_preview(painter: QPainter, widget: Any) -> None:
    host = getattr(widget, "_host_api", None)
    settings = getattr(host, "settings", None)
    if settings is None or not settings.get("view/trim_preview", False):
        return
    pages = getattr(host, "pages", None)
    if pages is None:
        return
    guides = (pages.get_page_view_state(pages.get_active_page_id()) or {}).get("guides", [])
    if not isinstance(guides, list) or any(not isinstance(g, dict) for g in guides):
        return
    width, height = widget.state.width, widget.state.height
    trim = trim_bounds(guides, (width, height))
    if trim is None:
        return
    left, top, right, bottom = trim
    color = QColor(0, 0, 0, 160)
    for rect in (
        (0, 0, width, top),
        (0, bottom, width, height - bottom),
        (0, top, left, bottom - top),
        (right, top, width - right, bottom - top),
    ):
        painter.fillRect(QRectF(*rect), color)
