"""Preview-only geometric diagnostics; no alpha/text recognition or document mutation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import PanelFrameError, Point, Rect, validate_rect


@dataclass(frozen=True, slots=True)
class ContentDiagnostic:
    item_id: str
    status: str
    uncovered_area_px2: float = 0.0
    show_ghost: bool = False


def _area(points: tuple[Point, ...]) -> float:
    return (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(points, points[1:] + points[:1], strict=True)
            )
        )
        / 2
    )


def _clip(points, axis, boundary, keep_above):
    result = []
    for previous, current in zip(points[-1:] + points[:-1], points, strict=True):
        pin = previous[axis] >= boundary if keep_above else previous[axis] <= boundary
        cin = current[axis] >= boundary if keep_above else current[axis] <= boundary
        if pin != cin:
            ratio = (boundary - previous[axis]) / (current[axis] - previous[axis])
            result.append(tuple(previous[i] + ratio * (current[i] - previous[i]) for i in (0, 1)))
        if cin:
            result.append(current)
    return tuple(result)


def diagnose_content(  # noqa: PLR0912 - explicit inactive/unknown/coverage diagnosis states
    item_id: str,
    footprint: tuple[Point, ...] | None,
    clip_rect: Rect,
    *,
    image: bool = False,
    fit_policy: str = "keep",
    visible: bool = True,
    clip_enabled: bool = True,
    clip_polygon: tuple[Point, ...] | None = None,
) -> ContentDiagnostic:
    """Footprint is a transformed convex quad/bounds supplied by a capable adapter.

    None means unknown, never 'safe'. Contain whitespace is intentional.
    Each shared target must be diagnosed separately, not via their union AABB.
    """
    validate_rect(clip_rect)
    if not item_id:
        raise PanelFrameError("diagnostic requires content identity")
    if not visible or not clip_enabled:
        return ContentDiagnostic(item_id, "inactive")
    if footprint is None:
        return ContentDiagnostic(item_id, "unknown")
    if len(footprint) not in {3, 4} or not all(
        len(p) == 2 and all(math.isfinite(v) for v in p) for p in footprint
    ):
        raise PanelFrameError("invalid content footprint")
    turns = [
        (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        for a, b, c in zip(
            footprint, footprint[1:] + footprint[:1], footprint[2:] + footprint[:2], strict=True
        )
    ]
    if (any(t > 1e-9 for t in turns) and any(t < -1e-9 for t in turns)) or _area(footprint) <= 1e-9:
        raise PanelFrameError("content footprint must be convex and nondegenerate")
    x, y, w, h = clip_rect
    clipped = footprint
    target_area = w * h
    if clip_polygon is not None:
        from .convex_clip import validate_polygon  # noqa: PLC0415

        polygon = validate_polygon(clip_polygon)
        target_area = _area(polygon)
        for a, b in zip(polygon, polygon[1:] + polygon[:1], strict=True):
            clipped = _clip_boundary(clipped, a, b)
            if not clipped:
                break
    else:
        for axis, boundary, above in (
            (0, x, True),
            (0, x + w, False),
            (1, y, True),
            (1, y + h, False),
        ):
            if not clipped:
                break
            clipped = _clip(clipped, axis, boundary, above)
    covered = _area(clipped) if clipped else 0.0
    tolerance = 1e-8 * max(1.0, target_area, _area(footprint))
    if covered <= tolerance:
        return ContentDiagnostic(item_id, "fully_clipped", target_area if image else 0.0, True)
    missing = max(0.0, target_area - covered) if image else 0.0
    if missing > tolerance:
        status = "contain_whitespace" if fit_policy == "contain" else "uncovered"
    else:
        status = "partly_clipped" if covered < _area(footprint) - tolerance else "visible"
    return ContentDiagnostic(item_id, status, missing)


def _clip_boundary(points, a, b):
    result = []
    dx, dy = b[0] - a[0], b[1] - a[1]
    for start, end in zip(points[-1:] + points[:-1], points, strict=True):
        first = dx * (start[1] - a[1]) - dy * (start[0] - a[0])
        last = dx * (end[1] - a[1]) - dy * (end[0] - a[0])
        if (first >= 0) != (last >= 0):
            ratio = first / (first - last)
            result.append(tuple(start[i] + ratio * (end[i] - start[i]) for i in (0, 1)))
        if last >= 0:
            result.append(end)
    return tuple(result)
