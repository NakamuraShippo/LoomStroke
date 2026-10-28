"""Seeded inward/outer radial flash contours, independent of Qt."""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from .effect_line_geometry import BBox, Point, validate_bbox


def flash_params(raw: Mapping[str, object]) -> dict[str, float | int]:
    defaults = {
        "center_x": 0.0,
        "center_y": 0.0,
        "inner": 0.35,
        "outer": 1.0,
        "count": 64,
        "variation": 0.3,
        "seed": 20261028,
        "white": 0,
    }
    ranges = {
        "center_x": (-1, 1),
        "center_y": (-1, 1),
        "inner": (0, 1),
        "outer": (0.001, 1),
        "count": (3, 256),
        "variation": (0, 1),
        "seed": (0, 2147483647),
        "white": (0, 1),
    }
    result = {}
    for key, default in defaults.items():
        value = float(raw.get(key, default))
        if not math.isfinite(value):
            raise ValueError("Invalid flash parameter")
        low, high = ranges[key]
        value = max(low, min(high, value))
        result[key] = int(value) if key in ("count", "seed", "white") else value
    if result["inner"] >= result["outer"]:
        raise ValueError("Inner diameter must be smaller than outer diameter")
    return result


def flash_polygons(
    bbox: BBox, raw: Mapping[str, object], *, inward: bool
) -> tuple[tuple[Point, ...], ...]:
    validate_bbox(bbox)
    p = flash_params(raw)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    center = (
        (bbox[0] + bbox[2]) / 2 + p["center_x"] * w / 2,
        (bbox[1] + bbox[3]) / 2 + p["center_y"] * h / 2,
    )
    radius = min(w, h) / 2
    inner, outer = p["inner"] * radius, p["outer"] * radius
    count = int(p["count"])
    step = math.tau / count
    rng = random.Random(p["seed"])

    def point(angle, r):
        return center[0] + math.cos(angle) * r, center[1] + math.sin(angle) * r

    star, sectors = [], []
    for i in range(count):
        angle = step * i
        peak = outer - (outer - inner) * 0.45 * p["variation"] * rng.random()
        valley = inner + (outer - inner) * 0.25 * p["variation"] * rng.random()
        if inward:
            sectors.append(
                (
                    point(angle - step / 2, outer),
                    point(angle + step / 2, outer),
                    point(angle + step / 2, outer * 0.9),
                    point(angle, valley),
                    point(angle - step / 2, outer * 0.9),
                )
            )
        else:
            star.extend((point(angle, peak), point(angle + step / 2, valley)))
    return tuple(sectors) if inward else (tuple(star),)
