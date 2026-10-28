"""Grid overlay adapter; default square rendering retains its original pixel positions."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ...core.layout_grid_geometry import visible_grid_geometry
from ...core.layout_grid_settings import read_layout_grid


def draw_layout_grid(painter: QPainter, widget: Any) -> None:
    if not widget.state.grid_visible:
        return
    settings = getattr(getattr(widget, "_host_api", None), "settings", None)
    origin = (0.0, 0.0)
    controller = getattr(widget, "_guide_controller", None)
    if (
        controller is not None
        and settings is not None
        and settings.get("view/grid_follow_ruler_origin", False)
    ):
        point = controller.ruler_state().origin
        origin = point.x(), point.y()
    pages = getattr(getattr(widget, "_host_api", None), "pages", None)
    dpi = (
        pages.get_active_canvas_spec().dpi
        if pages is not None
        and settings is not None
        and settings.get("view/grid_kind") == "baseline"
        else (72.0, 72.0)
    )
    grid = read_layout_grid(settings, size=widget.state.grid_size, origin=origin, dpi=dpi)
    if grid.kind == "square" and grid.origin == (0.0, 0.0) and grid.rotation == 0:
        draw_square_grid(painter, widget)
        return
    canvas_rect = QRectF(0, 0, widget.state.width, widget.state.height)
    visible = widget.viewport.get_visible_rect().intersected(canvas_rect)
    if visible.isEmpty():
        return
    geometry = visible_grid_geometry(
        grid, (visible.left(), visible.top(), visible.right(), visible.bottom())
    )
    painter.save()
    try:
        painter.setClipRect(visible, Qt.ClipOperation.IntersectClip)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        minor_pen = QPen(QColor(200, 200, 200), 1.0)
        major_pen = QPen(QColor(200, 200, 200), 2.0)
        minor_pen.setCosmetic(True)
        major_pen.setCosmetic(True)
        for line in geometry.lines:
            painter.setPen(major_pen if line.major else minor_pen)
            painter.drawLine(QPointF(*line.start), QPointF(*line.end))
        painter.setPen(minor_pen)
        for cx, cy, radius in geometry.circles:
            painter.drawEllipse(QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius))
    finally:
        painter.restore()


def draw_square_grid(painter: QPainter, widget: Any) -> None:
    """Draw grid overlay with pixel-snapped lines for uniform cells."""
    if not widget.state.grid_visible:
        return

    painter.save()

    step = widget.state.grid_size
    if step <= 0:
        step = 1

    # Compute grid line positions in canvas coordinates
    canvas_rect = QRectF(0.0, 0.0, float(widget.state.width), float(widget.state.height))
    visible_rect = QRectF(widget.viewport.get_visible_rect()).intersected(canvas_rect)
    if visible_rect.isEmpty():
        painter.restore()
        return

    # Switch to widget coordinates for pixel-perfect lines
    transform = painter.transform()
    painter.resetTransform()

    pen = QPen(QColor(200, 200, 200), 1.0)
    painter.setPen(pen)

    # Map canvas clip rect to widget coords for clipping
    widget_clip = transform.mapRect(canvas_rect)
    painter.setClipRect(widget_clip)

    # Map canvas boundary edges to widget coords
    top_left_w = transform.map(QPointF(0.0, 0.0))
    bot_right_w = transform.map(QPointF(float(widget.state.width), float(widget.state.height)))
    w_left = top_left_w.x()
    w_right = bot_right_w.x()
    w_top = top_left_w.y()
    w_bottom = bot_right_w.y()

    # Draw vertical lines — snap each to nearest pixel
    left = visible_rect.left()
    right = visible_rect.right()
    first_xi = int(left) // step
    last_xi = int(right) // step + 1
    for xi in range(first_xi, last_xi + 1):
        pt = transform.map(QPointF(float(xi * step), 0.0))
        wx = round(pt.x()) + 0.5
        painter.drawLine(QPointF(wx, w_top), QPointF(wx, w_bottom))

    # Draw horizontal lines — snap each to nearest pixel
    top = visible_rect.top()
    bottom = visible_rect.bottom()
    first_yi = int(top) // step
    last_yi = int(bottom) // step + 1
    for yi in range(first_yi, last_yi + 1):
        pt = transform.map(QPointF(0.0, float(yi * step)))
        wy = round(pt.y()) + 0.5
        painter.drawLine(QPointF(w_left, wy), QPointF(w_right, wy))

    painter.restore()
