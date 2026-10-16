"""Painter primitives for vector text and vector overlays."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)

from .japanese_text.typography import has_typography
from .text_edit_geometry import TextEditGeometry
from .text_line_geometry import horizontal_line_geometry, vertical_line_geometry
from .text_shape_geometry import shape_line_geometry
from .vector_text_layout import TextLayoutCalculator
from .vector_text_selection import (
    SelectionRenderEngine,
    TextSelectionRenderPolicy,
)
from .vector_variable_width_renderer import VariableWidthPathRenderer
from .vertical_font_forms import VerticalFontForms

logger = logging.getLogger(__name__)


_VARIABLE_WIDTH = VariableWidthPathRenderer()


@dataclass(frozen=True)
class TextObjectRenderContext:
    content: str
    bounds: tuple[float, float, float, float]
    draw_rect: tuple[float, float, float, float]
    font: QFont
    color: QColor
    align: str
    vertical: bool
    rotate_half_width: bool
    vertical_flow: str
    line_height: float
    tracking: float
    tracking_runs: list[dict[str, Any]] | None
    line_height_runs: list[dict[str, Any]] | None
    padding: tuple[float, float, float, float]
    wrap_mode: str
    inline_cursor_pos: int | None
    inline_selection: tuple[int, int] | None
    inline_cursor_color: QColor
    selection_policy: TextSelectionRenderPolicy
    rotation_deg: float
    typography_payload: Mapping[str, Any] | None = None


class TextRenderPrimitives(TextLayoutCalculator, SelectionRenderEngine):
    """QPainter rendering primitives for vector and text objects."""

    _canvas: Any

    def draw_vector_layer(
        self,
        painter: QPainter,
        layer: Any,
        selection: dict[str, Any] | None,
        preview_obj: Any | None,
        preview_layer_id: str | None,
    ) -> None:
        """Draw a vector layer including optional preview object."""
        objects = list(layer.vector_objects)
        if preview_obj and (preview_layer_id is None or preview_layer_id == layer.id):
            preview_id = getattr(preview_obj, "id", None)
            objects = [obj for obj in objects if getattr(obj, "id", None) != preview_id]
            objects.append(preview_obj)
        if not objects:
            return
        self.draw_vector_objects(painter, layer.id, objects, selection)

    def draw_vector_objects(  # noqa: PLR0912, PLR0915
        self,
        painter: QPainter,
        layer_id: str,
        objects: Sequence[Any],
        selection: dict[str, Any] | None,
    ) -> None:
        """Render vector objects with optional selection highlighting."""
        canvas = self._canvas

        for vec in objects:
            text_payload = canvas._extract_text_payload(vec)
            if text_payload is not None:
                if not canvas._is_vector_preview_object(vec) and "inline_preview" in text_payload:
                    # Stored objects can contain old editing chrome. Keep the
                    # explicit single-object drawing API usable for previews.
                    text_payload = dict(text_payload)
                    text_payload.pop("inline_preview", None)
                # Apply canvas-flip mirror transform around the text frame
                # center so text glyphs appear mirrored after Image > Flip.
                vf_h = bool(text_payload.get("visual_flip_h", False))
                vf_v = bool(text_payload.get("visual_flip_v", False))
                mirror_active = False
                if vf_h or vf_v:
                    bbox = canvas._compute_vector_bbox(vec)
                    if bbox:
                        mx = (bbox[0] + bbox[2]) / 2.0
                        my = (bbox[1] + bbox[3]) / 2.0
                        painter.save()
                        painter.translate(mx, my)
                        painter.scale(-1.0 if vf_h else 1.0, -1.0 if vf_v else 1.0)
                        painter.translate(-mx, -my)
                        mirror_active = True
                self.draw_text_vector_object(
                    painter=painter,
                    vec=vec,
                    payload=text_payload,
                    selection=selection,
                    layer_id=layer_id,
                )
                if mirror_active:
                    painter.restore()
                continue

            style = getattr(vec, "style", None)
            pen, brush = canvas._vector_style_paint(style)
            painter.setPen(pen)
            painter.setBrush(brush)

            cached_qpaths = canvas._get_cached_qpaths(vec)
            # CT-15: a variable width profile renders as a filled outline.
            profile = _VARIABLE_WIDTH.profile_for(vec)
            if profile is not None:
                _VARIABLE_WIDTH.draw(painter, vec, cached_qpaths, pen, brush, profile)
                continue
            for qpath in cached_qpaths:
                if not qpath.isEmpty():
                    painter.drawPath(qpath)
        # Selection/editing UI (anchors, handles, transform bbox) is drawn
        # by the screen-space overlay (ui/canvas/vector_selection_overlay.py,
        # spec §15) so raster output stays selection-independent.

    def draw_text_vector_object(
        self,
        *,
        painter: QPainter,
        vec: Any,
        payload: Mapping[str, Any],
        selection: dict[str, Any] | None,
        layer_id: str,
    ) -> None:
        """Draw a text vector object and its transform overlay."""

        canvas = self._canvas
        bounds = canvas._compute_vector_bbox(vec)
        if not bounds:
            bounds = canvas._text_bbox_from_payload(payload)
        if not bounds:
            return

        is_preview_object = canvas._is_vector_preview_object(vec)
        context = canvas._text_object_render_context(
            payload=payload,
            bounds=bounds,
            selection=selection,
            object_id=getattr(vec, "id", ""),
            layer_id=layer_id,
            is_preview_object=is_preview_object,
        )
        if context is None:
            return

        def shape_path_for_rect(
            x: float,
            y: float,
            w: float,
            h: float,
        ) -> Any | None:
            return canvas._text_shape_path_from_payload(payload, x=x, y=y, w=w, h=h)

        canvas._draw_text_object_content(
            painter,
            context,
            shape_path_for_rect=shape_path_for_rect,
        )
        # Transform overlay is drawn by the screen-space overlay (spec §15).

    def draw_horizontal_text_lines(  # noqa: PLR0912, PLR0915
        self,
        painter: QPainter,
        *,
        content: str,
        x: float,
        y: float,
        w: float,
        h: float,
        align: str,
        line_height: float,
        line_height_runs: list[dict[str, Any]] | None = None,
        tracking: float = 0.0,
        tracking_runs: list[dict[str, Any]] | None = None,
        wrap_mode: str = "none",
        shape_path: Any | None = None,
        cursor_pos: int | None = None,
        cursor_color: QColor | None = None,
        selection: tuple[int, int] | None = None,
    ) -> None:
        if wrap_mode == "shape" and shape_path is not None:
            self.draw_horizontal_text_shape_lines(
                painter,
                content=content,
                x=x,
                y=y,
                w=w,
                h=h,
                line_height=line_height,
                shape_path=shape_path,
                cursor_pos=cursor_pos,
                cursor_color=cursor_color,
                selection=selection,
            )
            return

        # When per-char tracking is active, reset font letter spacing to avoid
        # double-counting (we handle all spacing manually in char-by-char mode).
        if tracking_runs:
            adjusted_font = QFont(painter.font())
            adjusted_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
            painter.setFont(adjusted_font)
        metrics = QFontMetricsF(painter.font())
        lines = horizontal_line_geometry(
            content,
            metrics,
            font=painter.font(),
            x=x,
            y=y,
            w=w,
            align=align,
            line_height=line_height,
            tracking=tracking,
            tracking_runs=tracking_runs,
            line_height_runs=line_height_runs,
            wrap_mode=wrap_mode,
            cursor_offsets=cursor_pos is not None or selection is not None,
        )
        span = VectorTextRenderService.horizontal_text_selection_span(
            selection,
            cursor_color=cursor_color,
            fallback_color=painter.pen().color(),
        )
        for line in lines:
            if line.baseline > y + h - 2.0:
                break
            if span.color is not None and span.start < line.end and span.end > line.start:
                s = max(0, span.start - line.start)
                e = min(len(line.text), span.end - line.start)
                sx, ex = line.x + line.offsets[s], line.x + line.offsets[e]
                painter.fillRect(
                    QRectF(
                        sx,
                        line.baseline - metrics.ascent(),
                        ex - sx,
                        metrics.ascent() + max(1.0, metrics.descent()),
                    ),
                    span.color,
                )
            if tracking_runs or line.justify_extra > 0:
                for ci, ch in enumerate(line.text or " "):
                    painter.drawText(QPointF(line.x + line.offsets[ci], line.baseline), ch)
            else:
                painter.drawText(QPointF(line.x, line.baseline), line.text or " ")
        if cursor_pos is not None:
            geometry = TextEditGeometry(
                content,
                lines,
                metrics,
                QTransform(),
                QRectF(x, y, w, h),
                False,
                0.0,
            )
            rect = geometry.caret_rect(cursor_pos)
            color = QColor(cursor_color) if cursor_color is not None else painter.pen().color()
            if color.alpha() <= 0:
                color.setAlpha(255)
            painter.save()
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(rect.topLeft(), QPointF(rect.left(), rect.bottom()))
            painter.restore()

    def draw_horizontal_text_shape_lines(  # noqa: PLR0912, PLR0915
        self,
        painter: QPainter,
        *,
        content: str,
        x: float,
        y: float,
        w: float,
        h: float,
        line_height: float,
        shape_path: Any,
        cursor_pos: int | None = None,
        cursor_color: QColor | None = None,
        selection: tuple[int, int] | None = None,
    ) -> None:
        metrics = QFontMetricsF(painter.font())
        ascent = metrics.ascent()
        text = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = shape_line_geometry(
            text,
            metrics,
            font=painter.font(),
            x=x,
            y=y,
            w=w,
            h=h,
            line_height=line_height,
            shape_path=shape_path,
            cursor_offsets=selection is not None,
        )
        span = VectorTextRenderService.horizontal_text_selection_span(
            selection,
            cursor_color=cursor_color,
            fallback_color=painter.pen().color(),
        )
        for line in lines:
            if span.color is not None and span.start < line.end and span.end > line.start:
                start = max(0, span.start - line.start)
                end = min(len(line.text), span.end - line.start)
                painter.fillRect(
                    QRectF(
                        line.x + line.offsets[start],
                        line.baseline - ascent,
                        line.offsets[end] - line.offsets[start],
                        ascent + max(1.0, metrics.descent()),
                    ),
                    span.color,
                )
            if line.text:
                painter.drawText(QPointF(line.x, line.baseline), line.text)

        if cursor_pos is not None:
            caret = VectorTextRenderService.shape_caret_position(
                content=text,
                cursor_pos=cursor_pos,
                metrics=metrics,
                x=x,
                y=y,
                w=w,
                h=h,
                line_height=line_height,
                shape_path=shape_path,
            )
            if caret is not None:
                caret_x, caret_baseline = caret
                active_caret_color = (
                    QColor(cursor_color) if cursor_color is not None else painter.pen().color()
                )
                if active_caret_color.alpha() <= 0:
                    active_caret_color.setAlpha(255)
                caret_pen = QPen(active_caret_color, 1.0)
                top = caret_baseline - ascent
                bottom_y = min(y + h - 1.0, caret_baseline + max(1.0, metrics.descent()))
                painter.save()
                painter.setPen(caret_pen)
                painter.drawLine(QPointF(caret_x, top), QPointF(caret_x, bottom_y))
                painter.restore()

    @staticmethod
    def parse_rgba(raw: Any) -> QColor:
        if isinstance(raw, QColor):
            return QColor(raw)
        if isinstance(raw, (tuple, list)) and len(raw) >= 4:
            try:
                r = round(float(raw[0]))
                g = round(float(raw[1]))
                b = round(float(raw[2]))
                a = round(float(raw[3]))
            except (TypeError, ValueError):
                return QColor(0, 0, 0, 255)
            return QColor(
                max(0, min(255, r)),
                max(0, min(255, g)),
                max(0, min(255, b)),
                max(0, min(255, a)),
            )
        return QColor(0, 0, 0, 255)

    @staticmethod
    def parse_vector_style_color(raw: Any) -> QColor | None:  # noqa: PLR0911
        if raw is None:
            return None
        if isinstance(raw, QColor):
            return QColor(raw)
        if isinstance(raw, str):
            candidate = QColor(raw)
            return candidate if candidate.isValid() else None
        if isinstance(raw, (tuple, list)) and len(raw) >= 3:
            try:
                r_f, g_f, b_f = (float(raw[0]), float(raw[1]), float(raw[2]))
            except (TypeError, ValueError):
                return None
            # Type-based detection: all-int -> 0-255, all-float -> 0..1
            all_int = all(isinstance(c, int) for c in raw[:3])
            if not all_int and max(r_f, g_f, b_f) <= 1.0:
                return QColor.fromRgbF(r_f, g_f, b_f, 1.0)
            return QColor(int(r_f), int(g_f), int(b_f))
        return None

    @staticmethod
    def vector_style_paint(style: Any) -> tuple[QPen, QBrush | Qt.BrushStyle]:
        color = QColor(0, 0, 0)
        width = 2
        brush: QBrush | Qt.BrushStyle = Qt.BrushStyle.NoBrush
        if style:
            stroke_color = VectorTextRenderService.parse_vector_style_color(
                getattr(style, "stroke_color", None)
            )
            if stroke_color is not None:
                color = stroke_color
            width = max(1, int(getattr(style, "stroke_width", 2)))
            if getattr(style, "fill_enabled", False):
                fill_color = VectorTextRenderService.parse_vector_style_color(
                    getattr(style, "fill_color", None)
                )
                if fill_color is not None:
                    brush = QBrush(fill_color)
        pen = QPen(color)
        pen.setWidth(width)
        cap_map = {"butt": Qt.PenCapStyle.FlatCap, "square": Qt.PenCapStyle.SquareCap}
        join_map = {"miter": Qt.PenJoinStyle.MiterJoin, "bevel": Qt.PenJoinStyle.BevelJoin}
        cap_str = getattr(style, "cap", "round") if style else "round"
        join_str = getattr(style, "join", "round") if style else "round"
        pen.setCapStyle(cap_map.get(cap_str, Qt.PenCapStyle.RoundCap))
        pen.setJoinStyle(join_map.get(join_str, Qt.PenJoinStyle.RoundJoin))
        if style:
            miter = getattr(style, "miter_limit", None)
            if miter is not None:
                pen.setMiterLimit(float(miter))
            dash = getattr(style, "dash", None)
            if dash and len(dash) >= 2:
                pen.setDashPattern(list(dash))
        return (pen, brush)

    def draw_vertical_text_lines(  # noqa: PLR0912, PLR0915
        self,
        painter: QPainter,
        *,
        content: str,
        x: float,
        y: float,
        w: float,
        h: float,
        align: str,
        vertical_flow: str,
        line_height: float,
        line_height_runs: list[dict[str, Any]] | None = None,
        tracking: float = 0.0,
        tracking_runs: list[dict[str, Any]] | None = None,
        cursor_pos: int | None = None,
        selection: tuple[int, int] | None = None,
        cursor_color: QColor | None = None,
        rotate_half_width: bool = False,
        wrap_mode: str = "none",
    ) -> None:
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        metrics = QFontMetricsF(painter.font())
        ascent = metrics.ascent()
        column_step = max(metrics.height(), metrics.horizontalAdvance("M")) * line_height
        lines = vertical_line_geometry(
            normalized_content,
            metrics,
            x=x,
            y=y,
            w=w,
            h=h,
            align=align,
            vertical_flow=vertical_flow,
            line_height=line_height,
            tracking=tracking,
            tracking_runs=tracking_runs,
            line_height_runs=line_height_runs,
            wrap_mode=wrap_mode,
        )
        ref_dx, ref_dy = VectorTextRenderService.vertical_reference_center_offset(metrics)
        forms = VerticalFontForms(painter.font())
        selection_color = None
        if selection is not None and 0 <= selection[0] < selection[1] <= len(normalized_content):
            selection_color = (
                QColor(cursor_color) if cursor_color is not None else painter.pen().color()
            )
            if selection_color.alpha() <= 0:
                selection_color.setAlpha(255)
            selection_color.setAlpha(max(32, int(selection_color.alpha() * 0.35)))
        for line in lines:
            if line.x < x + 1.0 or line.x > x + w - 2.0:
                break
            for index, cluster_end in line.clusters or ((0, 1),):
                ch = (line.text or " ")[index:cluster_end]
                draw_y = line.baseline + line.offsets[index]
                if draw_y > y + h:
                    break
                char_step = line.steps[index]
                glyph, angle, dx, dy = VectorTextRenderService.vertical_glyph_draw_spec(
                    ch,
                    metrics=metrics,
                    column_step=column_step,
                    char_step=char_step,
                    vertical_flow=vertical_flow,
                    rotate_half_width=rotate_half_width,
                )
                needs_fallback = (glyph, angle, dx, dy) != (ch, 0.0, 0.0, 0.0)
                if not needs_fallback or forms.has_alternate(ch):
                    glyph, angle, dx, dy = ch, 0.0, 0.0, 0.0
                    painter.setFont(forms.font)
                else:
                    painter.setFont(forms.base_font)
                if (
                    selection_color is not None
                    and selection is not None
                    and selection[0] < line.start + cluster_end
                    and line.start + index < selection[1]
                ):
                    painter.fillRect(
                        QRectF(
                            line.x + dx - column_step * 0.45,
                            draw_y + dy - ascent * 0.75,
                            max(1.0, column_step * 0.9),
                            max(1.0, char_step * 0.9),
                        ),
                        selection_color,
                    )
                if abs(angle) <= 1e-6:
                    painter.drawText(QPointF(line.x + dx, draw_y + dy), glyph)
                else:
                    VectorTextRenderService.draw_rotated_glyph_centered(
                        painter,
                        glyph=glyph,
                        angle_deg=angle,
                        center_x=line.x + ref_dx + dx,
                        center_y=draw_y + ref_dy + dy,
                        metrics=metrics,
                    )
        painter.setFont(forms.base_font)
        if cursor_pos is not None:
            geometry = TextEditGeometry(
                normalized_content,
                lines,
                metrics,
                QTransform(),
                QRectF(x, y, w, h),
                True,
                column_step,
            )
            rect = geometry.caret_rect(cursor_pos)
            color = QColor(cursor_color) if cursor_color is not None else painter.pen().color()
            if color.alpha() <= 0:
                color.setAlpha(255)
            painter.save()
            painter.setPen(QPen(color, 1.0))
            painter.drawLine(rect.topLeft(), QPointF(rect.right(), rect.top()))
            painter.restore()

    def draw_text_object_lines(
        self,
        painter: QPainter,
        context: TextObjectRenderContext,
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        shape_path: Any | None = None,
    ) -> None:
        if context.vertical:
            self.draw_vertical_text_lines(
                painter,
                content=context.content,
                x=x,
                y=y,
                w=w,
                h=h,
                align=context.align,
                vertical_flow=context.vertical_flow,
                line_height=context.line_height,
                line_height_runs=context.line_height_runs,
                tracking=context.tracking,
                tracking_runs=context.tracking_runs,
                cursor_pos=context.inline_cursor_pos,
                selection=context.inline_selection,
                cursor_color=context.inline_cursor_color,
                rotate_half_width=context.rotate_half_width,
                wrap_mode=context.wrap_mode,
            )
            return
        if context.wrap_mode == "shape" and shape_path is not None:
            self.draw_horizontal_text_shape_lines(
                painter,
                content=context.content,
                x=x,
                y=y,
                w=w,
                h=h,
                line_height=context.line_height,
                shape_path=shape_path,
                cursor_pos=context.inline_cursor_pos,
                cursor_color=context.inline_cursor_color,
                selection=context.inline_selection,
            )
            return
        self.draw_horizontal_text_lines(
            painter,
            content=context.content,
            x=x,
            y=y,
            w=w,
            h=h,
            align=context.align,
            line_height=context.line_height,
            line_height_runs=context.line_height_runs,
            tracking=context.tracking,
            tracking_runs=context.tracking_runs,
            wrap_mode=context.wrap_mode,
            cursor_pos=context.inline_cursor_pos,
            cursor_color=context.inline_cursor_color,
            selection=context.inline_selection,
        )

    def draw_text_object_content(  # noqa: PLR0912
        self,
        painter: QPainter,
        context: TextObjectRenderContext,
        *,
        shape_path_for_rect: Callable[[float, float, float, float], Any | None] | None = None,
    ) -> None:
        if context.typography_payload is not None:
            from .japanese_text.typography_painter import (  # noqa: PLC0415
                draw_japanese_text_object,
            )

            draw_japanese_text_object(painter, context, shape_path_for_rect=shape_path_for_rect)
            return
        draw_min_x, draw_min_y, draw_max_x, draw_max_y = context.draw_rect
        draw_width = max(1.0, draw_max_x - draw_min_x)
        draw_height = max(1.0, draw_max_y - draw_min_y)
        shape_path_global = (
            shape_path_for_rect(draw_min_x, draw_min_y, draw_width, draw_height)
            if (
                shape_path_for_rect is not None
                and context.wrap_mode == "shape"
                and not context.vertical
            )
            else None
        )

        if not (context.content or context.inline_cursor_pos is not None):
            return
        if context.selection_policy.hide_source_text:
            return

        painter.save()
        painter.setPen(QPen(context.color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setFont(context.font)
        if abs(context.rotation_deg) > 1e-6:
            center_x = draw_min_x + (draw_width * 0.5)
            center_y = draw_min_y + (draw_height * 0.5)
            painter.translate(center_x, center_y)
            painter.rotate(context.rotation_deg)
            local_x = -draw_width * 0.5
            local_y = -draw_height * 0.5
            shape_path_local = None
            if shape_path_global is not None and shape_path_for_rect is not None:
                shape_path_local = shape_path_for_rect(local_x, local_y, draw_width, draw_height)
            if isinstance(shape_path_local, QPainterPath):
                painter.setClipPath(shape_path_local)
            elif shape_path_local is not None:
                # _MaskShapeContour-like shapes clip to the bounding rect; per-line
                # clipping is handled by shape_segments_at_y.
                painter.setClipRect(
                    round(local_x),
                    round(local_y),
                    round(draw_width),
                    round(draw_height),
                )
            else:
                painter.setClipRect(
                    round(local_x),
                    round(local_y),
                    round(draw_width),
                    round(draw_height),
                )
            pad_x, pad_y, pad_w, pad_h = VectorTextRenderService.text_padded_rect(
                local_x,
                local_y,
                draw_width,
                draw_height,
                context.padding,
            )
            self.draw_text_object_lines(
                painter,
                context,
                x=pad_x,
                y=pad_y,
                w=pad_w,
                h=pad_h,
                shape_path=shape_path_local,
            )
        else:
            if isinstance(shape_path_global, QPainterPath):
                painter.setClipPath(shape_path_global)
            else:
                painter.setClipRect(
                    round(draw_min_x),
                    round(draw_min_y),
                    round(draw_width),
                    round(draw_height),
                )
            gpad_x, gpad_y, gpad_w, gpad_h = VectorTextRenderService.text_padded_rect(
                draw_min_x,
                draw_min_y,
                draw_width,
                draw_height,
                context.padding,
            )
            self.draw_text_object_lines(
                painter,
                context,
                x=gpad_x,
                y=gpad_y,
                w=gpad_w,
                h=gpad_h,
                shape_path=shape_path_global,
            )
        painter.restore()

    @staticmethod
    def draw_rotated_glyph_centered(
        painter: QPainter,
        *,
        glyph: str,
        angle_deg: float,
        center_x: float,
        center_y: float,
        metrics: QFontMetricsF,
    ) -> None:
        bounds = metrics.tightBoundingRect(glyph if glyph else " ")
        if bounds is None:
            return
        center = bounds.center()
        origin_x = -float(center.x())
        origin_y = -float(center.y())
        painter.save()
        painter.translate(center_x, center_y)
        painter.rotate(angle_deg)
        painter.drawText(QPointF(origin_x, origin_y), glyph)
        painter.restore()

    @staticmethod
    def text_object_render_context(
        *,
        payload: Mapping[str, Any],
        bounds: tuple[float, float, float, float] | None,
        selection: Mapping[str, Any] | None,
        object_id: Any,
        layer_id: str,
        is_preview_object: bool,
    ) -> TextObjectRenderContext | None:
        if bounds is None:
            bounds = VectorTextRenderService.text_bbox_from_payload(payload)
        if bounds is None:
            return None

        content = VectorTextRenderService.text_content_from_payload(payload)
        font = VectorTextRenderService.make_text_font(payload)
        style_defaults = VectorTextRenderService.text_style_defaults(payload)
        color = VectorTextRenderService.parse_rgba(
            style_defaults.get("color_rgba", payload.get("color_rgba"))
        )
        align = VectorTextRenderService.text_align_from_payload(payload)
        if align not in {"left", "center", "right", "justify"}:
            align = "left"
        vertical = bool(style_defaults.get("vertical", payload.get("vertical", False)))
        rotate_half_width = bool(
            style_defaults.get("rotate_half_width", payload.get("rotate_half_width", False))
        )
        vertical_flow = VectorTextRenderService.text_vertical_flow_from_payload(payload)
        line_height = VectorTextRenderService.text_line_height_from_payload(payload)
        line_height = max(0.5, min(4.0, line_height))
        tracking = VectorTextRenderService.text_tracking_from_payload(payload, style_defaults)
        tracking_runs = VectorTextRenderService.text_range_runs_from_payload(
            payload,
            "tracking_runs",
        )
        line_height_runs = VectorTextRenderService.text_range_runs_from_payload(
            payload,
            "line_height_runs",
        )
        padding = VectorTextRenderService.text_padding_from_payload(payload)
        text_mode = VectorTextRenderService.text_mode_from_payload(payload)
        wrap_mode = VectorTextRenderService.text_wrap_mode_from_payload(
            payload,
            text_mode=text_mode,
        )
        inline_cursor_pos = VectorTextRenderService.text_inline_cursor_for_content(
            payload,
            len(content),
        )
        inline_selection = VectorTextRenderService.text_inline_selection_for_content(
            payload,
            len(content),
        )
        inline_cursor_color = QColor(color)
        if inline_cursor_color.alpha() <= 0:
            inline_cursor_color.setAlpha(255)
        selection_policy = VectorTextRenderService.text_selection_render_policy(
            selection=selection,
            object_id=object_id,
            layer_id=layer_id,
            vertical=vertical,
            is_preview_object=is_preview_object,
        )
        draw_rect = VectorTextRenderService.text_bbox_from_payload(payload) or bounds
        rotation_deg = VectorTextRenderService.text_rotation_from_payload(payload)
        return TextObjectRenderContext(
            content=content,
            bounds=bounds,
            draw_rect=draw_rect,
            font=font,
            color=color,
            align=align,
            vertical=vertical,
            rotate_half_width=rotate_half_width,
            vertical_flow=vertical_flow,
            line_height=line_height,
            tracking=tracking,
            tracking_runs=tracking_runs,
            line_height_runs=line_height_runs,
            padding=padding,
            wrap_mode=wrap_mode,
            inline_cursor_pos=inline_cursor_pos,
            inline_selection=inline_selection,
            inline_cursor_color=inline_cursor_color,
            selection_policy=selection_policy,
            rotation_deg=rotation_deg,
            typography_payload=payload if has_typography(payload) else None,
        )

    @staticmethod
    def build_vector_qpaths(vec: Any) -> list[QPainterPath]:
        """Build QPainterPath list from a VectorObject's paths."""
        result: list[QPainterPath] = []
        paths = getattr(vec, "paths", None) or []
        for path in paths:
            anchors = getattr(path, "anchors", None) or []
            qpath = QPainterPath()
            if not anchors:
                result.append(qpath)
                continue
            first = anchors[0]
            qpath.moveTo(float(first.pos[0]), float(first.pos[1]))
            if len(anchors) > 1:
                VectorTextRenderService._append_path_segments(qpath, anchors)
                if VectorTextRenderService._path_is_closed(path, anchors):
                    VectorTextRenderService._close_qpath(qpath, anchors)
            result.append(qpath)
        return result

    @staticmethod
    def path_bbox(vec: Any) -> tuple[float, float, float, float] | None:
        paths = getattr(vec, "paths", None) or []
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for path in paths:
            anchors = getattr(path, "anchors", None) or []
            for anchor in anchors:
                min_x = min(min_x, anchor.pos[0])
                min_y = min(min_y, anchor.pos[1])
                max_x = max(max_x, anchor.pos[0])
                max_y = max(max_y, anchor.pos[1])
                if getattr(anchor, "h_in", None):
                    hx = anchor.pos[0] + anchor.h_in[0]
                    hy = anchor.pos[1] + anchor.h_in[1]
                    min_x = min(min_x, hx)
                    min_y = min(min_y, hy)
                    max_x = max(max_x, hx)
                    max_y = max(max_y, hy)
                if getattr(anchor, "h_out", None):
                    hx = anchor.pos[0] + anchor.h_out[0]
                    hy = anchor.pos[1] + anchor.h_out[1]
                    min_x = min(min_x, hx)
                    min_y = min(min_y, hy)
                    max_x = max(max_x, hx)
                    max_y = max(max_y, hy)
        if min_x == float("inf"):
            return None
        return (min_x, min_y, max_x, max_y)

    @staticmethod
    def vector_bbox(vec: Any) -> tuple[float, float, float, float] | None:
        path_bounds = VectorTextRenderService.path_bbox(vec)
        if path_bounds is not None:
            return path_bounds
        text_payload = VectorTextRenderService.extract_text_payload(vec)
        if text_payload is not None:
            return VectorTextRenderService.text_bbox_from_payload(text_payload)
        return None

    @staticmethod
    def is_vector_preview_object(vec: Any, preview_obj: Any | None) -> bool:
        if preview_obj is vec:
            return True
        if preview_obj is not None:
            preview_id = getattr(preview_obj, "id", None)
            if preview_id is not None and preview_id == getattr(vec, "id", None):
                return True
        meta = getattr(vec, "meta", None)
        if isinstance(meta, dict) and bool(meta.get("preview")):
            return True
        return bool(getattr(vec, "preview", False))

    @staticmethod
    def is_parametric_shape_editable(vec: Any) -> bool:
        meta = getattr(vec, "meta", None)
        if not isinstance(meta, dict):
            return False
        payload = meta.get("shape_manager_parametric")
        if not isinstance(payload, dict):
            return False
        return bool(payload.get("editable", True))

    @staticmethod
    def should_draw_vector_path_controls(
        *,
        vec: Any,
        layer_id: str,
        path_idx: int,
        selection: Mapping[str, Any] | None,
        preview_obj: Any | None,
    ) -> bool:
        if VectorTextRenderService.is_parametric_shape_editable(vec):
            return False
        if VectorTextRenderService.is_vector_preview_object(vec, preview_obj):
            return True
        if not selection:
            return False
        selection_matches = (
            selection.get("layer_id") in (None, layer_id)
            and selection.get("id") == getattr(vec, "id", None)
            and not selection.get("shape_handles")
        )
        if not selection_matches:
            return False

        selected_path = selection.get("path")
        if isinstance(selected_path, int):
            return selected_path == path_idx
        return True

    @staticmethod
    def draw_vector_anchor_box_overlay(painter: QPainter, raw_bounds: Any) -> None:
        if not isinstance(raw_bounds, (tuple, list)) or len(raw_bounds) != 4:
            return
        try:
            min_x, min_y, max_x, max_y = (float(raw_bounds[i]) for i in range(4))
        except (TypeError, ValueError):
            return
        if max_x <= min_x or max_y <= min_y:
            return
        if max(abs(min_x), abs(min_y), abs(max_x), abs(max_y)) > 1_000_000:
            return
        color = QColor(20, 120, 255, 180)
        fill = QColor(20, 120, 255, 24)
        painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
        painter.setBrush(QBrush(fill))
        painter.drawRect(
            int(min_x),
            int(min_y),
            int(max_x - min_x),
            int(max_y - min_y),
        )

    @staticmethod
    def draw_vector_transform_overlay(
        painter: QPainter,
        bounds: tuple[float, float, float, float],
    ) -> None:
        min_x, min_y, max_x, max_y = bounds
        color = QColor(20, 120, 255)
        pen = QPen(color, 1, Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(
            int(min_x),
            int(min_y),
            int(max_x - min_x),
            int(max_y - min_y),
        )
        cx = (min_x + max_x) * 0.5
        cy = (min_y + max_y) * 0.5
        corner_size = 8
        edge_size = 7
        rotation_offset = 12
        painter.setBrush(QColor(255, 255, 255))
        corners = [
            (min_x, min_y),
            (max_x, min_y),
            (min_x, max_y),
            (max_x, max_y),
        ]
        for hx, hy in corners:
            painter.drawRect(
                int(hx - corner_size / 2),
                int(hy - corner_size / 2),
                corner_size,
                corner_size,
            )
        edges = [
            (cx, min_y),
            (cx, max_y),
            (min_x, cy),
            (max_x, cy),
        ]
        for hx, hy in edges:
            painter.drawEllipse(
                int(hx - edge_size / 2),
                int(hy - edge_size / 2),
                edge_size,
                edge_size,
            )
        rotation_x = cx
        rotation_y = min_y - rotation_offset
        painter.drawLine(int(cx), int(min_y), int(rotation_x), int(rotation_y))
        painter.drawEllipse(
            int(rotation_x - edge_size / 2),
            int(rotation_y - edge_size / 2),
            edge_size,
            edge_size,
        )

    @staticmethod
    def draw_vector_shape_handles(painter: QPainter, handles: Any) -> None:
        if not isinstance(handles, list):
            return
        for entry in handles:
            if not isinstance(entry, dict):
                continue
            try:
                x = float(cast("Any", entry.get("x")))
                y = float(cast("Any", entry.get("y")))
            except (TypeError, ValueError):
                continue
            # Guard against int overflow in QPainter draw calls
            if abs(x) > 1_000_000 or abs(y) > 1_000_000:
                continue
            active = bool(entry.get("active", False))
            kind = str(entry.get("kind", "circle"))
            raw_color = entry.get("color")
            if isinstance(raw_color, (list, tuple)) and len(raw_color) >= 3:
                r, g, b = int(raw_color[0]), int(raw_color[1]), int(raw_color[2])
                color = (
                    QColor(min(255, r + 40), min(255, g + 40), min(255, b + 40))
                    if active
                    else QColor(r, g, b)
                )
                border = QColor(max(0, r - 80), max(0, g - 80), max(0, b - 80))
            else:
                color = QColor(255, 140, 0) if active else QColor(255, 196, 72)
                border = QColor(130, 76, 0)
            painter.setPen(QPen(border, 1))
            painter.setBrush(color)
            size = 10 if active else 8
            half = size * 0.5
            if kind == "diamond":
                diamond = QPolygonF(
                    [
                        QPointF(x, y - half),
                        QPointF(x + half, y),
                        QPointF(x, y + half),
                        QPointF(x - half, y),
                    ]
                )
                painter.drawPolygon(diamond)
            else:
                painter.drawEllipse(int(x - half), int(y - half), size, size)

    @staticmethod
    def draw_vector_anchor_marker(painter: QPainter, anchor: Any, selected: Any) -> None:
        r = 4
        center_x = int(anchor.pos[0])
        center_y = int(anchor.pos[1])
        marker_color = QColor(20, 120, 255) if selected else QColor(255, 255, 255)
        border = QColor(20, 120, 255) if selected else QColor(50, 50, 50)
        painter.setBrush(marker_color)
        painter.setPen(QPen(border, 1))
        painter.drawEllipse(center_x - r, center_y - r, r * 2, r * 2)
        if anchor.h_in:
            painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DotLine))
            painter.drawLine(
                center_x,
                center_y,
                int(center_x + anchor.h_in[0]),
                int(center_y + anchor.h_in[1]),
            )
            painter.setPen(QPen(QColor(80, 80, 80), 1))
            painter.drawEllipse(
                int(center_x + anchor.h_in[0]) - 2,
                int(center_y + anchor.h_in[1]) - 2,
                4,
                4,
            )
        if anchor.h_out:
            painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DotLine))
            painter.drawLine(
                center_x,
                center_y,
                int(center_x + anchor.h_out[0]),
                int(center_y + anchor.h_out[1]),
            )
            painter.setPen(QPen(QColor(80, 80, 80), 1))
            painter.drawEllipse(
                int(center_x + anchor.h_out[0]) - 2,
                int(center_y + anchor.h_out[1]) - 2,
                4,
                4,
            )

    @staticmethod
    def _append_path_segments(qpath: QPainterPath, anchors: list[Any]) -> None:
        for index in range(len(anchors) - 1):
            anchor = anchors[index]
            next_anchor = anchors[index + 1]
            c1 = (
                anchor.pos[0] + (anchor.h_out[0] if anchor.h_out else 0.0),
                anchor.pos[1] + (anchor.h_out[1] if anchor.h_out else 0.0),
            )
            c2 = (
                next_anchor.pos[0] + (next_anchor.h_in[0] if next_anchor.h_in else 0.0),
                next_anchor.pos[1] + (next_anchor.h_in[1] if next_anchor.h_in else 0.0),
            )
            has_out = anchor.h_out and (abs(anchor.h_out[0]) > 1e-3 or abs(anchor.h_out[1]) > 1e-3)
            has_in = next_anchor.h_in and (
                abs(next_anchor.h_in[0]) > 1e-3 or abs(next_anchor.h_in[1]) > 1e-3
            )
            if has_out or has_in:
                qpath.cubicTo(
                    c1[0],
                    c1[1],
                    c2[0],
                    c2[1],
                    float(next_anchor.pos[0]),
                    float(next_anchor.pos[1]),
                )
            else:
                qpath.lineTo(float(next_anchor.pos[0]), float(next_anchor.pos[1]))

    @staticmethod
    def _path_is_closed(path: Any, anchors: list[Any]) -> bool:
        path_closed = bool(getattr(path, "closed", False))
        if path_closed or len(anchors) < 3:
            return path_closed
        first_pos = anchors[0].pos
        last_pos = anchors[-1].pos
        return bool((first_pos[0] - last_pos[0]) ** 2 + (first_pos[1] - last_pos[1]) ** 2 < 0.25)

    @staticmethod
    def _close_qpath(qpath: QPainterPath, anchors: list[Any]) -> None:
        last = anchors[-1]
        first = anchors[0]
        c1 = (
            last.pos[0] + (last.h_out[0] if last.h_out else 0.0),
            last.pos[1] + (last.h_out[1] if last.h_out else 0.0),
        )
        c2 = (
            first.pos[0] + (first.h_in[0] if first.h_in else 0.0),
            first.pos[1] + (first.h_in[1] if first.h_in else 0.0),
        )
        has_out = last.h_out and (abs(last.h_out[0]) > 1e-3 or abs(last.h_out[1]) > 1e-3)
        has_in = first.h_in and (abs(first.h_in[0]) > 1e-3 or abs(first.h_in[1]) > 1e-3)
        if has_out or has_in:
            qpath.cubicTo(
                c1[0],
                c1[1],
                c2[0],
                c2[1],
                float(first.pos[0]),
                float(first.pos[1]),
            )
        else:
            qpath.lineTo(float(first.pos[0]), float(first.pos[1]))
        qpath.closeSubpath()


class _VectorTextRenderPrimitiveProxy(TextRenderPrimitives):
    pass


VectorTextRenderService = _VectorTextRenderPrimitiveProxy

__all__ = (
    "TextObjectRenderContext",
    "TextRenderPrimitives",
)
