"""Bounded overlay geometry per assist (CT-14: ≤ 256 lines, clipped to the canvas)."""

from __future__ import annotations

import math
from typing import ClassVar

from .assist_overlay_geometry import AssistOverlayGeometry
from .drawing_assist import DrawingAssist
from .rotational_geometry import RotationalGeometry

__all__ = ["OverlayGeometryBuilder"]

Point = tuple[float, float]


class OverlayGeometryBuilder:
    MAX_LINES: ClassVar[int] = 256
    PARALLEL_MIN_SPACING: ClassVar[float] = 24.0
    RADIAL_SPOKES: ClassVar[int] = 24
    CONCENTRIC_RINGS: ClassVar[int] = 8
    PERSPECTIVE_FAN: ClassVar[int] = 16

    def build(
        self, assist: DrawingAssist, canvas_size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        if not assist.is_known:
            return AssistOverlayGeometry()
        builder = getattr(self, f"_build_{assist.type}")
        return builder(assist, (max(1.0, float(canvas_size[0])), max(1.0, float(canvas_size[1]))))

    # -- per type ----------------------------------------------------------------------

    def _build_mirror(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        p0, p1 = assist.point("p0"), assist.point("p1")
        if p0 is None or p1 is None:
            return AssistOverlayGeometry()
        line = self._extend(p0, p1, size)
        lines = (line,) if line is not None else ()
        return AssistOverlayGeometry(
            lines=lines, markers=((p0[0], p0[1], "A"), (p1[0], p1[1], "B"))
        )

    def _build_rotational(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        center = assist.point("center")
        if center is None:
            return AssistOverlayGeometry()
        count = max(2, min(17, int(assist.number("count", 2.0, source="options"))))
        geometry = RotationalGeometry(center[0], center[1], count)
        diagonal = math.hypot(*size)
        lines = []
        for angle in geometry.spoke_angles():
            end = (center[0] + math.cos(angle) * diagonal, center[1] + math.sin(angle) * diagonal)
            lines.append((center, end))
        return AssistOverlayGeometry(
            lines=tuple(lines), markers=((center[0], center[1], f"x{count}"),)
        )

    def _build_parallel(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        angle = math.radians(assist.number("angle", 0.0))
        dx, dy = math.cos(angle), math.sin(angle)
        nx, ny = -dy, dx
        diagonal = math.hypot(*size)
        spacing = max(self.PARALLEL_MIN_SPACING, diagonal / 16.0)
        count = min(self.MAX_LINES, int(diagonal / spacing) + 2)
        cx, cy = size[0] / 2.0, size[1] / 2.0
        lines = []
        for index in range(-(count // 2), count // 2 + 1):
            ox, oy = cx + nx * spacing * index, cy + ny * spacing * index
            lines.append(
                ((ox - dx * diagonal, oy - dy * diagonal), (ox + dx * diagonal, oy + dy * diagonal))
            )
        return AssistOverlayGeometry(
            lines=tuple(lines[: self.MAX_LINES]), truncated=len(lines) > self.MAX_LINES
        )

    def _build_radial(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        center = assist.point("center")
        if center is None:
            return AssistOverlayGeometry()
        diagonal = math.hypot(*size)
        lines = []
        for k in range(self.RADIAL_SPOKES):
            angle = 2.0 * math.pi * k / self.RADIAL_SPOKES
            lines.append(
                (
                    center,
                    (
                        center[0] + math.cos(angle) * diagonal,
                        center[1] + math.sin(angle) * diagonal,
                    ),
                )
            )
        return AssistOverlayGeometry(lines=tuple(lines), markers=((center[0], center[1], "+"),))

    def _build_concentric(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        center = assist.point("center")
        if center is None:
            return AssistOverlayGeometry()
        ratio = assist.number("ratio", 1.0, source="options")
        ratio = ratio if 0.05 <= ratio <= 20.0 else 1.0
        reach = math.hypot(*size)
        step = reach / (self.CONCENTRIC_RINGS + 1)
        ellipses = tuple(
            (center[0], center[1], step * k, step * k * ratio)
            for k in range(1, self.CONCENTRIC_RINGS + 1)
        )
        return AssistOverlayGeometry(ellipses=ellipses, markers=((center[0], center[1], "+"),))

    def _build_perspective(
        self, assist: DrawingAssist, size: tuple[float, float]
    ) -> AssistOverlayGeometry:
        points = assist.points("vanishing_points")[:3]
        if not points:
            return AssistOverlayGeometry()
        lines: list[tuple[Point, Point]] = []
        diagonal = math.hypot(*size)
        corners = ((0.0, 0.0), (size[0], 0.0), (0.0, size[1]), (size[0], size[1]))
        for vp in points:
            for k in range(self.PERSPECTIVE_FAN):
                angle = 2.0 * math.pi * k / self.PERSPECTIVE_FAN
                lines.append(
                    (vp, (vp[0] + math.cos(angle) * diagonal, vp[1] + math.sin(angle) * diagonal))
                )
            for corner in corners:
                lines.append((vp, corner))
        if len(points) >= 2:
            horizon = self._extend(points[0], points[1], size)
            if horizon is not None:
                lines.append(horizon)
        markers = tuple((vp[0], vp[1], f"VP{i + 1}") for i, vp in enumerate(points))
        truncated = len(lines) > self.MAX_LINES
        return AssistOverlayGeometry(
            lines=tuple(lines[: self.MAX_LINES]), markers=markers, truncated=truncated
        )

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _extend(p0: Point, p1: Point, size: tuple[float, float]) -> tuple[Point, Point] | None:
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        reach = math.hypot(*size) * 2.0
        ux, uy = dx / length, dy / length
        return ((p0[0] - ux * reach, p0[1] - uy * reach), (p0[0] + ux * reach, p0[1] + uy * reach))
