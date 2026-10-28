"""Print page geometry (CT-11 P4, spec 11 §9).

Pure math from pixels/mm to PDF points: where the trim, bleed, and media
areas sit on a PDF page, and where the crop marks go.  ``points =
pixels / dpi * 72`` is the single conversion rule.

Two inputs are possible per page:

- A CT-07 :class:`PhysicalPageLayout` — trim/bleed are authoritative and
  the marks sit in a margin outside the bleed.
- No layout — the whole canvas is the trim area, there is no bleed, and
  the caller surfaces a "no bleed" warning (spec 11 §9); marks can still
  be drawn in the margin.

Crop marks are drawn in PDF page coordinates and never enter the bleed
area (spec 11 §9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .comic_document_profile import PhysicalPageLayout, canvas_pixel_size
from .print_margin_models import PrintMarginGeometry
from .print_mark_geometry import jp_corner_marks, print_marks
from .print_mark_models import (
    MARK_GAP_PT,
    MARK_LENGTH_PT,
    MARK_MARGIN_PT,
    CropMark,
    PrintMarkOptions,
)

__all__ = [
    "MARK_GAP_PT",
    "MARK_LENGTH_PT",
    "MARK_MARGIN_PT",
    "CropMark",
    "PrintMarkOptions",
    "PrintPageGeometry",
    "geometry_for_canvas",
    "geometry_for_layout",
    "px_to_pt",
]


def px_to_pt(pixels: float, dpi: float) -> float:
    return pixels / dpi * 72.0


def mm_to_pt(mm: float) -> float:
    return mm / 25.4 * 72.0


@dataclass(frozen=True, slots=True)
class PrintPageGeometry:
    """Boxes in PDF points, top-left origin.  ``(x, y, w, h)`` each."""

    media_box_pt: tuple[float, float, float, float]
    bleed_box_pt: tuple[float, float, float, float]
    trim_box_pt: tuple[float, float, float, float]
    #: Where the page raster (canvas = trim + bleed) lands on the media.
    content_rect_pt: tuple[float, float, float, float]
    has_bleed: bool
    marks: tuple[CropMark, ...] = ()
    circle_marks: tuple[tuple[float, float, float], ...] = ()
    mark_options: PrintMarkOptions = field(default_factory=PrintMarkOptions)
    margin_info: PrintMarginGeometry | None = None


def geometry_for_layout(
    layout: PhysicalPageLayout, *, with_marks: bool, mark_options: PrintMarkOptions | None = None
) -> PrintPageGeometry:
    """Geometry from an authoritative physical layout (CT-07)."""
    width_px, height_px = canvas_pixel_size(layout)
    dpi_x, dpi_y = layout.dpi
    content_w = px_to_pt(width_px, dpi_x)
    content_h = px_to_pt(height_px, dpi_y)
    bleed_left = mm_to_pt(layout.bleed_mm[0])
    bleed_top = mm_to_pt(layout.bleed_mm[1])
    bleed_right = mm_to_pt(layout.bleed_mm[2])
    bleed_bottom = mm_to_pt(layout.bleed_mm[3])
    trim_w = mm_to_pt(layout.trim_mm[0])
    trim_h = mm_to_pt(layout.trim_mm[1])
    has_bleed = max(layout.bleed_mm) > 0
    return _assemble(
        content_w,
        content_h,
        trim_offset=(bleed_left, bleed_top),
        trim_size=(trim_w, trim_h),
        bleed_insets=(bleed_left, bleed_top, bleed_right, bleed_bottom),
        has_bleed=has_bleed,
        with_marks=with_marks,
        mark_options=mark_options,
    )


def geometry_for_canvas(
    width_px: int,
    height_px: int,
    dpi: tuple[float, float],
    *,
    with_marks: bool,
    mark_options: PrintMarkOptions | None = None,
) -> PrintPageGeometry:
    """Fallback: no physical layout — whole canvas is trim, no bleed."""
    content_w = px_to_pt(width_px, dpi[0])
    content_h = px_to_pt(height_px, dpi[1])
    return _assemble(
        content_w,
        content_h,
        trim_offset=(0.0, 0.0),
        trim_size=(content_w, content_h),
        bleed_insets=(0.0, 0.0, 0.0, 0.0),
        has_bleed=False,
        with_marks=with_marks,
        mark_options=mark_options,
    )


def _assemble(
    content_w: float,
    content_h: float,
    *,
    trim_offset: tuple[float, float],
    trim_size: tuple[float, float],
    bleed_insets: tuple[float, float, float, float],
    has_bleed: bool,
    with_marks: bool,
    mark_options: PrintMarkOptions | None = None,
) -> PrintPageGeometry:
    options = mark_options or PrintMarkOptions()
    margin = options.margin if with_marks else 0.0
    content_rect = (margin, margin, content_w, content_h)
    bleed_box = content_rect  # canvas covers trim + bleed by construction
    trim_box = (
        margin + trim_offset[0],
        margin + trim_offset[1],
        trim_size[0],
        trim_size[1],
    )
    media_box = (0.0, 0.0, content_w + margin * 2, content_h + margin * 2)
    if with_marks and options != PrintMarkOptions():
        # QPdfWriter rounds MediaBox dimensions to integer points. Leave
        # the legacy default untouched, but retain every new stroke edge.
        media_box = (0.0, 0.0, math.ceil(media_box[2]), math.ceil(media_box[3]))
    del bleed_insets  # marks anchor on the real (pixel-rounded) bleed edge
    marks, circles = print_marks(trim_box, content_rect, options) if with_marks else ((), ())
    return PrintPageGeometry(
        media_box_pt=media_box,
        bleed_box_pt=bleed_box,
        trim_box_pt=trim_box,
        content_rect_pt=content_rect,
        has_bleed=has_bleed,
        marks=marks,
        circle_marks=circles,
        mark_options=options,
    )


def _corner_marks(
    trim_box: tuple[float, float, float, float],
    content_rect: tuple[float, float, float, float],
) -> tuple[CropMark, ...]:
    """Japanese-style corner marks: for each trim corner, one horizontal
    and one vertical line.  Lines are anchored on the *actual* bleed edge
    (= content rect, pixel-rounded) plus a small gap, so they can never
    enter the bleed area regardless of mm/px rounding (spec 11 §9)."""
    return jp_corner_marks(trim_box, content_rect)
