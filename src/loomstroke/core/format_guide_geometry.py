"""Physical page guide reconstruction and replacement, without canvas resizing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .binding_margin_geometry import binding_margin_position
from .comic_document_profile import (
    PhysicalPageLayout,
    canvas_pixel_size,
    guides_as_view_state,
    layout_guides,
)
from .guide_layout_geometry import guide_role_edges, unit_to_pixels

_FORMAT_PREFIXES = ("tpl-trim-", "tpl-bleed-", "tpl-safe-", "tpl-gutter-")


def layout_from_guides(
    guides: Sequence[Mapping[str, Any]],
    size: tuple[int, int],
    dpi: tuple[float, float],
) -> PhysicalPageLayout:
    mm_x, mm_y = 1 / unit_to_pixels(1, "mm", dpi[0]), 1 / unit_to_pixels(1, "mm", dpi[1])
    try:
        left, right = guide_role_edges(guides, "trim", "vertical")
        top, bottom = guide_role_edges(guides, "trim", "horizontal")
        safe_left, safe_right = guide_role_edges(guides, "safe", "vertical")
        safe_top, safe_bottom = guide_role_edges(guides, "safe", "horizontal")
        return PhysicalPageLayout(
            trim_mm=((right - left) * mm_x, (bottom - top) * mm_y),
            bleed_mm=(left * mm_x, top * mm_y, (size[0] - right) * mm_x, (size[1] - bottom) * mm_y),
            safe_mm=(
                (safe_left - left) * mm_x,
                (safe_top - top) * mm_y,
                (right - safe_right) * mm_x,
                (bottom - safe_bottom) * mm_y,
            ),
            dpi=dpi,
        )
    except ValueError:
        return PhysicalPageLayout(
            trim_mm=(size[0] * mm_x, size[1] * mm_y),
            bleed_mm=(0, 0, 0, 0),
            safe_mm=(0, 0, 0, 0),
            dpi=dpi,
        )


def regenerate_format_guides(
    layout: PhysicalPageLayout,
    existing: Sequence[Mapping[str, Any]],
    *,
    size: tuple[int, int],
    gutter_mm: float = 0.0,
    physical_side: str = "right",
) -> list[dict[str, Any]]:
    if canvas_pixel_size(layout) != size:
        raise ValueError("The page format must match the existing canvas size")
    left, top, right, bottom = layout.safe_mm
    if left + right >= layout.trim_mm[0] or top + bottom >= layout.trim_mm[1]:
        raise ValueError("Safe margins leave no content")
    guides = guides_as_view_state(layout_guides(layout))
    gutter = binding_margin_position(
        guides,
        width_mm=gutter_mm,
        dpi_x=layout.dpi[0],
        physical_side=physical_side,
        canvas_width=size[0],
    )
    if gutter is not None:
        guides.append({"id": "tpl-gutter-v-1", "orientation": "vertical", "position": gutter})
    manual = [
        dict(guide)
        for guide in existing
        if not str(guide.get("id", "")).startswith(_FORMAT_PREFIXES)
    ]
    return [*manual, *guides]
