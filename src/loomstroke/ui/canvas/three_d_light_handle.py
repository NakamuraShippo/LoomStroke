"""Screen-space hit target for the active 3D layer's point light."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPen, QRadialGradient

from loomstroke.core.drawing_figures.projection import project_points
from loomstroke.core.layer_3d_light import light_position
from loomstroke.core.layer_3d_rendering import orbit_camera_state


@dataclass(frozen=True, slots=True)
class LightHandlePlacement:
    canvas: tuple[float, float]
    widget: tuple[float, float]
    light_canvas: tuple[float, float]
    offscreen_pixels: float


@dataclass(frozen=True, slots=True)
class AxisHandle:
    canvas: tuple[float, float]
    widget: tuple[float, float]
    direction_widget: tuple[float, float]


def canvas_position(layer: Any, width: int, height: int) -> tuple[float, float]:
    point = project_points(
        np.asarray([light_position(layer)]),
        layer.scene_3d,
        orbit_camera_state(layer.camera_3d, layer.scene_3d),
        width,
        height,
        (getattr(layer, "offset_x", 0), getattr(layer, "offset_y", 0)),
    )[0]
    return float(point[0]), float(point[1])


def placement(layer: Any, widget: Any) -> LightHandlePlacement:
    light_canvas = canvas_position(layer, int(widget.state.width), int(widget.state.height))
    wx, wy = widget.viewport.canvas_to_widget(*light_canvas)
    width, height = _widget_size(widget)
    if 0.0 <= wx <= width and 0.0 <= wy <= height:
        return LightHandlePlacement(light_canvas, (wx, wy), light_canvas, 0.0)
    margin_x, margin_y = min(36.0, width * 0.5), min(36.0, height * 0.5)
    marker = (
        min(max(wx, margin_x), width - margin_x),
        min(max(wy, margin_y), height - margin_y),
    )
    return LightHandlePlacement(
        widget.viewport.widget_to_canvas(*marker),
        marker,
        light_canvas,
        math.hypot(wx - marker[0], wy - marker[1]),
    )


def _widget_size(widget: Any) -> tuple[float, float]:
    width_fn, height_fn = getattr(widget, "width", None), getattr(widget, "height", None)
    width = float(width_fn()) if callable(width_fn) else float(widget.state.width)
    height = float(height_fn()) if callable(height_fn) else float(widget.state.height)
    return max(width, 1.0), max(height, 1.0)


def axis_handles(
    layer: Any, widget: Any, marker: LightHandlePlacement | None = None
) -> dict[int, AxisHandle]:
    marker = marker or placement(layer, widget)
    if marker.offscreen_pixels:
        return {}
    rotation = orbit_camera_state(layer.camera_3d, layer.scene_3d).rotation_matrix()
    zoom = max(float(getattr(getattr(widget.viewport, "state", None), "zoom", 1.0)), 0.01)
    handles: dict[int, AxisHandle] = {}
    for axis in range(3):
        direction_canvas = (
            marker.canvas[0] + float(rotation[0, axis]),
            marker.canvas[1] - float(rotation[1, axis]),
        )
        projected = widget.viewport.canvas_to_widget(*direction_canvas)
        dx, dy = projected[0] - marker.widget[0], projected[1] - marker.widget[1]
        length = math.hypot(dx, dy)
        if length < 0.18 * zoom:
            continue
        direction = dx / length, dy / length
        endpoint = (
            marker.widget[0] + direction[0] * 45.0,
            marker.widget[1] + direction[1] * 45.0,
        )
        handles[axis] = AxisHandle(widget.viewport.widget_to_canvas(*endpoint), endpoint, direction)
    return handles


def draw(painter: Any, layer: Any, widget: Any) -> None:
    marker = placement(layer, widget)
    x, y = marker.canvas
    zoom = max(float(getattr(getattr(widget.viewport, "state", None), "zoom", 1.0)), 0.01)
    pixel = 1.0 / zoom
    center = QPointF(x, y)
    painter.save()
    painter.setPen(QPen(QColor("#9b651f"), 1.0 * pixel))
    painter.setBrush(QColor(255, 218, 107, 40))
    painter.drawEllipse(center, 31.0 * pixel, 31.0 * pixel)
    if marker.offscreen_pixels:
        dx, dy = marker.light_canvas[0] - x, marker.light_canvas[1] - y
        length = math.hypot(dx, dy)
        direction = QPointF(dx / length, dy / length)
        tip = QPointF(x + direction.x() * 28.0 * pixel, y + direction.y() * 28.0 * pixel)
        painter.setPen(QPen(QColor("#203039"), 5.0 * pixel))
        painter.drawLine(center, tip)
        painter.setPen(QPen(QColor("#ffe17b"), 3.0 * pixel))
        painter.drawLine(center, tip)
    else:
        handles = axis_handles(layer, widget, marker)
        for axis, color in enumerate(("#ed685d", "#73c68c", "#65aaf4")):
            handle = handles.get(axis)
            if handle is None:
                continue
            end = QPointF(*handle.canvas)
            painter.setPen(QPen(QColor("#25313b"), 5.5 * pixel))
            painter.drawLine(center, end)
            painter.setPen(QPen(QColor(color), 3.5 * pixel))
            painter.drawLine(center, end)
            painter.setPen(QPen(QColor("#25313b"), 1.4 * pixel))
            painter.setBrush(QColor(color))
            painter.drawEllipse(end, 7.0 * pixel, 7.0 * pixel)
    sphere = QRadialGradient(QPointF(x - 4.0 * pixel, y - 4.0 * pixel), 20.0 * pixel)
    sphere.setColorAt(0.0, QColor("#fffef2"))
    sphere.setColorAt(0.38, QColor("#ffe883"))
    sphere.setColorAt(0.72, QColor("#e9a935"))
    sphere.setColorAt(1.0, QColor("#80501f"))
    painter.setPen(QPen(QColor("#203039"), 1.4 * pixel))
    painter.setBrush(sphere)
    painter.drawEllipse(center, 14.0 * pixel, 14.0 * pixel)
    painter.setPen(QPen(QColor("#fffaf0"), 1.2 * pixel))
    painter.drawPoint(center)
    if marker.offscreen_pixels:
        _draw_distance_badge(painter, marker, widget, pixel)
    painter.restore()


def _draw_distance_badge(
    painter: Any, marker: LightHandlePlacement, widget: Any, pixel: float
) -> None:
    label = f"{marker.offscreen_pixels:,.0f} px"
    font = painter.font()
    font.setPixelSize(max(1, round(10.0 * pixel)))
    painter.setFont(font)
    screen_width = len(label) * 6.5 + 14.0
    width = screen_width * pixel
    view_width, view_height = _widget_size(widget)
    toward_center = (
        view_width * 0.5 - marker.widget[0],
        view_height * 0.5 - marker.widget[1],
    )
    inward_length = max(math.hypot(*toward_center), 1.0)
    badge_widget = (
        marker.widget[0] + toward_center[0] / inward_length * (45.0 + screen_width * 0.5),
        marker.widget[1] + toward_center[1] / inward_length * (45.0 + screen_width * 0.5),
    )
    badge = QPointF(*widget.viewport.widget_to_canvas(*badge_widget))
    rect = QRectF(badge.x() - width * 0.5, badge.y() - 9.0 * pixel, width, 18.0 * pixel)
    painter.setPen(QPen(QColor("#203039"), 1.0 * pixel))
    painter.setBrush(QColor(32, 48, 57, 225))
    painter.drawRoundedRect(rect, 5.0 * pixel, 5.0 * pixel)
    painter.setPen(QColor("#fff4cf"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


def hit(
    layer: Any,
    widget: Any,
    widget_pos: tuple[float, float],
    marker: LightHandlePlacement | None = None,
) -> bool:
    return target_at(layer, widget, widget_pos, marker) is not None


def target_at(
    layer: Any,
    widget: Any,
    widget_pos: tuple[float, float],
    marker: LightHandlePlacement | None = None,
) -> str | None:
    marker = marker or placement(layer, widget)
    dx, dy = marker.widget[0] - widget_pos[0], marker.widget[1] - widget_pos[1]
    distance_sq = dx * dx + dy * dy
    if marker.offscreen_pixels:
        return "proxy" if distance_sq <= 32.0**2 else None
    if distance_sq <= 17.0**2:
        return "sphere"
    closest_axis: int | None = None
    closest_distance_sq = 11.0**2
    for axis, handle in axis_handles(layer, widget, marker).items():
        tip_dx = handle.widget[0] - widget_pos[0]
        tip_dy = handle.widget[1] - widget_pos[1]
        tip_distance_sq = tip_dx * tip_dx + tip_dy * tip_dy
        if tip_distance_sq <= closest_distance_sq:
            closest_axis, closest_distance_sq = axis, tip_distance_sq
            continue
        from_center = (
            widget_pos[0] - marker.widget[0],
            widget_pos[1] - marker.widget[1],
        )
        along = (
            from_center[0] * handle.direction_widget[0]
            + from_center[1] * handle.direction_widget[1]
        )
        if 18.0 <= along <= 45.0:
            across = (
                from_center[0] * handle.direction_widget[1]
                - from_center[1] * handle.direction_widget[0]
            )
            if across * across < closest_distance_sq and across * across <= 7.0**2:
                closest_axis, closest_distance_sq = axis, across * across
    return "xyz"[closest_axis] if closest_axis is not None else None
