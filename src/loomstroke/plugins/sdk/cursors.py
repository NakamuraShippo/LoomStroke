"""Shared cursor utilities for tool hover feedback."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QCursor,
    QGuiApplication,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

_rotate_cursor: QCursor | None = None


def get_rotate_cursor() -> QCursor:
    """Return a cached rotation arrow cursor."""
    global _rotate_cursor  # noqa: PLW0603
    if _rotate_cursor is not None:
        return _rotate_cursor
    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(Qt.GlobalColor.black, 1.8)
    p.setPen(pen)
    cx, cy, r = size / 2.0, size / 2.0, 8.0
    arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
    p.drawArc(arc_rect, 30 * 16, 280 * 16)
    angle_rad = math.radians(30)
    ax = cx + r * math.cos(angle_rad)
    ay = cy - r * math.sin(angle_rad)
    arrow = QPolygonF(
        [
            QPointF(ax + 4, ay - 1),
            QPointF(ax - 2, ay - 4),
            QPointF(ax - 1, ay + 3),
        ]
    )
    p.setBrush(Qt.GlobalColor.black)
    p.drawPolygon(arrow)
    p.end()
    _rotate_cursor = QCursor(pixmap, size // 2, size // 2)
    return _rotate_cursor


def cursor_for_handle(handle_name: str, handle_mode: str) -> QCursor | None:  # noqa: PLR0911
    """Return the appropriate cursor for a transform handle hit result."""
    if handle_mode == "scale":
        if handle_name in {"corner-tl", "corner-br"}:
            return QCursor(Qt.CursorShape.SizeFDiagCursor)
        if handle_name in {"corner-tr", "corner-bl"}:
            return QCursor(Qt.CursorShape.SizeBDiagCursor)
        if handle_name in {"edge-top", "edge-bottom"}:
            return QCursor(Qt.CursorShape.SizeVerCursor)
        if handle_name in {"edge-left", "edge-right"}:
            return QCursor(Qt.CursorShape.SizeHorCursor)
    elif handle_mode == "rotate" or handle_name == "rotate":
        return get_rotate_cursor()
    elif handle_mode == "move" or handle_name == "move-area":
        return QCursor(Qt.CursorShape.SizeAllCursor)
    return None


def apply_hover_cursor(cursor: QCursor | None, *, is_set: bool) -> bool:
    """Apply or restore override cursor. Returns new is_set state."""
    if cursor is not None:
        if not is_set:
            QGuiApplication.setOverrideCursor(cursor)
        else:
            QGuiApplication.changeOverrideCursor(cursor)
        return True
    if is_set:
        QGuiApplication.restoreOverrideCursor()
    return False
