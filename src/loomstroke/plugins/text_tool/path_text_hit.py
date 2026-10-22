"""Pick rendered path text cells instead of the contour's large input frame."""

from PySide6.QtCore import QPointF

from loomstroke.core.japanese_text.typography_layout import build_japanese_layout
from loomstroke.core.japanese_text.typography_painter import text_rotation_transform


def path_body_contains(payload, x, y, *, pad=0):
    local = text_rotation_transform(payload).inverted()[0].map(QPointF(x, y))
    layout = build_japanese_layout(payload)
    tolerance = max(2.0, pad)
    return any(
        glyph.cell.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(local)
        for glyph in layout.glyphs
    )
