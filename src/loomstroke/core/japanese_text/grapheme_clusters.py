"""Qt grapheme boundaries expressed in the unchanged Python string's indices."""

from __future__ import annotations

from PySide6.QtCore import QTextBoundaryFinder


def grapheme_spans(
    text: str, start: int = 0, end: int | None = None
) -> tuple[tuple[int, int], ...]:
    """Return indivisible user-perceived characters, without normalizing text.

    Qt boundaries use UTF-16 positions. The one-pass map keeps supplementary
    characters, combining marks, variation selectors and emoji ZWJ sequences
    associated with their original code-point ranges.
    """
    stop = len(text) if end is None else end
    segment = text[start:stop]
    if not segment:
        return ()
    utf16_to_python = {0: start}
    units = 0
    for index, char in enumerate(segment, start + 1):
        units += 2 if ord(char) > 0xFFFF else 1
        utf16_to_python[units] = index
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, segment)
    result = []
    previous = start
    while (boundary := finder.toNextBoundary()) >= 0:
        current = utf16_to_python[boundary]
        if current > previous:
            result.append((previous, current))
            previous = current
    return tuple(result)
