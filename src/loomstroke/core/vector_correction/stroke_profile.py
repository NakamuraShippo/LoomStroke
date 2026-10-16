"""Normalised width profile of a vector stroke (CT-15 ``loomstroke:stroke_profile`` v1/v2).

``samples`` are ``(t, scale)`` pairs over arc-length ``t`` in [0, 1]; ``scale``
multiplies the style's constant stroke width.  v1 payloads carry
``width_profile`` / ``pressure`` dict lists, v2 adds editable ``width_stops``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any, ClassVar

__all__ = ["StrokeProfile"]

Sample = tuple[float, float]


@dataclass(frozen=True, slots=True)
class StrokeProfile:
    base_width: float = 1.0
    samples: tuple[Sample, ...] = ()
    """Raw per-sample scales (v1 ``width_profile`` or pressure), sorted by ``t``."""
    stops: tuple[Sample, ...] = ()
    """Editable v2 ``width_stops``; when present they override ``samples``."""
    interpolation: str = "linear"
    source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    SCHEMA_VERSION: ClassVar[int] = 2
    MAX_STOPS: ClassVar[int] = 64
    MAX_SAMPLES: ClassVar[int] = 256
    EPSILON: ClassVar[float] = 1e-3

    # -- queries -----------------------------------------------------------------------

    @property
    def curve(self) -> tuple[Sample, ...]:
        return self.stops if self.stops else self.samples

    @property
    def is_variable(self) -> bool:
        return any(abs(scale - 1.0) > self.EPSILON for _t, scale in self.curve)

    def scale_at(self, t: float) -> float:
        curve = self.curve
        if not curve:
            return 1.0
        t = 0.0 if not math.isfinite(t) else max(0.0, min(1.0, t))
        if t <= curve[0][0]:
            return curve[0][1]
        if t >= curve[-1][0]:
            return curve[-1][1]
        for (t0, s0), (t1, s1) in pairwise(curve):
            if t0 <= t <= t1:
                if t1 - t0 < 1e-12:
                    return s1
                ratio = (t - t0) / (t1 - t0)
                if self.interpolation == "monotone":
                    ratio = ratio * ratio * (3.0 - 2.0 * ratio)  # smoothstep keeps monotonic
                return s0 + (s1 - s0) * ratio
        return curve[-1][1]

    def width_at(self, t: float) -> float:
        return max(0.0, self.base_width * self.scale_at(t))

    def with_stops(self, stops: Sequence[Sample]) -> StrokeProfile:
        return replace(self, stops=self._normalise(stops, self.MAX_STOPS))

    # -- (de)serialisation -------------------------------------------------------------

    @classmethod
    def from_raw(cls, raw: Any, base_width: float) -> StrokeProfile | None:
        if not isinstance(raw, Mapping):
            return None
        width = cls._float(raw.get("base_stroke_width"), base_width)
        samples = cls._normalise(cls._pairs(raw.get("width_profile"), "scale"), cls.MAX_SAMPLES)
        if not samples:
            samples = cls._normalise(cls._pairs(raw.get("pressure"), "value"), cls.MAX_SAMPLES)
        stops = cls._normalise(cls._pairs(raw.get("width_stops"), "scale"), cls.MAX_STOPS)
        interpolation = str(raw.get("interpolation") or "linear")
        if interpolation not in ("linear", "monotone"):
            interpolation = "linear"
        known = {
            "schema_version",
            "base_stroke_width",
            "width_profile",
            "width_stops",
            "interpolation",
            "source",
        }
        extra = {key: value for key, value in raw.items() if key not in known}
        if not samples and not stops:
            return cls(width, (), (), interpolation, str(raw.get("source") or ""), extra)
        return cls(width, samples, stops, interpolation, str(raw.get("source") or ""), extra)

    def to_raw(self) -> dict[str, Any]:
        payload: dict[str, Any] = dict(self.extra)
        payload["schema_version"] = self.SCHEMA_VERSION
        payload["base_stroke_width"] = float(self.base_width)
        if self.source:
            payload["source"] = self.source
        if self.samples:
            payload["width_profile"] = [{"t": t, "scale": s} for t, s in self.samples]
        if self.stops:
            payload["width_stops"] = [{"t": t, "scale": s} for t, s in self.stops]
        payload["interpolation"] = self.interpolation
        return payload

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) and number > 0 else default

    @classmethod
    def _pairs(cls, raw: Any, value_key: str) -> list[Sample]:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return []
        pairs: list[Sample] = []
        for entry in raw:
            if isinstance(entry, Mapping):
                t, value = entry.get("t"), entry.get(value_key)
            elif isinstance(entry, Sequence) and len(entry) == 2:
                t, value = entry[0], entry[1]
            else:
                continue
            try:
                t_f, v_f = float(t), float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(t_f) and math.isfinite(v_f):
                pairs.append((max(0.0, min(1.0, t_f)), max(0.0, v_f)))
        return pairs

    @staticmethod
    def _normalise(pairs: Sequence[Sample], limit: int) -> tuple[Sample, ...]:
        ordered = sorted(((float(t), float(s)) for t, s in pairs), key=lambda p: p[0])
        if len(ordered) > limit:
            last = len(ordered) - 1
            indexes = sorted({round(i * last / (limit - 1)) for i in range(limit)})
            ordered = [ordered[i] for i in indexes]
        return tuple(ordered)
