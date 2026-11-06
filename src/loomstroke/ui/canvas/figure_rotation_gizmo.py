"""Projected local XYZ rotation rings with stable edge-on dragging."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPolygonF

from loomstroke.core.drawing_figures.hand_joints import HAND_JOINTS
from loomstroke.core.drawing_figures.kinematics import world_matrices
from loomstroke.core.drawing_figures.model import JOINTS, FigurePose, load_asset
from loomstroke.core.drawing_figures.projection import project_points, projection_scale

_RADIUS = 42.0
_SAMPLES = 64
_COLORS = {"X": "#ff6262", "Y": "#6bda83", "Z": "#649dff"}


@dataclass(frozen=True, slots=True)
class RotationRing:
    axis: str
    world_axis: np.ndarray
    center_widget: np.ndarray
    plane_widget: np.ndarray
    canvas_points: np.ndarray
    widget_points: np.ndarray


def rotation_rings(
    pose: FigurePose,
    joint: str,
    scene: Any,
    camera: Any,
    size: tuple[int, int],
    offset: tuple[float, float],
    viewport: Any,
) -> tuple[RotationRing, ...]:
    asset = load_asset(pose.model)
    matrix = world_matrices(pose, asset)[asset.names.index({**JOINTS, **HAND_JOINTS}[joint])]
    center, basis = matrix[:3, 3], matrix[:3, :3]
    origin_widget = np.asarray(viewport.canvas_to_widget(0, 0))
    zoom = float(np.linalg.norm(np.asarray(viewport.canvas_to_widget(1, 0)) - origin_widget))
    radius = _RADIUS / max(projection_scale(scene, camera, *size) * zoom, 1e-8)
    phases = np.linspace(0, 2 * math.pi, _SAMPLES + 1)
    world_rings = []
    for axis in range(3):
        u, v = basis[:, (axis + 1) % 3], basis[:, (axis + 2) % 3]
        points = center + radius * (np.cos(phases)[:, None] * u + np.sin(phases)[:, None] * v)
        world_rings.append(points)
    # Share one projection/bounds scan across all three rings and their center.
    projected = project_points(
        np.concatenate([center[None, :], *world_rings]), scene, camera, *size, offset
    )
    center_widget = np.asarray(viewport.canvas_to_widget(*projected[0]))
    rings = []
    for axis, name in enumerate(("X", "Y", "Z")):
        start = 1 + axis * (_SAMPLES + 1)
        canvas_points = projected[start : start + _SAMPLES + 1]
        widget_points = np.asarray([viewport.canvas_to_widget(*xy) for xy in canvas_points])
        plane = np.column_stack(
            (widget_points[0] - center_widget, widget_points[_SAMPLES // 4] - center_widget)
        )
        rings.append(
            RotationRing(
                name, basis[:, axis].copy(), center_widget, plane, canvas_points, widget_points
            )
        )
    return tuple(rings)


def hit_rotation_ring(
    rings: tuple[RotationRing, ...], point: np.ndarray, tolerance: float = 6.0
) -> RotationRing | None:
    nearest, distance = None, tolerance
    for ring in rings:
        origins, ends = ring.widget_points[:-1], ring.widget_points[1:]
        vectors = ends - origins
        t = np.clip(
            np.sum((point - origins) * vectors, axis=1)
            / np.maximum(np.sum(vectors * vectors, axis=1), 1e-12),
            0,
            1,
        )
        current = float(np.min(np.linalg.norm(point - origins - t[:, None] * vectors, axis=1)))
        if current <= distance:
            nearest, distance = ring, current
    return nearest


@dataclass
class RotationDrag:
    ring: RotationRing
    start: np.ndarray
    angle: float = 0.0
    _phase: float = field(default=0.0, init=False)
    _inverse: np.ndarray | None = field(default=None, init=False)
    _tangent: np.ndarray = field(default_factory=lambda: np.zeros(2), init=False)

    def __post_init__(self) -> None:
        singular = np.linalg.svd(self.ring.plane_widget, compute_uv=False)
        if singular[-1] > singular[0] * 0.15:
            self._inverse = np.linalg.inv(self.ring.plane_widget)
            xy = self._inverse @ (self.start - self.ring.center_widget)
            self._phase = math.atan2(float(xy[1]), float(xy[0]))
        else:
            index = int(np.argmin(np.linalg.norm(self.ring.widget_points - self.start, axis=1)))
            phase = index * 2 * math.pi / _SAMPLES
            tangent = self.ring.plane_widget @ np.array((-math.sin(phase), math.cos(phase)))
            if np.linalg.norm(tangent) < 0.5:
                tangent = self.ring.plane_widget[
                    :, int(np.argmax(np.linalg.norm(self.ring.plane_widget, axis=0)))
                ]
            self._tangent = tangent / max(float(np.linalg.norm(tangent)), 1e-8)

    def update(self, point: np.ndarray) -> float:
        if self._inverse is None:
            self.angle = float(np.dot(point - self.start, self._tangent)) / _RADIUS
        else:
            xy = self._inverse @ (point - self.ring.center_widget)
            if np.linalg.norm(xy) > 1e-6:
                phase = math.atan2(float(xy[1]), float(xy[0]))
                self.angle += math.atan2(
                    math.sin(phase - self._phase), math.cos(phase - self._phase)
                )
                self._phase = phase
        return math.degrees(self.angle)


def draw_rotation_rings(
    painter: Any,
    rings: tuple[RotationRing, ...],
    zoom: float,
    active_axis: str | None,
    hover_axis: str | None = None,
) -> None:
    painter.save()
    try:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        font = painter.font()
        font.setPointSizeF(8.0 / max(zoom, 0.01))
        painter.setFont(font)
        for ring in sorted(rings, key=lambda item: item.axis in {active_axis, hover_axis}):
            highlighted = ring.axis in {active_axis, hover_axis}
            color = QColor(_COLORS[ring.axis])
            color.setAlpha(255 if highlighted else 195)
            polyline = QPolygonF([QPointF(*xy) for xy in ring.canvas_points])
            if highlighted:
                halo = QPen(QColor(20, 24, 30, 220), 6.0)
                halo.setCosmetic(True)
                painter.setPen(halo)
                painter.drawPolyline(polyline)
            pen = QPen(color, 3.5 if highlighted else 1.8)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPolyline(polyline)
            label = ring.canvas_points[8] + np.array((4, -4)) / max(zoom, 0.01)
            painter.drawText(QPointF(*label), ring.axis)
    finally:
        painter.restore()
