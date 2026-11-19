"""Space dabs by distance, with a continuously varying pressure-dependent paint density."""

from __future__ import annotations

import math

from loomstroke.host_api import StrokePoint

PaintDab = tuple[int, int] | tuple[int, int, float, float, float]


def _tilt(axes: tuple[float, float] | None) -> tuple[float, float]:
    if axes is None or not all(math.isfinite(value) for value in axes):
        return 0.0, 0.0
    x, y = axes
    return min(90.0, max(-90.0, x)), min(90.0, max(-90.0, y))


def _contact(x: int, y: int, pressure: float, tilt_x: float, tilt_y: float) -> PaintDab:
    if pressure == 1.0 and tilt_x == tilt_y == 0.0:
        return x, y
    # Pressure/tilt at the same interpolated point can differ by an arithmetic ULP when
    # a driver splits a straight segment. Keep far more precision than the hardware reports.
    return x, y, round(pressure, 6), round(tilt_x, 6), round(tilt_y, 6)


class PaintDabSampler:
    def __init__(self, first: StrokePoint, diameter: int) -> None:
        self._step = max(1.0, diameter / 16)
        self.position = first.pos
        self._pressure = self._clamp_pressure(first.pressure)
        self._tilt = _tilt(first.tilt)
        self.last_dab = _contact(
            round(first.pos[0]), round(first.pos[1]), self._pressure, *self._tilt
        )
        self._fraction = 0.0

    @staticmethod
    def _clamp_pressure(pressure: float) -> float:
        return max(0.0, min(1.0, pressure)) if math.isfinite(pressure) else 1.0

    def advance(self, point: StrokePoint, *, lift: bool = False) -> list[PaintDab]:
        x, y = self.position
        dx, dy = point.pos[0] - x, point.pos[1] - y
        distance = math.hypot(dx, dy)
        if not math.isfinite(distance):
            return []
        # At pen-up, drivers may reset all axes while supplying a final position.
        released = lift and point.pressure <= 0
        pressure = self._pressure if released else self._clamp_pressure(point.pressure)
        tilt = self._tilt if released else _tilt(point.tilt)
        start_rate = (0.35 + 0.65 * self._pressure) / self._step
        end_rate = (0.35 + 0.65 * pressure) / self._step
        # Integrate linearly interpolated pressure so delivery in smaller events leaves the
        # number and positions of dabs unchanged. One unit of accumulated density emits one dab.
        total = self._fraction + distance * (start_rate + end_rate) / 2
        count = math.floor(total)
        dabs: list[PaintDab] = []
        dp = pressure - self._pressure
        tx, ty = self._tilt
        dtx, dty = tilt[0] - tx, tilt[1] - ty
        for i in range(1, count + 1):
            target = (i - self._fraction) / distance
            root = math.sqrt(max(0.0, start_rate**2 + 2 * (end_rate - start_rate) * target))
            t = 2 * target / (start_rate + root)
            dab = round(x + dx * t), round(y + dy * t)
            if dab != self.last_dab[:2]:
                self.last_dab = _contact(*dab, self._pressure + dp * t, tx + dtx * t, ty + dty * t)
                dabs.append(self.last_dab)
        stationary_touch = not distance and not self._pressure and pressure > 0 and not lift
        self._fraction = total - count
        self._pressure = pressure
        self._tilt = tilt
        self.position = point.pos
        if lift or stationary_touch:
            end = round(point.pos[0]), round(point.pos[1])
            if stationary_touch or end != self.last_dab[:2]:
                self.last_dab = _contact(*end, pressure, *tilt)
                dabs.append(self.last_dab)
        return dabs
