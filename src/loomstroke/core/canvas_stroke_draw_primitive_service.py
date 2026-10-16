"""QPainter stroke draw primitives for ``SimpleCanvasService``."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from .pen_taper_model import point_width_scale
from .pressure_stroke_point import point_opacity_scale, point_tone_pressure


class CanvasStrokeDrawPrimitiveService:
    """Draw paint / erase stroke primitives without mutating canvas state."""

    def draw_paint_stroke(
        self,
        painter: QPainter,
        points: list[Any],
        brush: Any,
        color: QColor,
        *,
        offset: QRect | None = None,
    ) -> None:
        pen, base_size, base_alpha, base_rgb, tone_range, tone_gamma = (
            self.prepare_paint_stroke_profile(brush, color)
        )

        if offset is not None:
            painter.save()
            painter.translate(-offset.x(), -offset.y())

        if len(points) >= 2:
            self.draw_paint_stroke_polyline(
                painter,
                points=points,
                pen=pen,
                base_size=base_size,
                base_alpha=base_alpha,
                base_rgb=base_rgb,
                tone_range=tone_range,
                tone_gamma=tone_gamma,
            )
        else:
            self.draw_paint_stroke_single_point(
                painter,
                points=points,
                pen=pen,
                base_size=base_size,
                base_alpha=base_alpha,
                base_rgb=base_rgb,
                tone_range=tone_range,
                tone_gamma=tone_gamma,
            )

        if offset is not None:
            painter.restore()

    def prepare_paint_stroke_profile(
        self,
        brush: Any,
        color: QColor,
    ) -> tuple[QPen, float, int, tuple[int, int, int], float, float]:
        pen = QPen(color)
        pen.setWidth(max(1, int(getattr(brush, "size", 2))))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        base_size = max(1.0, float(getattr(brush, "size", 2.0)))
        base_opacity = max(0.0, min(1.0, float(getattr(brush, "opacity", 1.0))))

        base_alpha = None
        if hasattr(brush, "color") and brush.color and len(brush.color) >= 4:
            base_alpha = max(0, min(255, int(brush.color[3])))
        if base_alpha is None:
            base_alpha = int(255 * base_opacity)
        base_rgb = (int(color.red()), int(color.green()), int(color.blue()))
        tone_range = max(0.0, min(1.0, float(getattr(brush, "tone_range", 1.0))))
        tone_gamma = max(0.1, min(2.0, float(getattr(brush, "tone_gamma", 0.6))))

        return pen, base_size, base_alpha, base_rgb, tone_range, tone_gamma

    @staticmethod
    def paint_tilt_pair(point: Any) -> tuple[float, float] | None:
        if not hasattr(point, "tilt_x") or not hasattr(point, "tilt_y"):
            return None
        try:
            tilt_x = float(getattr(point, "tilt_x", 0.0) or 0.0)
            tilt_y = float(getattr(point, "tilt_y", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        return (tilt_x, tilt_y)

    @staticmethod
    def paint_segment_pressure(p1: Any, p2: Any) -> float:
        if hasattr(p1, "pressure") and hasattr(p2, "pressure"):
            return float(max(0.0, min(1.0, (p1.pressure + p2.pressure) / 2.0)))
        return 1.0

    @staticmethod
    def paint_point_pressure(point: Any) -> float:
        pressure = getattr(point, "pressure", 1.0)
        if not hasattr(point, "opacity_scale"):
            pressure = pressure or 1.0
        return max(0.0, min(1.0, float(pressure)))

    @classmethod
    def paint_tilt_scale(cls, p1: Any, p2: Any) -> tuple[float, float]:
        t1 = cls.paint_tilt_pair(p1)
        t2 = cls.paint_tilt_pair(p2)
        if t1 is None and t2 is None:
            return (1.0, 1.0)
        t1x, t1y = t1 or (0.0, 0.0)
        t2x, t2y = t2 or (0.0, 0.0)
        if abs(t1x) + abs(t1y) + abs(t2x) + abs(t2y) <= 1e-6:
            return (1.0, 1.0)
        avg_x = (t1x + t2x) * 0.5
        avg_y = (t1y + t2y) * 0.5
        tilt_mag = min(1.0, math.sqrt(avg_x * avg_x + avg_y * avg_y) / 60.0)
        if tilt_mag <= 0.0:
            return (1.0, 1.0)
        width_scale = 0.5 + 0.5 * tilt_mag
        alpha_scale = 0.7 + 0.3 * tilt_mag
        return width_scale, alpha_scale

    @staticmethod
    def paint_tone_color(
        *,
        base_rgb: tuple[int, int, int],
        pressure: float,
        tone_range: float,
        tone_gamma: float,
    ) -> QColor:
        p = max(0.0, min(1.0, pressure))
        tone_pressure = p**tone_gamma
        tone_factor = (1.0 - tone_range) + tone_range * tone_pressure
        inv = 1.0 - tone_factor
        r = round(base_rgb[0] * tone_factor + 255 * inv)
        g = round(base_rgb[1] * tone_factor + 255 * inv)
        b = round(base_rgb[2] * tone_factor + 255 * inv)
        return QColor(r, g, b)

    def draw_paint_stroke_polyline(
        self,
        painter: QPainter,
        points: list[Any],
        pen: QPen,
        *,
        base_size: float,
        base_alpha: int,
        base_rgb: tuple[int, int, int],
        tone_range: float,
        tone_gamma: float,
    ) -> None:
        prev_width = -1
        prev_alpha = -1
        prev_pressure = -1.0
        segment_pen = QPen(pen)
        for idx in range(len(points) - 1):
            p1 = points[idx]
            p2 = points[idx + 1]
            pressure = self.paint_segment_pressure(p1, p2)
            width_scale, alpha_scale = self.paint_tilt_scale(p1, p2)
            taper = (point_width_scale(p1) + point_width_scale(p2)) * 0.5
            if taper <= 0.0:
                continue
            seg_width = (
                max(0.01, base_size * pressure * width_scale * taper)
                if taper < 1.0
                else max(1, int(base_size * pressure * width_scale))
            )
            opacity_scale = (point_opacity_scale(p1) + point_opacity_scale(p2)) * 0.5
            tone_pressure = (point_tone_pressure(p1) + point_tone_pressure(p2)) * 0.5
            seg_alpha = int(max(0.0, min(255.0, base_alpha * alpha_scale * opacity_scale)))

            if seg_width != prev_width or seg_alpha != prev_alpha or tone_pressure != prev_pressure:
                segment_pen.setWidthF(seg_width)
                seg_color = self.paint_tone_color(
                    base_rgb=base_rgb,
                    pressure=tone_pressure,
                    tone_range=tone_range,
                    tone_gamma=tone_gamma,
                )
                seg_color.setAlpha(seg_alpha)
                segment_pen.setColor(seg_color)
                painter.setPen(segment_pen)
                prev_width = seg_width
                prev_alpha = seg_alpha
                prev_pressure = tone_pressure

            painter.drawLine(int(p1.x), int(p1.y), int(p2.x), int(p2.y))

    def draw_paint_stroke_single_point(
        self,
        painter: QPainter,
        points: list[Any],
        pen: QPen,
        *,
        base_size: float,
        base_alpha: int,
        base_rgb: tuple[int, int, int],
        tone_range: float,
        tone_gamma: float,
    ) -> None:
        point = points[0]
        pressure = self.paint_point_pressure(point)
        width_scale, alpha_scale = self.paint_tilt_scale(point, point)
        taper = point_width_scale(point)
        if taper <= 0.0:
            return
        seg_width = (
            max(0.01, base_size * pressure * width_scale * taper)
            if taper < 1.0
            else max(1, int(base_size * pressure * width_scale))
        )
        pen.setWidthF(seg_width)

        point_color = self.paint_tone_color(
            base_rgb=base_rgb,
            pressure=point_tone_pressure(point),
            tone_range=tone_range,
            tone_gamma=tone_gamma,
        )
        point_color.setAlpha(
            int(max(0.0, min(255.0, base_alpha * alpha_scale * point_opacity_scale(point))))
        )
        point_pen = QPen(pen)
        point_pen.setColor(point_color)
        painter.setPen(point_pen)
        painter.drawPoint(int(point.x), int(point.y))

    def draw_erase_stroke(
        self,
        painter: QPainter,
        points: list[Any],
        base_size: int,
        *,
        color: QColor,
        offset: QRect | None = None,
    ) -> None:
        pen = QPen(color)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        if offset is not None:
            painter.save()
            painter.translate(-offset.x(), -offset.y())

        if len(points) == 1:
            pressure = getattr(points[0], "pressure", 1.0) or 1.0
            pen.setWidth(max(1, int(base_size * pressure)))
            painter.setPen(pen)
            painter.drawPoint(int(points[0].x), int(points[0].y))
        else:
            for idx in range(len(points) - 1):
                p1 = points[idx]
                p2 = points[idx + 1]
                p1_pressure = getattr(p1, "pressure", 1.0) or 1.0
                p2_pressure = getattr(p2, "pressure", 1.0) or 1.0
                pressure = max(0.1, (p1_pressure + p2_pressure) / 2.0)
                pen.setWidth(max(1, int(base_size * pressure)))
                painter.setPen(pen)
                painter.drawLine(int(p1.x), int(p1.y), int(p2.x), int(p2.y))

        if offset is not None:
            painter.restore()
