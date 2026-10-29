"""Adapters between existing guide records and Qt-free rotated-line geometry."""

import math

from ...core.composition_snap import nearest_line_hit
from ...core.guide_transform import guide_line, rotate_point


def stored_transform(entry):
    try:
        rotation = float(entry.get("rotation", 0))
        raw = entry.get("pivot")
        pivot = tuple(map(float, raw)) if isinstance(raw, (list, tuple)) and len(raw) == 2 else None
        if not math.isfinite(rotation) or (pivot and not all(math.isfinite(v) for v in pivot)):
            raise ValueError("Non-finite guide transform")
        return rotation % 360, pivot
    except (TypeError, ValueError):
        return 0.0, None


def segment(guide, size):
    return guide_line(guide.orientation, guide.position, tuple(size), guide.rotation, guide.pivot)


def projected_axis(guide, point, size):
    pivot = guide.pivot or (size[0] / 2, size[1] / 2)
    x, y = rotate_point((point.x(), point.y()), pivot, -guide.rotation)
    return x if guide.orientation == "vertical" else y


def snap_rotated(controller, x, y, w=0, h=0):
    if not controller.guides_visible:
        return x, y, None
    width, height, _dpi = controller._active_canvas_spec()
    entries = [(g, segment(g, (width, height))) for g in controller.guides() if g.rotation % 360]
    entries = [(g, line) for g, line in entries if line is not None]
    lines = tuple(line for _, line in entries)
    threshold = controller._screen_to_canvas_distance(controller._snap_threshold_px)
    best = None
    for px, py in dict.fromkeys(((x, y), (x + w, y), (x, y + h), (x + w, y + h))):
        hit = nearest_line_hit((px, py), lines, threshold)
        if hit is None:
            continue
        point, index = hit
        dx, dy = point[0] - px, point[1] - py
        distance = dx * dx + dy * dy
        if best is None or distance < best[0]:
            best = distance, x + dx, y + dy, entries[index][0].id
    return (x, y, None) if best is None else best[1:]
