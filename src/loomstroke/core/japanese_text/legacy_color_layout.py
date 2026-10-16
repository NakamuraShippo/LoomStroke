"""Expose the existing legacy line geometry as positioned glyphs for range color."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontInfo, QFontMetricsF, QPainterPath, QTransform

from ..text_line_geometry import horizontal_line_geometry, vertical_line_geometry
from ..text_shape_geometry import shape_line_geometry
from ..vector_text_layout import TextLayoutCalculator as Layout
from ..vertical_font_forms import VerticalFontForms
from .typography_frame import resolve_shape
from .typography_layout_types import JapaneseLayoutResult, PositionedGlyph
from .typography_shaping import run_cursor_offsets


def legacy_color_layout(payload, text, font, frame, shape):
    """Reuse legacy wrapping, alignment, metrics and glyph substitution exactly."""
    vertical = bool(
        Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
    )
    font = QFont(font)
    tracking = Layout.text_range_runs_from_payload(payload, "tracking_runs")
    if tracking and not vertical and shape is None:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
    metrics = QFontMetricsF(font)
    line_height = Layout.text_line_height_from_payload(payload)
    mode = Layout.text_wrap_mode_from_payload(
        payload, text_mode=Layout.text_mode_from_payload(payload)
    )
    args = dict(
        x=frame.x(),
        y=frame.y(),
        w=frame.width(),
        align=Layout.text_align_from_payload(payload),
        line_height=line_height,
        tracking=Layout.text_tracking_from_payload(payload),
        tracking_runs=tracking,
        line_height_runs=Layout.text_range_runs_from_payload(payload, "line_height_runs"),
        wrap_mode=mode,
    )
    shape_args = None
    if vertical:
        lines = vertical_line_geometry(
            text,
            metrics,
            h=frame.height(),
            vertical_flow=Layout.text_vertical_flow_from_payload(payload),
            **args,
        )
    elif mode == "shape" and shape is not None:
        shape_args = dict(
            metrics=metrics,
            x=frame.x(),
            y=frame.y(),
            w=frame.width(),
            h=frame.height(),
            line_height=line_height,
            shape_path=shape,
        )
        lines = shape_line_geometry(text, font=font, **shape_args)
    else:
        lines = horizontal_line_geometry(text, metrics, font=font, **args)
    step = max(metrics.height(), metrics.horizontalAdvance("M")) * line_height
    forms = VerticalFontForms(font)
    glyphs, ink, occupied = [], QRectF(), QRectF()
    for line_index, line in enumerate(lines):
        if vertical:
            if line.x < frame.left() + 1 or line.x > frame.right() - 2:
                break
            pieces = _vertical_glyphs(line, forms, metrics, step, payload, frame)
        else:
            if line.baseline > frame.bottom() - 2:
                break
            if tracking or line.justify_extra > 0:
                pieces = [
                    (
                        char,
                        font,
                        QTransform.fromTranslate(line.x + line.offsets[i], line.baseline),
                        line.start + i,
                        line.start + i + 1,
                    )
                    for i, char in enumerate(line.text)
                ]
            else:
                pieces = [
                    (
                        line.text,
                        font,
                        QTransform.fromTranslate(line.x, line.baseline),
                        line.start,
                        line.end,
                    )
                ]
        for value, selected_font, transform, start, end in pieces:
            fm = QFontMetricsF(selected_font)
            cell = transform.mapRect(
                QRectF(0, -fm.ascent(), fm.horizontalAdvance(value), fm.height())
            )
            glyphs.append(
                PositionedGlyph(
                    value, selected_font, transform, start, end, line_index=line_index, cell=cell
                )
            )
            ink = ink.united(transform.mapRect(fm.tightBoundingRect(value)))
            occupied = occupied.united(cell)
    carets = _legacy_carets(lines, font, metrics, frame, vertical, tracking, step)
    return JapaneseLayoutResult(
        tuple(lines),
        tuple(glyphs),
        ink.intersected(legacy_clip_path(payload, frame).boundingRect()),
        occupied,
        frame,
        float(QFontInfo(font).pixelSize()),
        (),
        carets,
    )


def _vertical_glyphs(line, forms, metrics, step, payload, frame):
    ref_x, ref_y = Layout.vertical_reference_center_offset(metrics)
    rotate = bool(
        Layout.text_style_defaults(payload).get(
            "rotate_half_width", payload.get("rotate_half_width", False)
        )
    )
    for start, end in line.clusters:
        text = line.text[start:end]
        baseline = line.baseline + line.offsets[start]
        if baseline > frame.bottom():
            break
        glyph, angle, dx, dy = Layout.vertical_glyph_draw_spec(
            text,
            metrics=metrics,
            column_step=step,
            char_step=line.steps[start],
            vertical_flow=Layout.text_vertical_flow_from_payload(payload),
            rotate_half_width=rotate,
        )
        alternate = (glyph, angle, dx, dy) == (text, 0.0, 0.0, 0.0) or forms.has_alternate(text)
        font = forms.font if alternate else forms.base_font
        if alternate:
            glyph, angle, dx, dy = text, 0.0, 0.0, 0.0
        transform = QTransform.fromTranslate(line.x + dx, baseline + dy)
        if abs(angle) > 1e-6:
            center = metrics.tightBoundingRect(glyph).center()
            transform = (
                QTransform().translate(line.x + ref_x + dx, baseline + ref_y + dy).rotate(angle)
            )
            transform.translate(-center.x(), -center.y())
        yield glyph, font, transform, line.start + start, line.start + end


def _legacy_carets(lines, font, metrics, frame, vertical, tracking, step):
    carets = {}
    for line in lines:
        offsets = (
            line.offsets
            if vertical or tracking or line.justify_extra
            else run_cursor_offsets(line.text, font)
        )
        for i, offset in enumerate(offsets):
            if vertical:
                y = max(
                    frame.top() + 1,
                    min(frame.bottom() - 1, line.baseline + offset - metrics.ascent()),
                )
                x = max(frame.left() + 1, min(frame.right() - 2, line.x))
                half = max(2.0, step * 0.35)
                cell = QRectF(x - half, y, half * 2, 1)
            else:
                top = line.baseline - metrics.ascent()
                bottom = min(frame.bottom() - 1, line.baseline + max(1.0, metrics.descent()))
                cell = QRectF(line.x + offset, top, 1, max(1, bottom - top))
            carets[line.start + i] = cell
    return carets


def legacy_clip_path(payload, frame):
    """Clip to the outer frame, retaining its padding for glyph overhangs."""
    shape = resolve_shape(payload, frame)
    vertical = bool(
        Layout.text_style_defaults(payload).get("vertical", payload.get("vertical", False))
    )
    if isinstance(shape, QPainterPath) and not vertical:
        return shape
    x0, y0, x1, y1 = Layout.text_bbox_from_payload(payload) or frame.getCoords()
    path = QPainterPath()
    path.addRect(QRectF(round(x0), round(y0), round(x1 - x0), round(y1 - y0)))
    return path


def legacy_selection_rects(result, payload, font, start, end):
    """Retain the legacy vertical selection cells when only color is overridden."""
    metrics = QFontMetricsF(font)
    step = max(
        metrics.height(), metrics.horizontalAdvance("M")
    ) * Layout.text_line_height_from_payload(payload)
    forms = VerticalFontForms(font)
    for line in result.lines:
        if line.x < result.frame.left() + 1 or line.x > result.frame.right() - 2:
            break
        for first, last in line.clusters:
            baseline = line.baseline + line.offsets[first]
            if baseline > result.frame.bottom():
                break
            if start >= line.start + last or end <= line.start + first:
                continue
            _glyph, _angle, dx, dy = Layout.vertical_glyph_draw_spec(
                line.text[first:last],
                metrics=metrics,
                column_step=step,
                char_step=line.steps[first],
                vertical_flow=Layout.text_vertical_flow_from_payload(payload),
                rotate_half_width=bool(
                    Layout.text_style_defaults(payload).get(
                        "rotate_half_width", payload.get("rotate_half_width", False)
                    )
                ),
            )
            if forms.has_alternate(line.text[first:last]):
                dx, dy = 0.0, 0.0
            yield QRectF(
                line.x + dx - step * 0.45,
                baseline + dy - metrics.ascent() * 0.75,
                max(1.0, step * 0.9),
                max(1.0, line.steps[first] * 0.9),
            )
