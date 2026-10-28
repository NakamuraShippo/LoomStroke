"""Bounded, Qt-free composition overlay geometry in page coordinates."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from itertools import pairwise
from typing import Any

Rect = tuple[float, float, float, float]
Line = tuple[tuple[float, float], tuple[float, float]]
KINDS = ("thirds", "golden", "spiral", "diagonals", "center", "safe")
PHI = (1 + math.sqrt(5)) / 2


def reference_rect(
    size: tuple[float, float],
    reference: str,
    *,
    guides: Iterable[Mapping[str, Any]] = (),
    selection: Rect | None = None,
) -> Rect | None:
    if reference == "canvas":
        return (0.0, 0.0, *size)
    if reference == "selection":
        return selection
    if reference != "trim":
        return None
    axes: dict[str, list[float]] = {"horizontal": [], "vertical": []}
    for guide in guides:
        axis = guide.get("orientation")
        if str(guide.get("id", "")).startswith("tpl-trim-") and axis in axes:
            try:
                value = float(guide["position"])
            except (TypeError, ValueError, KeyError):
                continue
            if math.isfinite(value):
                axes[axis].append(value)
    xs, ys = sorted(set(axes["vertical"])), sorted(set(axes["horizontal"]))
    if len(xs) != 2 or len(ys) != 2:
        return None
    return xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]


def composition_lines(
    rect: Rect, kinds: Iterable[str], safe_percent: float = 10.0
) -> tuple[Line, ...]:
    x, y, width, height = rect
    if any(not math.isfinite(v) for v in rect) or min(width, height) <= 0:
        return ()
    if not math.isfinite(safe_percent) or not 0 <= safe_percent <= 49:
        return ()
    selected = {kind for kind in kinds if isinstance(kind, str)}
    lines: list[Line] = []
    for kind, fractions in (
        ("thirds", (1 / 3, 2 / 3)),
        ("golden", (1 - 1 / PHI, 1 / PHI)),
        ("center", (0.5,)),
    ):
        if kind in selected:
            for fraction in fractions:
                lines.extend(
                    (
                        ((x + width * fraction, y), (x + width * fraction, y + height)),
                        ((x, y + height * fraction), (x + width, y + height * fraction)),
                    )
                )
    if "diagonals" in selected:
        lines.extend((((x, y), (x + width, y + height)), ((x + width, y), (x, y + height))))
    if "safe" in selected:
        dx, dy = width * safe_percent / 100, height * safe_percent / 100
        corners = (
            (x + dx, y + dy),
            (x + width - dx, y + dy),
            (x + width - dx, y + height - dy),
            (x + dx, y + height - dy),
        )
        lines.extend((corners[i], corners[(i + 1) % 4]) for i in range(4))
    if "spiral" in selected:
        cx, cy, radius = x + width / 2, y + height / 2, min(width, height) / 2
        points = []
        for i in range(129):
            angle = -4 * math.pi + i * math.pi / 32
            r = radius * math.exp(angle * 2 * math.log(PHI) / math.pi)
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        lines.extend(pairwise(points))
    return tuple(lines)
