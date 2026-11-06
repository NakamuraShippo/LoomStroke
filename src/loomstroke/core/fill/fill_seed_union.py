"""Worker-side coverage union for finite, deduplicated symmetry seeds."""

from dataclasses import replace

import numpy as np

from .flood_fill import _bounds_from_spans


def calculate_seed_union(request, calculate, spans_from_mask):
    height, width = request.reference.shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    colors = set()
    for x, y in tuple(dict.fromkeys(request.seeds))[:17]:
        if not 0 <= x < width or not 0 <= y < height:
            continue
        color = tuple(request.reference[y, x])
        if mask[y, x] and color in colors:
            continue
        colors.add(color)
        spans, _bounds = calculate(replace(request, seed=(x, y), seeds=()))
        for row, left, right in spans:
            mask[row, left : right + 1] = 255
    spans = spans_from_mask(mask)
    return spans, _bounds_from_spans(spans)
