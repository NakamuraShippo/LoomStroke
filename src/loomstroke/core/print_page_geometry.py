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

from dataclasses import dataclass

from .comic_document_profile import PhysicalPageLayout, canvas_pixel_size

__all__ = [
    "CropMark",
    "PrintPageGeometry",
    "geometry_for_canvas",
    "geometry_for_layout",
    "px_to_pt",
]

#: Margin around the bleed box reserved for crop marks, in points (~6 mm).
MARK_MARGIN_PT = 17.0
#: Crop mark line length in points (~5 mm).
MARK_LENGTH_PT = 14.0
#: Gap between the trim corner and the start of a mark line (~1 mm).
MARK_GAP_PT = 3.0


def px_to_pt(pixels: float, dpi: float) -> float:
    return pixels / dpi * 72.0


def mm_to_pt(mm: float) -> float:
    return mm / 25.4 * 72.0


@dataclass(frozen=True, slots=True)
class CropMark:
    """One mark line in PDF page points (origin: top-left of the media)."""

    x1: float
    y1: float
    x2: float
    y2: float


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


def geometry_for_layout(layout: PhysicalPageLayout, *, with_marks: bool) -> PrintPageGeometry:
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
    )


def geometry_for_canvas(
    width_px: int, height_px: int, dpi: tuple[float, float], *, with_marks: bool
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
) -> PrintPageGeometry:
    margin = MARK_MARGIN_PT if with_marks else 0.0
    content_rect = (margin, margin, content_w, content_h)
    bleed_box = content_rect  # canvas covers trim + bleed by construction
    trim_box = (
        margin + trim_offset[0],
        margin + trim_offset[1],
        trim_size[0],
        trim_size[1],
    )
    media_box = (0.0, 0.0, content_w + margin * 2, content_h + margin * 2)
    del bleed_insets  # marks anchor on the real (pixel-rounded) bleed edge
    marks = _corner_marks(trim_box, content_rect) if with_marks else ()
    return PrintPageGeometry(
        media_box_pt=media_box,
        bleed_box_pt=bleed_box,
        trim_box_pt=trim_box,
        content_rect_pt=content_rect,
        has_bleed=has_bleed,
        marks=marks,
    )


def _corner_marks(
    trim_box: tuple[float, float, float, float],
    content_rect: tuple[float, float, float, float],
) -> tuple[CropMark, ...]:
    """Japanese-style corner marks: for each trim corner, one horizontal
    and one vertical line.  Lines are anchored on the *actual* bleed edge
    (= content rect, pixel-rounded) plus a small gap, so they can never
    enter the bleed area regardless of mm/px rounding (spec 11 §9)."""
    x, y, w, h = trim_box
    cx0, cy0, cw, ch = content_rect
    edges = {
        "left": cx0,
        "right": cx0 + cw,
        "top": cy0,
        "bottom": cy0 + ch,
    }
    corners = (
        (x, y, -1.0, -1.0),
        (x + w, y, 1.0, -1.0),
        (x, y + h, -1.0, 1.0),
        (x + w, y + h, 1.0, 1.0),
    )
    marks: list[CropMark] = []
    for cx, cy, dx, dy in corners:
        h_start = (edges["right"] if dx > 0 else edges["left"]) + dx * MARK_GAP_PT
        marks.append(CropMark(x1=h_start, y1=cy, x2=h_start + dx * MARK_LENGTH_PT, y2=cy))
        v_start = (edges["bottom"] if dy > 0 else edges["top"]) + dy * MARK_GAP_PT
        marks.append(CropMark(x1=cx, y1=v_start, x2=cx, y2=v_start + dy * MARK_LENGTH_PT))
    return tuple(marks)
