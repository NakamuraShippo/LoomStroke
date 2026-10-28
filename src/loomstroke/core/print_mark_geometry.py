"""Pure style-specific print marks outside the actual, pixel-rounded bleed box."""

from __future__ import annotations

from .print_mark_models import MARK_GAP_PT, MARK_LENGTH_PT, CropMark, PrintMarkOptions

Box = tuple[float, float, float, float]


def jp_corner_marks(trim: Box, bleed: Box, gap: float = MARK_GAP_PT) -> tuple[CropMark, ...]:
    x, y, w, h = trim
    bx, by, bw, bh = bleed
    marks = []
    for cx, cy, dx, dy in (
        (x, y, -1, -1),
        (x + w, y, 1, -1),
        (x, y + h, -1, 1),
        (x + w, y + h, 1, 1),
    ):
        start_x = (bx + bw if dx > 0 else bx) + dx * gap
        start_y = (by + bh if dy > 0 else by) + dy * gap
        marks.append(CropMark(start_x, cy, start_x + dx * MARK_LENGTH_PT, cy))
        marks.append(CropMark(cx, start_y, cx, start_y + dy * MARK_LENGTH_PT))
    return tuple(marks)


def jp_double_marks(trim: Box, bleed: Box, gap: float) -> tuple[CropMark, ...]:
    return tuple(
        dict.fromkeys((*jp_corner_marks(trim, bleed, gap), *jp_corner_marks(bleed, bleed, gap)))
    )


def western_marks(trim: Box, bleed: Box, gap: float) -> tuple[CropMark, ...]:
    # Crop extensions start beyond the bleed, so trim-relative offset is
    # necessarily at least the corresponding bleed inset plus gap.
    return jp_corner_marks(trim, bleed, gap)


def edge_crosses(
    bleed: Box, gap: float
) -> tuple[tuple[CropMark, ...], tuple[tuple[float, float, float], ...]]:
    x, y, w, h = bleed
    radius = 3.0
    centers = (
        (x + w / 2, y - gap - radius),
        (x + w / 2, y + h + gap + radius),
        (x - gap - radius, y + h / 2),
        (x + w + gap + radius, y + h / 2),
    )
    lines = tuple(
        mark
        for cx, cy in centers
        for mark in (
            CropMark(cx - radius, cy, cx + radius, cy),
            CropMark(cx, cy - radius, cx, cy + radius),
        )
    )
    return lines, tuple((cx, cy, radius) for cx, cy in centers)


def print_marks(
    trim: Box, bleed: Box, options: PrintMarkOptions
) -> tuple[tuple[CropMark, ...], tuple[tuple[float, float, float], ...]]:
    styles = {"jp_corner": jp_corner_marks, "jp_double": jp_double_marks, "western": western_marks}
    marks = list(styles[options.style](trim, bleed, options.gap)) if options.crop else []
    if options.bleed:
        marks.extend(jp_corner_marks(bleed, bleed, options.gap))
    circles = ()
    if options.center or options.registration:
        crosses, rings = edge_crosses(bleed, options.gap)
        marks.extend(crosses)
        circles = rings if options.registration else ()
    return tuple(dict.fromkeys(marks)), circles
