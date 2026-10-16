"""Content allocation by true convex overlap, with explicit ambiguous results."""

import math

from .allocation import ContentAllocation
from .models import PanelFrameError


def rect_points(rect):
    x, y, w, h = rect
    return ((x, y), (x + w, y), (x + w, y + h), (x, y + h))


def polygon_overlap(first, second):
    """Sutherland-Hodgman intersection; inputs use canvas-clockwise winding."""
    points = tuple(first)
    for a, b in zip(second, second[1:] + second[:1], strict=True):
        if not points:
            return 0.0

        def distance(point, start=a, end=b):
            return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
                point[0] - start[0]
            )

        clipped = []
        for start, end in zip(points[-1:] + points[:-1], points, strict=True):
            ds, de = distance(start), distance(end)
            if (ds >= 0) != (de >= 0):
                fraction = ds / (ds - de)
                clipped.append(tuple(start[i] + fraction * (end[i] - start[i]) for i in (0, 1)))
            if de >= 0:
                clipped.append(end)
        points = tuple(clipped)
    return (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(points, points[1:] + points[:1], strict=True)
            )
        )
        / 2
    )


def allocate_polygons(items, polygons, original, *, overrides=None):
    targets = set(polygons)
    overrides = dict(overrides or {})
    ids = {item.item_id for item in items}
    if (
        original not in targets
        or len(ids) != len(items)
        or (not overrides.keys() <= ids or not set(overrides.values()) <= targets)
    ):
        raise PanelFrameError("invalid polygon content allocation")
    result = []
    for item in items:
        if item.item_id in overrides:
            result.append(ContentAllocation(item.item_id, overrides[item.item_id], "manual"))
            continue
        if item.bounds is None:
            result.append(ContentAllocation(item.item_id, original, "unknown_bounds", True))
            continue
        areas = [
            (pid, polygon_overlap(rect_points(item.bounds), points))
            for pid, points in polygons.items()
        ]
        maximum = max(area for _, area in areas)
        winners = [
            pid for pid, area in areas if math.isclose(area, maximum, rel_tol=1e-9, abs_tol=1e-7)
        ]
        if maximum <= 0:
            allocation = ContentAllocation(item.item_id, original, "outside", True)
        elif len(winners) != 1:
            allocation = ContentAllocation(item.item_id, original, "tie", True)
        else:
            allocation = ContentAllocation(item.item_id, winners[0], "overlap")
        result.append(allocation)
    return tuple(result)
