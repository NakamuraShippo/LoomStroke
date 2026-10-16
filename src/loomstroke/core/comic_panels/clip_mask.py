"""Derived coverage masks using the existing layer mask representation.

This function consumes detached geometry, so callers can run preparation on a
worker. Only the returned image is transferred to the document's Command.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

import numpy as np
from PySide6.QtGui import QImage

from .models import PanelFrameError, Rect, validate_rect


def union_clip_mask(page_size: tuple[int, int], regions: Sequence[Rect]) -> QImage:
    """Area coverage of a rectangle union, including fractional/off-page edges.

    Overlaps are integrated once. Adjacent fractional rectangles have no seam;
    gaps between shared display targets remain transparent.
    """
    width, height = page_size
    if type(width) is not int or type(height) is not int or min(width, height) <= 0:
        raise PanelFrameError("invalid clip mask dimensions")
    clipped = []
    for rect in regions:
        validate_rect(rect)
        x, y, w, h = rect
        bounds = max(0.0, x), max(0.0, y), min(width, x + w), min(height, y + h)
        if bounds[0] < bounds[2] and bounds[1] < bounds[3]:
            clipped.append(bounds)
    image = QImage(width, height, QImage.Format.Format_Alpha8)
    if image.isNull():
        raise PanelFrameError("cannot allocate clip mask")
    image.fill(0)
    pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(height, image.bytesPerLine())
    if not clipped:
        return image
    cuts = sorted({v for r in clipped for v in (r[1], r[3])})
    # Boundary rows may be crossed by several disjoint slabs; accumulate them
    # in float precision before quantizing once. Full rows are written directly.
    boundary_rows: dict[int, np.ndarray] = {}
    for top, bottom in pairwise(cuts):
        middle = (top + bottom) / 2
        spans = sorted((r[0], r[2]) for r in clipped if r[1] < middle < r[3])
        row = _row_coverage(width, spans)
        first_full, last_full = math.ceil(top), math.floor(bottom)
        if last_full > first_full:
            pixels[first_full:last_full, :width] = np.rint(row * 255).astype(np.uint8)
        for y in {math.floor(top), math.ceil(bottom) - 1}:
            if first_full <= y < last_full:
                continue
            coverage = max(0.0, min(y + 1, bottom) - max(y, top))
            if coverage:
                boundary_rows.setdefault(y, np.zeros(width, dtype=np.float64))[:] += row * coverage
    for y, row in boundary_rows.items():
        pixels[y, :width] = np.rint(np.clip(row, 0, 1) * 255).astype(np.uint8)
    return image


def _row_coverage(width: int, spans: Sequence[tuple[float, float]]) -> np.ndarray:
    merged: list[tuple[float, float]] = []
    for left, right in spans:
        if merged and left <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(right, merged[-1][1])
        else:
            merged.append((left, right))
    row = np.zeros(width, dtype=np.float64)
    for left, right in merged:
        first, last = math.floor(left), math.ceil(right)
        columns = np.arange(first, last)
        row[first:last] += np.minimum(columns + 1, right) - np.maximum(columns, left)
    return row
