"""Paint the shared Japanese layout, including its editing overlays."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainterPath, QPen, QTransform

from ..text_line_geometry import shaped_cursor_offsets
from ..vector_text_layout import TextLayoutCalculator as Layout
from .character_color_paint import apply_glyph_clip, glyph_color_parts
from .character_styles import color_runs
from .legacy_color_layout import legacy_clip_path, legacy_selection_rects
from .typography import has_japanese_layout
from .typography_layout import build_japanese_layout


def text_rotation_transform(payload: Mapping[str, Any]) -> QTransform:
    """Rotate around the saved frame, independently of ruby/overflow ink."""
    bounds = Layout.text_bbox_from_payload(payload) or (0.0, 0.0, 1.0, 1.0)
    transform = QTransform()
    cx, cy = (bounds[0] + bounds[2]) * 0.5, (bounds[1] + bounds[3]) * 0.5
    transform.translate(cx, cy)
    transform.rotate(Layout.text_rotation_from_payload(payload))
    transform.translate(-cx, -cy)
    return transform


def japanese_ink_bounds(payload: Mapping[str, Any]) -> QRectF:
    """Canvas-space ink bounds; the editable frame itself remains unchanged."""
    result = build_japanese_layout(payload)
    return text_rotation_transform(payload).mapRect(result.ink_bounds)


def japanese_outline_path(payload: Mapping[str, Any]) -> QPainterPath:
    """Use the same positioned glyphs for outline and SVG export."""
    result = build_japanese_layout(payload)
    path = QPainterPath()
    for glyph in result.glyphs:
        outline = QPainterPath()
        outline.addText(QPointF(), glyph.font, glyph.text)
        path.addPath(glyph.transform.map(outline))
    if not has_japanese_layout(payload):
        path = path.intersected(legacy_clip_path(payload, result.frame))
    return text_rotation_transform(payload).map(path)


def _selection_rect(glyph: Any, start: int, end: int) -> QRectF:
    """Keep a partial Latin/TCY selection inside the actual shaped run."""
    if start <= glyph.start and end >= glyph.end:
        return QRectF(glyph.cell)
    offsets = shaped_cursor_offsets(glyph.text, glyph.font)
    left = min(len(offsets) - 1, max(0, start - glyph.start))
    right = min(len(offsets) - 1, max(left, end - glyph.start))
    metrics = QFontMetricsF(glyph.font)
    rect = QRectF(
        offsets[left], -metrics.ascent(), offsets[right] - offsets[left], metrics.height()
    )
    return glyph.transform.mapRect(rect)


def _draw_selection(painter: Any, result: Any, context: Any) -> None:
    selection = context.inline_selection
    if selection is None:
        return
    color = QColor(context.inline_cursor_color)
    if color.alpha() <= 0:
        color.setAlpha(255)
    color.setAlpha(max(32, round(color.alpha() * 0.35)))
    payload = context.typography_payload
    if not has_japanese_layout(payload) and bool(
        Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
    ):
        alpha = context.inline_cursor_color.alpha() or 255
        color.setAlpha(max(32, int(alpha * 0.35)))
        for rect in legacy_selection_rects(result, payload, context.font, *selection):
            painter.fillRect(rect, color)
        return
    for glyph in result.glyphs:
        if (
            glyph.role in {"base", "tcy"}
            and selection[0] < glyph.end
            and glyph.start < selection[1]
        ):
            painter.fillRect(_selection_rect(glyph, *selection), color)


def draw_japanese_text_object(
    painter: Any,
    context: Any,
    *,
    shape_path_for_rect: Callable[[float, float, float, float], Any] | None = None,
) -> None:
    payload = context.typography_payload
    if not (context.content or context.inline_cursor_pos is not None):
        return
    if context.selection_policy.hide_source_text:
        return
    shape_path = None
    if shape_path_for_rect is not None and context.wrap_mode == "shape" and not context.vertical:
        x0, y0, x1, y1 = context.draw_rect
        shape_path = shape_path_for_rect(x0, y0, x1 - x0, y1 - y0)
    result = build_japanese_layout(
        payload, content=context.content, font=context.font, shape_path=shape_path
    )
    colors = color_runs(payload)
    painter.save()
    try:
        painter.setPen(QPen(context.color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setWorldTransform(text_rotation_transform(payload), True)
        if not has_japanese_layout(payload):
            if isinstance(shape_path, QPainterPath):
                painter.setClipPath(shape_path, Qt.ClipOperation.IntersectClip)
            else:
                x0, y0, x1, y1 = context.draw_rect
                painter.setClipRect(
                    round(x0),
                    round(y0),
                    round(x1 - x0),
                    round(y1 - y0),
                    Qt.ClipOperation.IntersectClip,
                )
        # Advanced typography keeps ruby and hanging punctuation visible. The
        # layout reports overflow, and raster bounds include the resulting ink.
        _draw_selection(painter, result, context)
        _draw_colored_glyphs(painter, result.glyphs, colors, context.color.getRgb())
        cursor = context.inline_cursor_pos
        if cursor is not None and result.carets:
            key = max((i for i in result.carets if i <= cursor), default=min(result.carets))
            rect = result.carets[key]
            color = QColor(context.inline_cursor_color)
            if color.alpha() <= 0:
                color.setAlpha(255)
            painter.setPen(QPen(color, 1.0))
            if rect.width() > rect.height():
                painter.drawLine(rect.topLeft(), rect.topRight())
            else:
                painter.drawLine(rect.topLeft(), rect.bottomLeft())
    finally:
        painter.restore()


def _draw_colored_glyphs(painter, glyphs, colors, default):
    for glyph in glyphs:
        painter.save()
        try:
            painter.setFont(glyph.font)
            painter.setWorldTransform(glyph.transform, True)
            for rgba, clip in glyph_color_parts(glyph, colors, default):
                painter.save()
                try:
                    painter.setPen(QPen(QColor(*rgba), 1))
                    if clip is not None:
                        apply_glyph_clip(painter, clip)
                    painter.drawText(QPointF(), glyph.text)
                finally:
                    painter.restore()
        finally:
            painter.restore()
