"""Fit explicitly adjusted characters into the existing vertical TCY cell."""

from dataclasses import replace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from .character_geometry import adjusted_advance


def place_geometry_tcy(placer, atom, cell, line_index):
    from .typography_placement import GlyphPlacer  # noqa: PLC0415

    glyphs, carets, envelope, cursor = [], {}, QRectF(), 0.0
    for child in atom.children:
        font = QFont(child.font or placer.font)
        font.unsetFeature(QFont.Tag("vert"))
        font.unsetFeature(QFont.Tag("vrt2"))
        metrics = QFontMetricsF(font)
        advance = adjusted_advance(
            metrics.horizontalAdvance(child.text), child.geometry, child.em or placer.em, False
        )
        part, insertion = GlyphPlacer(font, placer.em, False, placer.options).place(
            replace(child, font=font), QRectF(cursor, 0, advance, placer.em), line_index
        )
        glyphs.extend(part)
        carets.update(insertion)
        for glyph in part:
            envelope = envelope.united(glyph.cell).united(
                glyph.transform.mapRect(QFontMetricsF(glyph.font).tightBoundingRect(glyph.text))
            )
        cursor += advance
    scale = min(
        1.0,
        cell.width() / max(0.01, envelope.width()),
        cell.height() / max(0.01, envelope.height()),
    )
    transform = (
        QTransform()
        .translate(
            cell.center().x() - envelope.center().x() * scale,
            cell.center().y() - envelope.center().y() * scale,
        )
        .scale(scale, scale)
    )
    return (
        [
            replace(
                g,
                transform=g.transform * transform,
                cell=transform.mapRect(g.cell),
                role="tcy",
                group_range=(atom.start, atom.end),
                group_cell=QRectF(cell),
            )
            for g in glyphs
        ],
        {index: transform.mapRect(rect) for index, rect in carets.items()},
    )
