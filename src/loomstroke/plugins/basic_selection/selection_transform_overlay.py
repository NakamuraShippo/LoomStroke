"""Screen-space chrome and hit testing for raster selection transforms."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform

from loomstroke.plugins.sdk.handles import handle_positions_for_rect, hit_test_handle_points
from loomstroke.ui._dock_safe import is_valid


class SelectionTransformOverlay:
    """Keep the drawn controls and their pointer targets in the same coordinate space."""

    ROTATION_OFFSET = 28.0

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._widget: Any = None
        self.enabled = False

    def refresh(self) -> None:
        if not self.enabled or self._tool.host_api is None:
            return
        free = self._tool._free_selection_controller
        if free._session is not None:
            return
        ui = getattr(self._tool.host_api, "ui", None)
        window = getattr(ui, "get_main_window", lambda: None)()
        widget = getattr(window, "canvas", None)
        if is_valid(widget) and callable(getattr(widget, "set_tool_overlay_fn", None)):
            self._widget = widget
            widget.set_tool_overlay_fn(self.paint)

    def clear(self) -> None:
        self.enabled = False
        if is_valid(self._widget):
            self._widget.set_tool_overlay_fn(None)
        self._widget = None

    def document_handles(self) -> dict[str, tuple[float, float]]:
        tool = self._tool
        if tool.host_api is None or tool._is_vector_mode() or tool._start is not None:
            return {}
        free = tool._free_selection_controller
        if free._session is not None or free._preparation.busy:
            return {}
        if tool._transform_active:
            return tool._handle_positions() or {}
        if not tool._selection_exists():
            return {}
        canvas = tool.host_api.canvas
        getter = getattr(canvas, "get_selection_bounds", None)
        bounds = tool._normalize_bounds(
            getter() if callable(getter) else getattr(canvas, "_selection_bounds", None)
        )
        return handle_positions_for_rect(bounds) if bounds is not None else {}

    def screen_handles(
        self, mapping: Callable[[float, float], tuple[float, float]]
    ) -> dict[str, tuple[float, float]]:
        handles = {name: mapping(*point) for name, point in self.document_handles().items()}
        if not handles or not all(math.isfinite(v) for point in handles.values() for v in point):
            return {}
        tx, ty = handles["edge-top"]
        bx, by = handles["edge-bottom"]
        dx, dy = tx - bx, ty - by
        length = math.hypot(dx, dy)
        if length <= 1e-8:
            return {}
        handles["rotate"] = (
            tx + dx / length * self.ROTATION_OFFSET,
            ty + dy / length * self.ROTATION_OFFSET,
        )
        return handles

    def hit_test(self, point: Any) -> tuple[str, str] | None:
        viewport = getattr(self._widget, "viewport", None) if is_valid(self._widget) else None
        mapping = getattr(viewport, "canvas_to_widget", None)
        if not callable(mapping):

            def mapping(x: float, y: float) -> tuple[float, float]:
                return x, y

        px, py = mapping(*point.pos)
        handles = self.screen_handles(mapping)
        for name in sorted(handles, key=lambda name: math.dist((px, py), handles[name])):
            hit = hit_test_handle_points(
                px,
                py,
                {name: handles[name]},
                corner_half_extent=7.0,
                edge_radius=7.0,
                rotation_radius=8.0,
            )
            if hit is not None:
                return hit
        if self._tool._transform_active and self._tool._point_inside_obb(*point.pos):
            return ("move-area", "move")
        return None

    def paint(self, painter: QPainter) -> None:
        transform = painter.transform()

        def mapping(x: float, y: float) -> tuple[float, float]:
            point = transform.map(QPointF(x, y))
            return point.x(), point.y()

        handles = self.screen_handles(mapping)
        if not handles:
            return
        painter.save()
        try:
            painter.setTransform(QTransform())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(20, 120, 255), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            corners = ("corner-tl", "corner-tr", "corner-br", "corner-bl")
            painter.drawPolygon(QPolygonF([QPointF(*handles[name]) for name in corners]))
            painter.drawLine(QPointF(*handles["edge-top"]), QPointF(*handles["rotate"]))
            painter.setBrush(QColor("white"))
            for name, (x, y) in handles.items():
                if name.startswith("corner"):
                    painter.drawRect(QRectF(x - 5.0, y - 5.0, 10.0, 10.0))
                else:
                    painter.drawEllipse(QPointF(x, y), 4.0, 4.0)
        finally:
            painter.restore()
