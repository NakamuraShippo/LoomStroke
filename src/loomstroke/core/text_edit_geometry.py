"""Canvas text geometry for pointer positions and IME candidate placement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFont, QFontMetricsF, QTransform

from .canvas_models import _MaskShapeContour
from .japanese_text.typography import has_typography
from .text_line_geometry import (
    ShapedCursorOffsets,
    TextLineGeometry,
    horizontal_line_geometry,
    vertical_line_geometry,
)
from .text_shape_geometry import shape_line_geometry
from .vector_text_layout import TextLayoutCalculator as Layout


def python_index_from_utf16(text: str, position: int) -> int:
    """Clamp Qt's UTF-16 position to the preceding Python character boundary."""
    remaining = max(0, int(position))
    for index, char in enumerate(text):
        remaining -= 2 if ord(char) > 0xFFFF else 1
        if remaining < 0:
            return index
        if remaining == 0:
            return index + 1
    return len(text)


def utf16_index_from_python(text: str, position: int) -> int:
    return sum(2 if ord(char) > 0xFFFF else 1 for char in text[: max(0, int(position))])


@dataclass(slots=True)
class TextEditGeometry:
    content: str
    lines: list[TextLineGeometry]
    metrics: QFontMetricsF
    transform: QTransform
    frame: QRectF
    vertical: bool
    column_step: float
    shape_args: dict[str, Any] | None = None
    carets: dict[int, QRectF] | None = None

    def index_at(self, point: QPointF) -> int:
        if not self.lines:
            return 0
        local = self.transform.inverted()[0].map(point)
        if self.carets:
            return min(
                self.carets,
                key=lambda index: (
                    max(
                        self.carets[index].left() - local.x(),
                        local.x() - self.carets[index].right(),
                        0.0,
                    )
                    ** 2
                    + max(
                        self.carets[index].top() - local.y(),
                        local.y() - self.carets[index].bottom(),
                        0.0,
                    )
                    ** 2
                ),
            )
        if self.vertical:
            line = min(self.lines, key=lambda item: abs(item.x - local.x()))
            value = local.y() - (line.baseline - self.metrics.ascent())
        else:
            # Several shape-wrap segments can share a baseline.
            center_offset = (self.metrics.descent() - self.metrics.ascent()) * 0.5
            baseline = min(
                self.lines,
                key=lambda item: abs(item.baseline + center_offset - local.y()),
            ).baseline
            row = [item for item in self.lines if abs(item.baseline - baseline) < 1e-6]
            line = min(
                row, key=lambda item: max(item.x - local.x(), local.x() - item.x - item.extent, 0)
            )
            value = local.x() - line.x
        if isinstance(line.offsets, ShapedCursorOffsets):
            offset = line.offsets.index_at(value)
        else:
            offset = min(
                range(min(len(line.text) + 1, len(line.offsets))),
                key=lambda i: abs(line.offsets[i] - value),
            )
        return min(len(self.content), line.start + offset)

    def caret_rect(self, index: int) -> QRectF:
        target = max(0, min(len(self.content), index))
        if self.carets:
            target = max((i for i in self.carets if i <= target), default=min(self.carets))
            return self.transform.mapRect(self.carets[target])
        ascent = self.metrics.ascent()
        if self.shape_args is not None:
            caret = Layout.shape_caret_position(
                content=self.content, cursor_pos=target, **self.shape_args
            )
            if caret is not None:
                x, baseline = caret
                rect = QRectF(
                    x,
                    baseline - ascent,
                    1.0,
                    max(
                        1.0,
                        min(self.frame.bottom() - 1.0, baseline + max(1.0, self.metrics.descent()))
                        - (baseline - ascent),
                    ),
                )
                return self.transform.mapRect(rect)
        candidates = [line for line in self.lines if line.start <= target <= line.end]
        # A soft break shares an index with the following line/column.
        line = candidates[-1] if candidates else (self.lines[-1] if self.lines else None)
        if line is None:
            return self.transform.mapRect(
                QRectF(self.frame.x() + 1, self.frame.y() + 2, 1, max(1, self.metrics.height()))
            )
        offset = min(max(0, target - line.start), len(line.offsets) - 1)
        if self.vertical:
            y = max(
                self.frame.top() + 1,
                min(self.frame.bottom() - 1, line.baseline + line.offsets[offset] - ascent),
            )
            x = max(self.frame.left() + 1, min(self.frame.right() - 2, line.x))
            half = max(2.0, self.column_step * 0.35)
            rect = QRectF(x - half, y, half * 2, 1)
        else:
            top = line.baseline - ascent
            bottom = min(self.frame.bottom() - 1, line.baseline + max(1.0, self.metrics.descent()))
            rect = QRectF(line.x + line.offsets[offset], top, 1, max(1, bottom - top))
        return self.transform.mapRect(rect)

    def adjacent_column_index(self, index: int, direction: int) -> int:
        """Move to the visual neighbouring column without using Qt's hidden rows."""
        candidates = [line for line in self.lines if line.start <= index <= line.end]
        if not candidates:
            return index
        current = candidates[-1]
        columns = [line for line in self.lines if (line.x - current.x) * direction > 1e-6]
        if not columns:
            return index
        adjacent = min(columns, key=lambda line: abs(line.x - current.x))
        offset = min(max(0, index - current.start), len(current.offsets) - 1)
        target = QPointF(
            adjacent.x,
            current.baseline - self.metrics.ascent() + current.offsets[offset],
        )
        return self.index_at(self.transform.map(target))


def build_text_edit_geometry(
    payload: Mapping[str, Any],
    content: str,
    *,
    zoom: float = 1.0,
    font: QFont | None = None,
    origin_offset: tuple[float, float] = (0.0, 0.0),
    frame_to_editor: QTransform | None = None,
) -> TextEditGeometry:
    """Use document coordinates until the final rotation and widget-zoom transform."""
    bounds = Layout.text_bbox_from_payload(payload) or (0.0, 0.0, 1.0, 1.0)
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x, y, w, h = Layout.text_padded_rect(
        0.0, 0.0, width, height, Layout.text_padding_from_payload(payload)
    )
    style = Layout.text_style_defaults(payload)
    vertical = bool(style.get("vertical", payload.get("vertical", False)))
    mode = Layout.text_wrap_mode_from_payload(
        payload, text_mode=Layout.text_mode_from_payload(payload)
    )
    shape = (
        Layout.text_shape_path_from_payload(
            payload, x=0.0, y=0.0, w=width, h=height, contour_factory=_MaskShapeContour
        )
        if mode == "shape" and not vertical
        else None
    )
    text_font = QFont(font) if font is not None else Layout.make_text_font(payload)
    tracking_runs = Layout.text_range_runs_from_payload(payload, "tracking_runs")
    if tracking_runs and not vertical and shape is None:
        text_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    metrics = QFontMetricsF(text_font)
    line_height = Layout.text_line_height_from_payload(payload)
    args = dict(
        x=x,
        y=y,
        w=w,
        align=Layout.text_align_from_payload(payload),
        line_height=line_height,
        tracking=Layout.text_tracking_from_payload(payload),
        tracking_runs=tracking_runs,
        line_height_runs=Layout.text_range_runs_from_payload(payload, "line_height_runs"),
        wrap_mode=mode,
    )
    shape_args = None
    carets = None
    column_step = max(metrics.height(), metrics.horizontalAdvance("M")) * line_height
    if has_typography(payload):
        from .japanese_text.typography_layout import build_japanese_layout  # noqa: PLC0415

        result = build_japanese_layout(
            payload, content=content, font=text_font, rect=QRectF(x, y, w, h), shape_path=shape
        )
        lines = list(result.lines)
        carets = result.carets
        column_step = result.em * line_height
    elif vertical:
        lines = vertical_line_geometry(
            content,
            metrics,
            h=h,
            vertical_flow=Layout.text_vertical_flow_from_payload(payload),
            **args,
        )
    elif shape is not None:
        shape_args = dict(
            metrics=metrics, x=x, y=y, w=w, h=h, line_height=line_height, shape_path=shape
        )
        lines = shape_line_geometry(content, font=text_font, **shape_args)
    else:
        lines = horizontal_line_geometry(content, metrics, font=text_font, **args)
    transform = QTransform(frame_to_editor) if frame_to_editor is not None else QTransform()
    if frame_to_editor is None:
        transform.translate(float(origin_offset[0]), float(origin_offset[1]))
        transform.scale(max(0.01, zoom), max(0.01, zoom))
    transform.translate(width * 0.5, height * 0.5)
    transform.rotate(Layout.text_rotation_from_payload(payload))
    transform.translate(-width * 0.5, -height * 0.5)
    return TextEditGeometry(
        content,
        lines,
        metrics,
        transform,
        QRectF(x, y, w, h),
        vertical,
        column_step,
        shape_args,
        carets,
    )
