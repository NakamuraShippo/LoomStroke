"""Usable full-height line bands inside rectangular or arbitrary text frames."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QTransform

from ..canvas_models import _MaskShapeContour
from ..vector_text_layout import TextLayoutCalculator as Layout


def frame_segments(
    frame: QRectF,
    path: QPainterPath | None,
    *,
    vertical: bool,
    cross: float,
    em: float,
    before: float,
    after: float,
) -> list[tuple[float, float]]:
    low, high = (frame.top(), frame.bottom()) if vertical else (frame.left(), frame.right())
    if path is None:
        return [(low, high)]
    if vertical:
        path = (
            QTransform(0, 1, 1, 0, 0, 0).map(path)
            if isinstance(path, QPainterPath)
            else _TransposedShape(path)
        )
        lower, upper = cross - after, cross + em + before
    else:
        lower, upper = cross - before, cross + em + after
    segments = [(low, high)]
    # Probe the entire glyph/ruby band rather than fitting only its baseline.
    for coordinate in (lower + 0.01, (lower + upper) * 0.5, upper - 0.01):
        row = Layout.shape_segments_at_y(path, low, high, coordinate)
        segments = _intersect(segments, row)
        if not segments:
            break
    return [(left + 0.5, right - 0.5) for left, right in segments if right - left > 1.0]


def _intersect(
    left: list[tuple[float, float]], right: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    result, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        low, high = max(left[i][0], right[j][0]), min(left[i][1], right[j][1])
        if high > low:
            result.append((low, high))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return result


def resolve_shape(payload: Mapping[str, Any], frame: QRectF) -> Any:
    mode = Layout.text_wrap_mode_from_payload(
        payload, text_mode=Layout.text_mode_from_payload(payload)
    )
    if mode != "shape":
        return None
    top, right, bottom, left = Layout.text_padding_from_payload(payload)
    return Layout.text_shape_path_from_payload(
        payload,
        x=frame.left() - left,
        y=frame.top() - top,
        w=frame.width() + left + right,
        h=frame.height() + top + bottom,
        contour_factory=_MaskShapeContour,
    )


def shape_contains_rect(shape: Any, rect: QRectF) -> bool:
    if isinstance(shape, QPainterPath):
        return shape.contains(rect)
    query = getattr(shape, "contains_rect", None)
    if callable(query):
        return query(rect)
    # Mask contours expose point containment only. Include every covered mask
    # pixel, so holes cannot cut through the ink's bounding rectangle.
    return all(
        shape.contains(QPointF(x + 0.5, y + 0.5))
        for y in range(math.floor(rect.top()), math.ceil(rect.bottom()))
        for x in range(math.floor(rect.left()), math.ceil(rect.right()))
    )


class _TransposedShape:
    def __init__(self, shape: Any) -> None:
        self.shape = shape

    def contains(self, point: QPointF) -> bool:
        return bool(self.shape.contains(QPointF(point.y(), point.x())))

    def segments_at_y(self, x0, x1, y):
        query = getattr(self.shape, "segments_at_x", None)
        if callable(query):
            return query(x0, x1, y)

        # Keep arbitrary point-containment providers working without recursion.
        class PointShape:
            contains = self.contains

        return Layout.shape_segments_at_y(PointShape(), x0, x1, y)
