"""Filter Pen sampling, separate from Basic Pen's input implementation."""

from __future__ import annotations

import math
from dataclasses import replace

from loomstroke.core.command_types import StrokePoint as CommandPoint
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.host_api import StrokePoint

from .settings import FilterPenSettings


class FilterPenStrokeInput:
    def __init__(self, settings: FilterPenSettings) -> None:
        self.settings = settings
        self.last: tuple[float, float] | None = None
        self.smoothed: tuple[float, float] | None = None
        self.accumulator = 0.0
        self.path: list[StrokePoint] = []

    def normalize(self, point: StrokePoint) -> StrokePoint:
        if not all(math.isfinite(float(value)) for value in (*point.pos, point.pressure)):
            raise ValueError("Filter Pen input must contain finite coordinates and pressure.")
        pressure = max(0.0, min(1.0, float(point.pressure)))
        if not self.settings.pressure_sensitive:
            pressure = 1.0
        tilt = None
        if self.settings.tilt_angle and point.tilt:
            tilt = tuple(max(-60.0, min(60.0, float(value))) for value in point.tilt[:2])
        return replace(point, pressure=pressure, tilt=tilt)

    def begin(self, point: StrokePoint) -> list[StrokePoint]:
        point = self.normalize(point)
        self.last = point.pos
        self.smoothed = point.pos
        self.accumulator = 0.0
        self.path = [point]
        return [point]

    def _smooth(self, pos: tuple[float, float]) -> tuple[float, float]:
        if self.smoothed is None or self.settings.smoothing <= 0:
            self.smoothed = pos
        else:
            alpha = max(0.1, 1.0 - 0.9 * self.settings.smoothing)
            self.smoothed = tuple(
                old + (new - old) * alpha for old, new in zip(self.smoothed, pos, strict=True)
            )
        return self.smoothed

    def _interpolate(self, point: StrokePoint, end: tuple[float, float]) -> list[StrokePoint]:
        if self.last is None:
            return self.begin(replace(point, pos=end))
        start = self.last
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        step = max(1.0, self.settings.size * self.settings.spacing)
        count = int((distance + self.accumulator) / step)
        result = []
        if distance > 0:
            for index in range(1, count + 1):
                fraction = (index * step - self.accumulator) / distance
                pos = (
                    start[0] + (end[0] - start[0]) * fraction,
                    start[1] + (end[1] - start[1]) * fraction,
                )
                result.append(replace(point, pos=pos))
            self.accumulator = distance + self.accumulator - count * step
        self.last = end
        self.path.extend(result)
        return result

    def move(self, points: list[StrokePoint]) -> list[StrokePoint]:
        result = []
        for raw in points:
            point = self.normalize(raw)
            result.extend(self._interpolate(point, self._smooth(point.pos)))
        return result

    def finish(self, point: StrokePoint) -> list[StrokePoint]:
        point = self.normalize(point)
        result = self._interpolate(point, self._smooth(point.pos))
        if self.settings.smoothing > 0:
            result.extend(self._interpolate(point, point.pos))
        if not self.path or math.dist(self.path[-1].pos, point.pos) > 1e-6:
            result.append(point)
            self.path.append(point)
        self.last = None
        self.smoothed = None
        self.accumulator = 0.0
        return result

    @staticmethod
    def command_points(points: list[StrokePoint]) -> list[CommandPoint]:
        return [
            CommandPoint(
                x=point.pos[0],
                y=point.pos[1],
                pressure=point.pressure,
                tilt_x=point.tilt[0] if point.tilt else 0.0,
                tilt_y=point.tilt[1] if point.tilt else 0.0,
                timestamp=point.time or 0.0,
            )
            for point in points
        ]


# Compatibility aliases for existing imports.
EffectPenStrokeInput = FilterPenStrokeInput


_bind_legacy_names(
    __name__,
    {"EffectPenSettings": "FilterPenSettings", "EffectPenStrokeInput": "FilterPenStrokeInput"},
)
