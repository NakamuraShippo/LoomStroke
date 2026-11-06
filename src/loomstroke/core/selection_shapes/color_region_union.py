"""Detached color-region union matching the magic-wand RGBA tolerance rules."""

import numpy as np

from loomstroke.core.fill.flood_fill import _scanline_fill


def color_region_union(reference, coverage, seeds, tolerance, contiguous):
    height, width = reference.shape[:2]
    output = np.zeros((height, width), dtype=np.uint8)
    signed = reference.astype(np.int16)
    colors = set()
    for x, y in tuple(dict.fromkeys(seeds))[:17]:
        if not 0 <= x < width or not 0 <= y < height or not coverage[y, x]:
            continue
        color = tuple(reference[y, x])
        if color in colors and (not contiguous or output[y, x]):
            continue
        colors.add(color)
        matching = (np.abs(signed - signed[y, x]).max(axis=2) <= tolerance) & (coverage > 0)
        if contiguous:
            for sy, x0, x1 in _scanline_fill(matching, x, y):
                output[sy, x0 : x1 + 1] = coverage[sy, x0 : x1 + 1]
        else:
            np.maximum(output, np.where(matching, coverage, np.uint8(0)), out=output)
    return output
