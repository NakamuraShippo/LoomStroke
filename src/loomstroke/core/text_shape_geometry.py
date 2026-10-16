"""Shared horizontal shape-wrap segments, in canvas logical pixels."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QFont, QFontMetricsF

from .text_line_geometry import TextLineGeometry, shaped_cursor_offsets
from .vector_text_layout import TextLayoutCalculator as Layout


def shape_line_geometry(
    content: str,
    metrics: QFontMetricsF,
    *,
    font: QFont,
    x: float,
    y: float,
    w: float,
    h: float,
    line_height: float,
    shape_path: Any,
    cursor_offsets: bool = True,
) -> list[TextLineGeometry]:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    ascent = metrics.ascent()
    baseline = y + ascent + 2.0
    bottom = y + h - 2.0
    step = metrics.lineSpacing() * line_height
    cursor = 0
    result = []
    while baseline <= bottom and cursor < len(text):
        segments = Layout.shape_segments_at_y(
            shape_path,
            x + 1.0,
            x + w - 1.0,
            baseline - ascent * 0.55,
        )
        for seg_x0, seg_x1 in segments:
            while cursor < len(text) and text[cursor] == " ":
                cursor += 1
            if cursor >= len(text):
                break
            if text[cursor] == "\n":
                cursor += 1
                break
            available = max(0.0, seg_x1 - seg_x0 - 2.0)
            if available <= 1.0:
                continue
            draw_text, consumed, newline = Layout.fit_shape_line_text(
                text[cursor:], metrics, available
            )
            if consumed <= 0:
                continue
            result.append(
                TextLineGeometry(
                    text=draw_text,
                    start=cursor,
                    next_start=cursor + consumed,
                    x=seg_x0 + 1.0,
                    baseline=baseline,
                    offsets=shaped_cursor_offsets(draw_text, font) if cursor_offsets else (0.0,),
                    extent=metrics.horizontalAdvance(draw_text),
                )
            )
            cursor += consumed
            if newline:
                break
        baseline += step
    return result
