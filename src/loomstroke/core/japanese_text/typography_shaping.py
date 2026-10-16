"""Collect shaped glyph origins once for linear-time run caret placement."""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextLayout


def run_cursor_offsets(text: str, font: QFont) -> tuple[float, ...]:
    if not text:
        return (0.0,)
    layout = QTextLayout(text, font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1e12)
    layout.endLayout()
    utf16, units = [0], 0
    for char in text:
        units += 2 if ord(char) > 0xFFFF else 1
        utf16.append(units)
    # Exact Qt insertion positions retain complex short ruby/TCY shaping.
    # Bound this path: calling cursorToX for an unbounded run is quadratic.
    if len(text) <= 128:
        return tuple(float(line.cursorToX(index)[0]) for index in utf16)
    flags = QTextLayout.GlyphRunRetrievalFlag
    origins: dict[int, float] = {0: 0.0}
    for run in layout.glyphRuns(0, -1, flags.RetrieveGlyphPositions | flags.RetrieveStringIndexes):
        for index, position in zip(run.stringIndexes(), run.positions(), strict=True):
            origins.setdefault(index, float(position.x()))
    origins[0] = float(line.cursorToX(0)[0])
    origins[units] = float(line.cursorToX(units)[0])
    known = sorted(origins)
    cursor, result = 0, []
    for index in utf16:
        while cursor + 1 < len(known) and known[cursor + 1] <= index:
            cursor += 1
        left = known[cursor]
        right = known[min(cursor + 1, len(known) - 1)]
        ratio = (index - left) / (right - left) if right > left else 0.0
        result.append(origins[left] + (origins[right] - origins[left]) * ratio)
    return tuple(result)
