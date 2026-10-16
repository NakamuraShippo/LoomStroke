"""Resolve raw raster ink footprints in detached worker preparation only."""

import math
from dataclasses import replace

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.comic_panels.models import ContentBounds
from loomstroke.core.command_types import LayerTransform


def _alpha_rect(image):
    if image is None or image.isNull():
        return None
    coverage = image.convertToFormat(QImage.Format.Format_Alpha8)
    pixels = np.frombuffer(coverage.constBits(), np.uint8).reshape(
        coverage.height(), coverage.bytesPerLine()
    )[:, : coverage.width()]
    left, top, right, bottom = coverage.width(), coverage.height(), -1, -1
    for start in range(0, coverage.height(), 128):
        band = pixels[start : start + 128]
        columns = np.flatnonzero(np.any(band, axis=0))
        if not columns.size:
            continue
        rows = np.flatnonzero(np.any(band[:, columns[0] : columns[-1] + 1], axis=1))
        left, right = min(left, int(columns[0])), max(right, int(columns[-1]))
        top, bottom = min(top, start + int(rows[0])), max(bottom, start + int(rows[-1]))
    return (left, top, right - left + 1, bottom - top + 1) if right >= left else None


def _part_bounds(state, layer_id, seen=frozenset()):  # noqa: PLR0911 - unknown bounds
    if layer_id in seen:
        return None
    part = state.panel_parts.get(layer_id)
    if part is None:
        return None
    values = part.values
    if values["effect_type"]:
        return None
    if values["layer_type"] == "group":
        if (
            values["transform"] != LayerTransform.identity()
            or values["offset_x"]
            or values["offset_y"]
        ):
            return None
        rects = [_part_bounds(state, child, seen | {layer_id}) for child in part.children]
        if not rects or any(rect is None for rect in rects):
            return None
        points = [(r[0], r[1]) for r in rects] + [(r[0] + r[2], r[1] + r[3]) for r in rects]
    elif values["layer_type"] == "raster":
        rect = _alpha_rect(values["image"])
        if rect is None:
            return None
        x, y, w, h = rect
        transform = values["transform"]
        if transform == LayerTransform.identity():
            transform = transform.with_translation(values["offset_x"], values["offset_y"])
        affine = transform.to_affine()
        points = [
            affine.apply(*point) for point in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        ]
    else:
        points = [
            a.pos for obj in values["vector_objects"] for path in obj.paths for a in path.anchors
        ]
    if not points or not all(math.isfinite(value) for point in points for value in point):
        return None
    xs, ys = zip(*points, strict=True)
    bounds = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    return bounds if min(bounds[2:]) > 0 else None


def state_with_raster_bounds(state):
    return replace(
        state,
        panel_contents={
            group_id: tuple(
                (
                    unit
                    if unit.bounds.bounds is not None
                    else replace(
                        unit,
                        bounds=ContentBounds(unit.layer_id, _part_bounds(state, unit.layer_id)),
                    )
                )
                for unit in units
            )
            for group_id, units in state.panel_contents.items()
        },
    )
