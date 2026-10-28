"""Bounded ordinary-RGB erase coverage, leaving native/placed primitives intact."""

from __future__ import annotations

import math
from collections.abc import Iterator
from itertools import pairwise
from typing import Any

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

_TILE_SIZE = 128
_SEGMENT_BATCH_SIZE = 4


def supports_rgb_erase_coverage(owner: Any, *, masked: bool) -> bool:
    # Import the definition-time identities, not the possibly overridden class
    # attributes seen when this helper happens to be imported for the first time.
    from .canvas_service_simple_stroke_api import (  # noqa: PLC0415
        _DEFAULT_RGB_ERASE_HOOKS,
    )

    names = (
        "_apply_erase_into_layer",
        "_draw_erase_stroke",
        "_paint_erased_masked_stroke" if masked else "_paint_erased_clear_stroke",
    )
    return all(
        getattr(getattr(owner, name, None), "__func__", None) is _DEFAULT_RGB_ERASE_HOOKS[name]
        for name in names
    )


def is_standard_zero_rgb_erase(owner: Any, brush: Any) -> bool:
    # Before source expansion, require either selection branch to be known.
    # Unknown private hooks keep the established call shape and behavior.
    return (
        float(getattr(brush, "opacity", 1.0)) <= 0
        and supports_rgb_erase_coverage(owner, masked=False)
        and supports_rgb_erase_coverage(owner, masked=True)
    )


def paint_rgb_erase_coverage(
    owner: Any,
    *,
    layer: Any,
    points: list[Any],
    brush: Any,
    bounds: QRect,
    local_bounds: QRect,
    offset_x: int,
    offset_y: int,
    selection_mask: QImage | None,
) -> None:
    strength = max(0.0, min(1.0, float(getattr(brush, "opacity", 1.0))))
    if strength <= 0 or not points:
        return
    size = max(1, int(getattr(brush, "size", 10)))
    hardness = max(0.0, min(1.0, float(getattr(brush, "hardness", 1.0))))
    anti_alias = bool(getattr(brush, "anti_alias", True))
    if hardness >= 1 and strength >= 1 and selection_mask is None:
        # Preserve the exact Qt Clear rounding of the established hard erase,
        # including semi-transparent premultiplied images and backing offsets.
        painter = QPainter(layer.image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, anti_alias)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.translate(-offset_x, -offset_y)
        try:
            owner._draw_erase_stroke(painter, points, size, color=QColor(0, 0, 0, 0))
        finally:
            painter.end()
        return

    if hardness >= 1:
        coverage = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
        coverage.fill(Qt.GlobalColor.transparent)
        painter = QPainter(coverage)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, anti_alias)
        try:
            owner._draw_erase_stroke(painter, points, size, color=QColor("white"), offset=bounds)
        finally:
            painter.end()
    else:
        coverage = _soft_round_coverage(points, size, hardness, anti_alias, bounds)
    if selection_mask is not None:
        owner._apply_selection_mask_to_image(coverage, selection_mask.copy(bounds))
    painter = QPainter(layer.image)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    painter.setOpacity(strength)
    try:
        # Geometry is combined before applying strength, so retracing segments
        # inside this Command cannot multiply the requested erase strength.
        painter.drawImage(local_bounds.topLeft(), coverage)
    finally:
        painter.end()


def _soft_round_coverage(
    points: list[Any], size: int, hardness: float, anti_alias: bool, bounds: QRect
) -> QImage:
    values = np.zeros((bounds.height(), bounds.width()), dtype=np.uint8)
    groups: dict[float, list[tuple[tuple[int, int], tuple[int, int]]]] = {}
    for start, end, radius in _soft_segments(points, size):
        groups.setdefault(radius, []).append((start, end))
    for radius, segments in groups.items():
        if len(segments) > 2:
            # Nearby input segments share small tile bounds; a bounded batch
            # avoids checking the entire path against every distant tile.
            for index in range(0, len(segments), _SEGMENT_BATCH_SIZE):
                _add_soft_group(
                    values,
                    bounds,
                    segments[index : index + _SEGMENT_BATCH_SIZE],
                    radius,
                    hardness,
                    anti_alias,
                )
        else:
            for start, end in segments:
                _add_soft_segment(values, bounds, start, end, radius, hardness, anti_alias)
    mask = QImage(
        values.data,
        bounds.width(),
        bounds.height(),
        values.strides[0],
        QImage.Format.Format_Alpha8,
    ).copy()
    return mask.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _soft_segments(
    points: list[Any], size: int
) -> Iterator[tuple[tuple[int, int], tuple[int, int], float]]:
    pending = None
    pending_pressure = None
    pairs = pairwise(points) if len(points) > 1 else ((points[0], points[0]),)
    for first, second in pairs:
        first_pressure = getattr(first, "pressure", 1.0) or 1.0
        second_pressure = getattr(second, "pressure", 1.0) or 1.0
        pressure = (
            first_pressure if len(points) == 1 else max(0.1, (first_pressure + second_pressure) / 2)
        )
        radius = max(1, int(size * pressure)) / 2.0
        start = (int(first.x), int(first.y))
        end = (int(second.x), int(second.y))
        uniform_pressure = first_pressure if first_pressure == second_pressure else None
        if pending is not None and uniform_pressure is not None:
            previous_start, previous_end, previous_radius = pending
            ax, ay = previous_end[0] - previous_start[0], previous_end[1] - previous_start[1]
            bx, by = end[0] - start[0], end[1] - start[1]
            if (
                previous_end == start
                and pending_pressure == uniform_pressure
                and previous_radius == radius
                and ax * by == ay * bx
                and ax * bx + ay * by > 0
            ):
                # Exact integer collinearity and the same direction/radius
                # preserve the capsule union. Never approximate bends,
                # reversals, or a changing pressure sequence.
                pending = (previous_start, end, radius)
                continue
        if pending is not None:
            yield pending
        pending = (start, end, radius)
        pending_pressure = uniform_pressure
    if pending is not None:
        yield pending


def _add_soft_segment(values, bounds, start, end, radius, hardness, anti_alias) -> None:
    outer = radius + (0.5 if anti_alias else 0.0)
    region = _segment_bounds(bounds, start, end, outer)
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    for ty in range(region.top(), region.bottom() + 1, _TILE_SIZE):
        for tx in range(region.left(), region.right() + 1, _TILE_SIZE):
            tile = region.intersected(QRect(tx, ty, _TILE_SIZE, _TILE_SIZE))
            # Reject far-away tiles of long diagonal segments before allocating
            # distance arrays. Each distance calculation is at most 128x128.
            cx, cy = tile.x() + tile.width() / 2, tile.y() + tile.height() / 2
            fraction = (
                max(0.0, min(1.0, ((cx - start[0]) * dx + (cy - start[1]) * dy) / length_squared))
                if length_squared
                else 0.0
            )
            distance = math.hypot(cx - start[0] - fraction * dx, cy - start[1] - fraction * dy)
            if distance > outer + math.hypot(tile.width(), tile.height()) / 2:
                continue
            xs = np.arange(tile.x(), tile.x() + tile.width(), dtype=np.float32)[None, :] + 0.5
            ys = np.arange(tile.y(), tile.y() + tile.height(), dtype=np.float32)[:, None] + 0.5
            squared = _squared_segment_distance(xs, ys, start, end)
            _merge_soft_tile(values, bounds, tile, squared, outer, radius - radius * hardness)


def _segment_bounds(bounds, start, end, outer) -> QRect:
    left, top = math.floor(min(start[0], end[0]) - outer), math.floor(min(start[1], end[1]) - outer)
    right, bottom = (
        math.ceil(max(start[0], end[0]) + outer),
        math.ceil(max(start[1], end[1]) + outer),
    )
    return bounds.intersected(QRect(left, top, right - left + 1, bottom - top + 1))


def _squared_segment_distance(xs, ys, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    relative_x, relative_y = xs - start[0], ys - start[1]
    if not length_squared:
        return relative_x * relative_x + relative_y * relative_y
    return _projected_squared_distance(relative_x, relative_y, dx, dy, length_squared)


def _projected_squared_distance(relative_x, relative_y, dx, dy, length_squared):
    along = relative_x * dx + relative_y * dy
    along /= length_squared
    along.clip(0, 1, out=along)
    distance_y = relative_y - along * dy
    along *= dx
    np.subtract(relative_x, along, out=along)
    np.square(along, out=along)
    np.square(distance_y, out=distance_y)
    along += distance_y
    return along


def _merge_soft_tile(values, bounds, tile, squared, outer, shoulder) -> None:
    # The radial falloff is monotone, so a same-radius group may select the
    # nearest squared distance before this conversion without changing pixels.
    np.sqrt(squared, out=squared)
    np.subtract(outer, squared, out=squared)
    squared /= max(0.5, shoulder)
    np.maximum(squared, 0, out=squared)
    np.minimum(squared, 1, out=squared)
    squared *= 255
    np.rint(squared, out=squared)
    x, y = tile.x() - bounds.x(), tile.y() - bounds.y()
    view = values[y : y + tile.height(), x : x + tile.width()]
    np.maximum(view, squared, out=view, casting="unsafe")


def _add_soft_group(values, bounds, segments, radius, hardness, anti_alias) -> None:
    outer = radius + (0.5 if anti_alias else 0.0)
    regions = [(start, end, _segment_bounds(bounds, start, end, outer)) for start, end in segments]
    region = QRect()
    for _start, _end, segment_region in regions:
        region = region.united(segment_region)
    starts = np.asarray([start for start, _end in segments], dtype=np.float32)
    deltas = np.asarray(
        [(end[0] - start[0], end[1] - start[1]) for start, end in segments], dtype=np.float32
    )
    lengths = np.asarray(
        [max(1, (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) for start, end in segments],
        dtype=np.float32,
    )
    for ty in range(region.top(), region.bottom() + 1, _TILE_SIZE):
        for tx in range(region.left(), region.right() + 1, _TILE_SIZE):
            tile = region.intersected(QRect(tx, ty, _TILE_SIZE, _TILE_SIZE))
            selected = [
                index for index, (_a, _b, rect) in enumerate(regions) if rect.intersects(tile)
            ]
            if not selected:
                continue
            xs = np.arange(tile.x(), tile.x() + tile.width(), dtype=np.float32)[None, None, :] + 0.5
            ys = (
                np.arange(tile.y(), tile.y() + tile.height(), dtype=np.float32)[None, :, None] + 0.5
            )
            squared = _projected_squared_distance(
                xs - starts[selected, 0, None, None],
                ys - starts[selected, 1, None, None],
                deltas[selected, 0, None, None],
                deltas[selected, 1, None, None],
                lengths[selected, None, None],
            )
            nearest = squared.min(axis=0)
            _merge_soft_tile(values, bounds, tile, nearest, outer, radius - radius * hardness)
