"""Bounded, JSON-safe lighting and height-profile parameters for relief styles."""

from __future__ import annotations

import math
from itertools import pairwise
from typing import Any

import numpy as np

MAX_LIGHTS = 8
MAX_PROFILE_POINTS = 32
IDENTITY_PROFILE = {"points": [[0.0, 0.0], [1.0, 1.0]], "interpolation": "linear"}


def _number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("number required")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("finite number required")
    return max(low, min(high, number))


def clean_lights(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("light list required")
    result: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(value[:MAX_LIGHTS]):
        if not isinstance(item, dict):
            raise ValueError("light object required")
        identifier = str(item.get("id", f"light-{index + 1}"))[:128]
        if not identifier or identifier in used:
            identifier = f"light-{index + 1}"
            while identifier in used:
                identifier += "-copy"
        used.add(identifier)
        color = item.get("color", [255, 255, 255, 255])
        if not isinstance(color, (list, tuple)) or len(color) not in (3, 4):
            raise ValueError("RGBA color required")
        rgba = [round(_number(c, 0, 255)) for c in color]
        if len(rgba) == 3:
            rgba.append(255)
        enabled = item.get("enabled", True)
        if not isinstance(enabled, (bool, int)):
            raise ValueError("boolean required")
        result.append(
            {
                "id": identifier,
                "enabled": bool(enabled),
                "angle": _number(item.get("angle", 135), 0, 360),
                "elevation": _number(item.get("elevation", 45), 0, 90),
                "intensity": _number(item.get("intensity", 1), 0, 2),
                "color": rgba,
            }
        )
    return result


def relief_lights(params: dict[str, Any]) -> list[dict[str, Any]]:
    return clean_lights(params.get("lights", [])) or clean_lights(
        [
            {
                "id": "primary",
                "angle": params.get("angle", 135),
                "elevation": params.get("elevation", 45),
            }
        ]
    )


def clean_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("interpolation", "linear") not in {
        "linear",
        "smooth",
    }:
        raise ValueError("profile object required")
    points = value.get("points")
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        raise ValueError("at least two control points required")
    cleaned: dict[float, float] = {}
    for point in points[:MAX_PROFILE_POINTS]:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("XY point required")
        cleaned[_number(point[0], 0, 1)] = _number(point[1], 0, 1)
    ordered = sorted(cleaned.items())
    if len(ordered) < 2:
        raise ValueError("distinct points required")
    ordered[0] = (0.0, ordered[0][1])
    ordered[-1] = (1.0, ordered[-1][1])
    # Avoid tiny segments that amplify dragging/rounding noise.
    filtered = [ordered[0]]
    for x, y in ordered[1:-1]:
        if x - filtered[-1][0] >= 0.001 and x <= 0.999:
            filtered.append((x, y))
    filtered.append(ordered[-1])
    return {
        "points": [[x, y] for x, y in filtered],
        "interpolation": value.get("interpolation", "linear"),
    }


def profile_knots(profile: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    points = clean_profile(profile)["points"]
    slopes = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in pairwise(points)]
    tangents = [slopes[0]]
    for left, right in pairwise(slopes):
        tangents.append(2 * left * right / (left + right) if left * right > 0 else 0.0)
    tangents.append(slopes[-1])
    return [(p[0], p[1], m, 0.0) for p, m in zip(points, tangents, strict=True)]


def evaluate_profile(values: np.ndarray, profile: dict[str, Any]) -> np.ndarray:
    profile = clean_profile(profile)
    knots = np.asarray(profile_knots(profile))
    values = np.clip(values, 0, 1)
    indices = np.clip(np.searchsorted(knots[:, 0], values, side="right") - 1, 0, len(knots) - 2)
    a, b = knots[indices], knots[indices + 1]
    width = b[..., 0] - a[..., 0]
    t = (values - a[..., 0]) / width
    if profile["interpolation"] == "linear":
        return np.asarray(a[..., 1] * (1 - t) + b[..., 1] * t)
    result = (
        (2 * t**3 - 3 * t * t + 1) * a[..., 1]
        + (t**3 - 2 * t * t + t) * width * a[..., 2]
        + (-2 * t**3 + 3 * t * t) * b[..., 1]
        + (t**3 - t * t) * width * b[..., 2]
    )
    return np.asarray(
        np.clip(result, np.minimum(a[..., 1], b[..., 1]), np.maximum(a[..., 1], b[..., 1]))
    )
