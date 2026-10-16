"""Fit multiple font runs on one baseline inside a vertical TCY cell."""

from itertools import groupby

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from .typography_layout_types import PositionedGlyph
from .typography_shaping import run_cursor_offsets


def place_mixed_tcy(atom, cell, line_index):
    runs, envelope, cursor = [], QRectF(), 0.0
    for _key, group in groupby(atom.children, key=lambda child: child.font.toString()):
        children = list(group)
        font = QFont(children[0].font)
        font.unsetFeature(QFont.Tag("vert"))
        font.unsetFeature(QFont.Tag("vrt2"))
        text = "".join(child.text for child in children)
        metrics = QFontMetricsF(font)
        width = metrics.horizontalAdvance(text)
        bounds = QRectF(cursor, -metrics.ascent(), width, metrics.height())
        envelope = envelope.united(bounds).united(
            metrics.tightBoundingRect(text).translated(cursor, 0)
        )
        runs.append((children[0].start, children[-1].end, text, font, cursor, width, metrics))
        cursor += width
    scale = min(
        1.0,
        cell.width() / max(0.01, envelope.width()),
        cell.height() / max(0.01, envelope.height()),
    )
    x = cell.center().x() - envelope.center().x() * scale
    y = cell.center().y() - envelope.center().y() * scale
    glyphs, carets = [], {}
    for start, end, text, font, offset, width, metrics in runs:
        transform = QTransform().translate(x + offset * scale, y).scale(scale, scale)
        bounds = QRectF(
            x + offset * scale,
            y - metrics.ascent() * scale,
            width * scale,
            metrics.height() * scale,
        )
        glyphs.append(
            PositionedGlyph(
                text,
                font,
                transform,
                start,
                end,
                "tcy",
                line_index,
                bounds,
                group_range=(atom.start, atom.end),
                group_cell=QRectF(cell),
            )
        )
        for index, advance in enumerate(run_cursor_offsets(text, font)):
            carets[start + index] = QRectF(
                x + (offset + advance) * scale, bounds.top(), 1.0, bounds.height()
            )
    return glyphs, carets
