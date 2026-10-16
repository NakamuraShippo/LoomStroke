"""Polygon edge paths and a single owner for every corner/seam pixel."""

import math

import numpy as np
from PySide6.QtGui import QImage

from .convex_clip import validate_polygon
from .models import PanelFrameError
from .ownership_labels import ownership_labels


def corner_owners(points, depths):
    points = validate_polygon(points)
    tangents = [
        (b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:] + points[:1], strict=True)
    ]
    scores = [
        (abs(dy) / math.hypot(dx, dy), depth, index)
        for index, ((dx, dy), depth) in enumerate(zip(tangents, depths, strict=True))
    ]
    return tuple(
        min((index - 1) % len(points), index, key=lambda i: scores[i])
        for index in range(len(points))
    )


def edge_paths(points, width, miter_limit):
    points = validate_polygon(points)
    if not math.isfinite(width) or width < 1 or not math.isfinite(miter_limit) or miter_limit < 1:
        raise PanelFrameError("invalid polygon stroke style")
    # Qt quantizes diagonal joins differently when a closed outline is
    # shortened to open paths. Each independently editable ink source retains
    # that outline; its disjoint ownership mask exposes exactly one edge.
    # Erasing one source cannot change another source or the logical region.
    return tuple(points for _ in points)


def span_ownership_mask(
    mask, a, b, span, *, window_ends=(False, False), corner_windows=(), corner_radius=0
):
    if span == (0.0, 1.0) and not any(window_ends):
        return mask
    result = mask.copy()
    width, height = result.width(), result.height()
    rows = np.frombuffer(result.bits(), np.uint8).reshape(height, result.bytesPerLine())
    dx, dy = b[0] - a[0], b[1] - a[1]
    xs = np.arange(width, dtype=np.float64)[None, :] + 0.5
    for top in range(0, height, 64):
        bottom = min(top + 64, height)
        ys = np.arange(top, bottom, dtype=np.float64)[:, None] + 0.5
        t = ((xs - a[0]) * dx + (ys - a[1]) * dy) / (dx * dx + dy * dy)
        inside = np.ones(t.shape, dtype=bool)
        if span[0] > 1e-8 or window_ends[0]:
            inside &= t >= span[0]
        if span[1] < 1 - 1e-8 or window_ends[1]:
            inside &= t < span[1]
        for cx, cy in corner_windows:
            inside |= (xs - cx) ** 2 + (ys - cy) ** 2 <= corner_radius**2
        rows[top:bottom, :width] *= inside
    return result


def edge_ownership_masks(page_size, points, owners, *, corner_radius):
    """Binary canvas-pixel partition. Prepare off the stroke path in bounded tiles.

    A nearest-sampled mask ensures adjacent parts never blend their common
    antialias coverage twice, including mixed vector/raster parts.
    """
    points = validate_polygon(points)
    width, height = page_size
    if min(width, height) <= 0 or len(owners) != len(points):
        raise PanelFrameError("invalid polygon ownership dimensions")
    masks = tuple(QImage(width, height, QImage.Format.Format_Alpha8) for _ in points)
    if any(mask.isNull() for mask in masks):
        raise PanelFrameError("cannot allocate polygon ownership")
    rows = []
    for mask in masks:
        mask.fill(0)
        rows.append(np.frombuffer(mask.bits(), np.uint8).reshape(height, mask.bytesPerLine()))
    for top in range(0, height, 64):
        bottom = min(top + 64, height)
        labels = ownership_labels(width, top, bottom, points, owners, corner_radius)
        for index, row in enumerate(rows):
            row[top:bottom, :width] = (labels == index).astype(np.uint8) * 255
    return masks
