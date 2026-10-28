"""Bounded deterministic effect-line polygons; no Qt or document dependencies."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

Point = tuple[float, float]
BBox = tuple[float, float, float, float]


def streamline_params(raw: Mapping[str, object]) -> dict[str, float | int]:
    defaults = {
        "direction": 0.0,
        "count": 32,
        "length_jitter": 0.3,
        "width": 0.01,
        "taper": 0.25,
        "seed": 20261028,
    }
    ranges = {
        "direction": (-360, 360),
        "count": (1, 512),
        "length_jitter": (0, 1),
        "width": (0.0001, 0.2),
        "taper": (0, 0.5),
        "seed": (0, 2147483647),
    }
    result = {}
    for key, default in defaults.items():
        value = float(raw.get(key, default))
        if not math.isfinite(value):
            raise ValueError("Non-finite effect parameter")
        lo, hi = ranges[key]
        value = min(hi, max(lo, value))
        result[key] = int(value) if key in ("count", "seed") else value
    return result


def validate_bbox(bbox: BBox) -> None:
    if not all(math.isfinite(v) for v in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError("Invalid effect bounds")


def _clip_polygon(points: list[Point], bbox: BBox) -> tuple[Point, ...]:
    for axis, boundary, lower in (
        (0, bbox[0], True),
        (0, bbox[2], False),
        (1, bbox[1], True),
        (1, bbox[3], False),
    ):
        previous = points[-1] if points else (0.0, 0.0)
        clipped = []
        for current in points:
            inside = current[axis] >= boundary if lower else current[axis] <= boundary
            was_inside = previous[axis] >= boundary if lower else previous[axis] <= boundary
            if inside != was_inside:
                t = (boundary - previous[axis]) / (current[axis] - previous[axis])
                clipped.append(
                    (
                        previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]),
                    )
                )
            if inside:
                clipped.append(current)
            previous = current
        points = clipped
    return tuple(points)


def streamline_polygons(bbox: BBox, raw: Mapping[str, object]) -> tuple[tuple[Point, ...], ...]:
    validate_bbox(bbox)
    p = streamline_params(raw)
    angle = math.radians(p["direction"])
    u = (math.cos(angle), math.sin(angle))
    v = (-u[1], u[0])
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    corners = [(x - cx, y - cy) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
    offsets = [x * v[0] + y * v[1] for x, y in corners]
    rng = random.Random(p["seed"])
    polygons = []
    count = int(p["count"])
    thickness = min(bbox[2] - bbox[0], bbox[3] - bbox[1]) * p["width"]
    for i in range(count):
        offset = min(offsets) + (max(offsets) - min(offsets)) * (i + 0.5) / count
        base = (cx + offset * v[0], cy + offset * v[1])
        low, high = -math.inf, math.inf
        for axis in (0, 1):
            if abs(u[axis]) < 1e-12:
                continue
            ends = [(bbox[axis] - base[axis]) / u[axis], (bbox[axis + 2] - base[axis]) / u[axis]]
            low, high = max(low, min(ends)), min(high, max(ends))
        length = max(0, high - low) * (1 - rng.random() * p["length_jitter"])
        if length < 1e-9:
            continue
        center = (low + high) / 2
        low, high = center - length / 2, center + length / 2
        taper = length * p["taper"]
        local = [
            (low, 0),
            (low + taper, -thickness / 2),
            (high - taper, -thickness / 2),
            (high, 0),
            (high - taper, thickness / 2),
            (low + taper, thickness / 2),
        ]
        polygon = _clip_polygon(
            [(base[0] + t * u[0] + w * v[0], base[1] + t * u[1] + w * v[1]) for t, w in local], bbox
        )
        if len(polygon) >= 3:
            polygons.append(polygon)
    return tuple(polygons)
