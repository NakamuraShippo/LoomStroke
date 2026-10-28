"""Pure export-only information placement beyond all marks and bleed."""

from __future__ import annotations

from dataclasses import replace

from .print_margin_models import PrintMarginContext, PrintMarginGeometry, PrintMarginOptions
from .print_page_geometry import PrintPageGeometry

_COLOR_PATCHES = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0, 0.0),
    (0.0, 1.0, 1.0, 0.0),
    (1.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 0.0),
)
_GRAY_PATCHES = tuple((0.0, 0.0, 0.0, i / 5) for i in range(6))


def _one_line(text: str, limit: int) -> str:
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())[:limit]


def margin_information(options: PrintMarginOptions, context: PrintMarginContext) -> str:
    parts = []
    if options.file_name:
        # A destination basename is expected; also reject path components in
        # injected contexts so machine/user paths never enter printed labels.
        parts.append(_one_line(context.file_name.replace("\\", "/").rsplit("/", 1)[-1], 4096))
    if options.page_number:
        parts.append(f"{context.page_number}/{context.total_pages}")
    if options.date_time:
        parts.append(_one_line(context.date_time, 64))
    if options.color_mode:
        parts.append(_one_line(context.color_mode, 64))
    return " | ".join(part for part in parts if part)


def annotate_print_geometry(
    geometry: PrintPageGeometry, options: PrintMarginOptions, context: PrintMarginContext
) -> PrintPageGeometry:
    if not options.enabled:
        return geometry
    text = margin_information(options, context)
    patches = (
        *(_COLOR_PATCHES if options.color_bar else ()),
        *(_GRAY_PATCHES if options.gray_bar else ()),
    )
    mx, my, old_width, old_height = geometry.media_box_pt
    width = max(old_width, 6 + len(patches) * 6)
    y = old_height + 3
    text_rect = None
    if text:
        text_rect = (3.0, y, max(1.0, width - 6), 12.0)
        y += 15
    swatches = ()
    if patches:
        patch_width = min(12.0, (width - 6) / len(patches))
        swatches = tuple(
            ((3 + i * patch_width, y, patch_width, 6.0), color) for i, color in enumerate(patches)
        )
        y += 9
    return replace(
        geometry,
        media_box_pt=(mx, my, width, y),
        margin_info=PrintMarginGeometry(text, text_rect, swatches),
    )
