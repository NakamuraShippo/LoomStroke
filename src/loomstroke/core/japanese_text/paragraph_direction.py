"""Reverse paragraph column order while retaining each glyph's orientation."""

from dataclasses import replace

from PySide6.QtGui import QTransform


def reverse_columns(lines, glyphs, carets, first_line, first_glyph, end, regions=None):
    columns = lines[first_line:]
    if len(columns) < 2:
        return
    axes = {}
    for index in range(first_line, len(lines)):
        region = regions[index] if regions is not None else None
        low, high = axes.get(region, (lines[index].x, lines[index].x))
        axes[region] = min(low, lines[index].x), max(high, lines[index].x)
    shifts = {
        index: sum(axes[regions[index] if regions is not None else None]) - 2 * lines[index].x
        for index in range(first_line, len(lines))
    }
    for index in range(first_glyph, len(glyphs)):
        glyph = glyphs[index]
        dx = shifts[glyph.line_index]
        glyphs[index] = replace(
            glyph,
            transform=glyph.transform * QTransform.fromTranslate(dx, 0),
            cell=glyph.cell.translated(dx, 0),
            group_cell=glyph.group_cell.translated(dx, 0) if glyph.group_cell is not None else None,
        )
    for index in range(first_line, len(lines)):
        line = lines[index]
        dx = shifts[index]
        stop = lines[index + 1].start if index + 1 < len(lines) else end + 1
        for position in range(line.start, stop):
            if position in carets:
                carets[position] = carets[position].translated(dx, 0)
        lines[index] = replace(line, x=line.x + dx)
