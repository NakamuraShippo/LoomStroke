"""Shared line/column positions for text painting and inline interaction."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, overload

from PySide6.QtGui import QFont, QFontMetricsF, QTextLayout

from .japanese_text import KinsokuPolicy
from .japanese_text.grapheme_clusters import grapheme_spans
from .vector_text_layout import TextLayoutCalculator as Layout


@dataclass(frozen=True, slots=True)
class TextLineGeometry:
    text: str
    start: int
    next_start: int
    x: float
    baseline: float
    offsets: Sequence[float]
    extent: float
    steps: tuple[float, ...] = ()
    justify_extra: float = 0.0
    vertical: bool = False
    clusters: tuple[tuple[int, int], ...] = ()

    @property
    def end(self) -> int:
        return self.start + len(self.text)


class ShapedCursorOffsets(Sequence[float]):
    """Keep a shaped Qt line; resolve only the insertion positions being used.

    Calling ``cursorToX`` for every character makes Qt repeatedly scan a long
    line. Pointer lookup instead uses one ``xToCursor`` call, and caret/selection
    painting only needs one or two positions.
    """

    def __init__(self, text: str, font: QFont) -> None:
        self._layout = QTextLayout(text, font)
        self._layout.beginLayout()
        self._line = self._layout.createLine()
        self._line.setLineWidth(1e12)
        self._layout.endLayout()
        self._utf16 = [0]
        for char in text:
            self._utf16.append(self._utf16[-1] + (2 if ord(char) > 0xFFFF else 1))

    def __len__(self) -> int:
        return len(self._utf16)

    @overload
    def __getitem__(self, index: int) -> float: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[float]: ...

    def __getitem__(self, index: int | slice) -> float | Sequence[float]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        return float(self._line.cursorToX(self._utf16[index])[0])

    def index_at(self, x: float) -> int:
        return max(0, bisect_right(self._utf16, self._line.xToCursor(x)) - 1)


def shaped_cursor_offsets(text: str, font: QFont) -> Sequence[float]:
    """Resolve Qt UTF-16 insertion positions lazily in Python character space."""
    return ShapedCursorOffsets(text, font) if text else (0.0,)


def horizontal_line_geometry(
    content: str,
    metrics: QFontMetricsF,
    *,
    font: QFont,
    x: float,
    y: float,
    w: float,
    align: str,
    line_height: float,
    tracking: float = 0.0,
    tracking_runs: Sequence[Mapping[str, Any]] | None = None,
    line_height_runs: Sequence[Mapping[str, Any]] | None = None,
    wrap_mode: str = "none",
    cursor_offsets: bool = True,
) -> list[TextLineGeometry]:
    lines = (
        (content.splitlines() or [content])
        if wrap_mode == "none"
        else Layout.wrap_text_lines(
            content,
            metrics,
            max(1.0, w - 2.0),
            wrap_mode=wrap_mode,
        )
    )
    starts, final = Layout.horizontal_text_line_offsets(content, lines, wrap_mode)
    ys = Layout.horizontal_text_line_y_offsets(
        lines=lines,
        line_offsets=starts,
        final_offset=final,
        line_step=metrics.lineSpacing() * line_height,
        line_height_runs=line_height_runs,
        base_line_spacing=metrics.lineSpacing(),
        line_height=line_height,
    )
    result = []
    for i, text in enumerate(lines):
        start = starts[i]
        extent = Layout.horizontal_text_total_width(
            text,
            start,
            metrics=metrics,
            tracking=tracking,
            tracking_runs=tracking_runs,
        )
        extra = Layout.horizontal_text_justify_extra(
            is_justify=align == "justify",
            line_index=i,
            line_count=len(lines),
            line_text=text,
            line_width=extent,
            available_width=w - 2.0,
        )
        caret_offsets: Sequence[float]
        if tracking_runs or extra > 0:
            offsets = Layout.horizontal_text_char_x_offsets(
                text,
                start,
                metrics=metrics,
                tracking=tracking,
                tracking_runs=tracking_runs,
                extra=extra,
            )
            end = 0.0
            if text:
                end = (
                    offsets[-1]
                    + metrics.horizontalAdvance(text[-1])
                    + Layout.horizontal_text_tracking_at(
                        start + len(text) - 1,
                        tracking=tracking,
                        tracking_runs=tracking_runs,
                    )
                )
            caret_offsets = (*offsets, end)
        elif cursor_offsets:
            caret_offsets = shaped_cursor_offsets(text, font)
        else:
            caret_offsets = (0.0,)
        result.append(
            TextLineGeometry(
                text=text,
                start=start,
                next_start=starts[i + 1] if i + 1 < len(starts) else final,
                x=Layout.horizontal_text_aligned_x(x=x, width=w, line_width=extent, align=align),
                baseline=y + metrics.ascent() + 2.0 + ys[i],
                offsets=caret_offsets,
                extent=extent,
                justify_extra=extra,
            )
        )
    return result


def vertical_line_geometry(
    content: str,
    metrics: QFontMetricsF,
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    align: str,
    vertical_flow: str,
    line_height: float,
    tracking: float = 0.0,
    tracking_runs: list[dict[str, Any]] | None = None,
    line_height_runs: Sequence[Mapping[str, Any]] | None = None,
    wrap_mode: str = "none",
) -> list[TextLineGeometry]:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    vertical_flow = str(vertical_flow).strip().lower()
    ascent = metrics.ascent()
    base_step = metrics.lineSpacing()
    base_column = max(metrics.height(), metrics.horizontalAdvance("M"))
    column_step = base_column * line_height
    spans = Layout.vertical_text_column_spans(
        text,
        height=h,
        ascent=ascent,
        base_char_step=base_step,
        tracking=tracking,
        tracking_runs=tracking_runs,
        wrap_mode=wrap_mode,
        # Text objects clip to their fixed frame. Pull punctuation and its
        # preceding character into the next column instead of hanging outside
        # that clip and silently omitting the punctuation from the painting.
        policy=KinsokuPolicy.standard(hanging=False),
    )
    column_x = x + w - ascent if vertical_flow == "left" else x + ascent
    result = []
    for i, span in enumerate(spans):
        column = span.text(text)
        clusters = grapheme_spans(column)
        steps = [0.0] * max(1, len(column))
        offsets = [0.0] * (len(column) + 1)
        extent = 0.0
        for cluster_start, cluster_end in clusters:
            step = base_step + Layout.horizontal_text_tracking_at(
                span.start + cluster_start,
                tracking=tracking,
                tracking_runs=tracking_runs,
            )
            for index in range(cluster_start, cluster_end):
                offsets[index] = extent
                steps[index] = step
            extent += step
        offsets[-1] = extent
        if not clusters:
            extent = steps[0] = base_step + Layout.horizontal_text_tracking_at(
                span.start, tracking=tracking, tracking_runs=tracking_runs
            )
        extra = 0.0
        if align == "justify" and i < len(spans) - 1 and len(clusters) > 1 and h > extent:
            extra = max(0.0, (h - 2.0 - extent) / (len(clusters) - 1))
            for j, (cluster_start, cluster_end) in enumerate(clusters):
                for index in range(cluster_start, cluster_end):
                    offsets[index] += extra * j
            offsets[-1] += extra * (len(clusters) - 1)
        if align == "center":
            baseline = y + (h - extent) * 0.5 + ascent
        elif align == "right":
            baseline = y + h - extent + ascent - 2.0
        else:
            baseline = y + ascent + 2.0
        result.append(
            TextLineGeometry(
                text=column,
                start=span.start,
                next_start=span.next_start,
                x=column_x,
                baseline=baseline,
                offsets=tuple(offsets),
                extent=extent,
                steps=tuple(steps),
                justify_extra=extra,
                vertical=True,
                clusters=clusters,
            )
        )
        step = column_step
        for run in line_height_runs or ():
            if int(run["start"]) < span.end and int(run["end"]) > span.start:
                step = base_column * float(run.get("line_height", line_height))
                break
        column_x += -step if vertical_flow == "left" else step
    return result
