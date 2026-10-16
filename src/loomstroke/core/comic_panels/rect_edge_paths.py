"""Rectangle stroke partition with horizontal corner ownership.

Horizontal parts include short vertical corner segments joined to the top or
bottom stroke. Their butt ends and the vertical parts meet on pixel boundaries,
so separate vector/raster layers never double-composite fractional seam alpha.
The original frame centreline, width and miter corners stay unchanged.
"""

import math

from .models import PanelFrameError, Point, Rect, Side, validate_rect


def rect_edge_paths(rect: Rect, stroke_width: float) -> tuple[tuple[Side, tuple[Point, ...]], ...]:
    validate_rect(rect)
    if not math.isfinite(stroke_width) or stroke_width < 1 or stroke_width >= min(rect[2:]):
        raise PanelFrameError("invalid rectangular frame stroke width")
    x, y, w, h = rect
    half = stroke_width / 2
    top_seam, bottom_seam = math.ceil(y + half), math.floor(y + h - half)
    if bottom_seam <= top_seam:
        raise PanelFrameError("frame has no room for pixel-aligned corner seams")
    # Keep a collinear midpoint: Qt's two-anchor fast stroker quantizes
    # fractional widths differently from the original closed frame stroker.
    # The normal path stroker preserves the original coverage byte for byte.
    return (
        ("top", ((x, top_seam), (x, y), (x + w, y), (x + w, top_seam))),
        ("right", ((x + w, top_seam), (x + w, (top_seam + bottom_seam) / 2), (x + w, bottom_seam))),
        ("bottom", ((x, bottom_seam), (x, y + h), (x + w, y + h), (x + w, bottom_seam))),
        ("left", ((x, bottom_seam), (x, (top_seam + bottom_seam) / 2), (x, top_seam))),
    )
