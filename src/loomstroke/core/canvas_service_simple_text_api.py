"""Canvas service TextApi mixin."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)

from loomstroke.core.canvas_models import (
    _MaskShapeContour,
)
from loomstroke.core.vector_text_render_service import (
    HorizontalTextSelectionSpan,
    TextObjectRenderContext,
    TextSelectionRenderPolicy,
    VectorTextRenderService,
)
from loomstroke.host_api import LayerRef

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceTextApiBase(CanvasServiceAccessorsMixin):
        _vector_text_render_service: VectorTextRenderService

else:

    class _CanvasServiceTextApiBase:
        pass


class CanvasServiceTextApiMixin(_CanvasServiceTextApiBase):
    def _extract_text_payload(self, vec: Any) -> dict[str, Any] | None:
        return VectorTextRenderService.extract_text_payload(vec)

    def _draw_text_vector_object(  # noqa: PLR0912, PLR0915
        self,
        *,
        painter: QPainter,
        vec: Any,
        payload: Mapping[str, Any],
        selection: dict[str, Any] | None,
        layer_id: str,
    ) -> None:
        self._vector_text_render().draw_text_vector_object(
            painter=painter,
            vec=vec,
            payload=payload,
            selection=selection,
            layer_id=layer_id,
        )

    def _draw_horizontal_text_lines(  # noqa: PLR0912, PLR0915
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
        shape_path: QPainterPath | None = None,
        cursor_pos: int | None = None,
        cursor_color: QColor | None = None,
        selection: tuple[int, int] | None = None,
    ) -> None:
        self._vector_text_render_service.draw_horizontal_text_lines(
            painter,
            content=content,
            x=x,
            y=y,
            w=w,
            h=h,
            align=align,
            line_height=line_height,
            line_height_runs=line_height_runs,
            tracking=tracking,
            tracking_runs=tracking_runs,
            wrap_mode=wrap_mode,
            shape_path=shape_path,
            cursor_pos=cursor_pos,
            cursor_color=cursor_color,
            selection=selection,
        )

    def _draw_horizontal_text_shape_lines(  # noqa: PLR0912, PLR0915
        self,
        painter: QPainter,
        *,
        content: str,
        x: float,
        y: float,
        w: float,
        h: float,
        line_height: float,
        shape_path: QPainterPath | _MaskShapeContour,
        cursor_pos: int | None = None,
        cursor_color: QColor | None = None,
    ) -> None:
        self._vector_text_render_service.draw_horizontal_text_shape_lines(
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
        )

    def _shape_caret_position(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        *,
        content: str,
        cursor_pos: int,
        metrics: QFontMetricsF,
        x: float,
        y: float,
        w: float,
        h: float,
        line_height: float,
        shape_path: QPainterPath | _MaskShapeContour,
    ) -> tuple[float, float] | None:
        return VectorTextRenderService.shape_caret_position(
            content=content,
            cursor_pos=cursor_pos,
            metrics=metrics,
            x=x,
            y=y,
            w=w,
            h=h,
            line_height=line_height,
            shape_path=shape_path,
        )

    @staticmethod
    def _shape_segments_at_y(
        shape_path: QPainterPath | _MaskShapeContour,
        x0: float,
        x1: float,
        y: float,
    ) -> list[tuple[float, float]]:
        return VectorTextRenderService.shape_segments_at_y(shape_path, x0, x1, y)

    @staticmethod
    def _fit_shape_line_text(
        text: str,
        metrics: QFontMetricsF,
        max_width: float,
    ) -> tuple[str, int, bool]:
        return VectorTextRenderService.fit_shape_line_text(text, metrics, max_width)

    @staticmethod
    def _wrap_text_lines(content: str, metrics: QFontMetricsF, max_width: float) -> list[str]:
        return VectorTextRenderService.wrap_text_lines(content, metrics, max_width)

    def _draw_vertical_text_lines(  # noqa: PLR0912, PLR0915
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
    ) -> None:
        self._vector_text_render_service.draw_vertical_text_lines(
            painter,
            content=content,
            x=x,
            y=y,
            w=w,
            h=h,
            align=align,
            vertical_flow=vertical_flow,
            line_height=line_height,
            line_height_runs=line_height_runs,
            tracking=tracking,
            tracking_runs=tracking_runs,
            cursor_pos=cursor_pos,
            selection=selection,
            cursor_color=cursor_color,
            rotate_half_width=rotate_half_width,
        )

    def _draw_text_object_lines(
        self,
        painter: QPainter,
        context: TextObjectRenderContext,
        *,
        x: float,
        y: float,
        w: float,
        h: float,
        shape_path: QPainterPath | _MaskShapeContour | None = None,
    ) -> None:
        self._vector_text_render_service.draw_text_object_lines(
            painter,
            context,
            x=x,
            y=y,
            w=w,
            h=h,
            shape_path=shape_path,
        )

    def _draw_text_object_content(
        self,
        painter: QPainter,
        context: TextObjectRenderContext,
        *,
        shape_path_for_rect: (
            Callable[
                [float, float, float, float],
                QPainterPath | _MaskShapeContour | None,
            ]
            | None
        ) = None,
    ) -> None:
        self._vector_text_render_service.draw_text_object_content(
            painter,
            context,
            shape_path_for_rect=shape_path_for_rect,
        )

    @staticmethod
    def _font_supports_char(metrics: QFontMetricsF, ch: str) -> bool:
        return VectorTextRenderService.font_supports_char(metrics, ch)

    @staticmethod
    def _is_vertical_center_rotated_char(ch: str) -> bool:
        return VectorTextRenderService.is_vertical_center_rotated_char(ch)

    @staticmethod
    def _vertical_reference_center_offset(metrics: QFontMetricsF) -> tuple[float, float]:
        return VectorTextRenderService.vertical_reference_center_offset(metrics)

    @staticmethod
    def _draw_rotated_glyph_centered(
        painter: QPainter,
        *,
        glyph: str,
        angle_deg: float,
        center_x: float,
        center_y: float,
        metrics: QFontMetricsF,
    ) -> None:
        VectorTextRenderService.draw_rotated_glyph_centered(
            painter,
            glyph=glyph,
            angle_deg=angle_deg,
            center_x=center_x,
            center_y=center_y,
            metrics=metrics,
        )

    def _vertical_glyph_draw_spec(
        self,
        ch: str,
        *,
        metrics: QFontMetricsF,
        column_step: float,
        char_step: float,
        vertical_flow: str,
        rotate_half_width: bool = False,
    ) -> tuple[str, float, float, float]:
        return VectorTextRenderService.vertical_glyph_draw_spec(
            ch,
            metrics=metrics,
            column_step=column_step,
            char_step=char_step,
            vertical_flow=vertical_flow,
            rotate_half_width=rotate_half_width,
        )

    def _make_text_font(self, payload: Mapping[str, Any]) -> QFont:
        return VectorTextRenderService.make_text_font(payload)

    @staticmethod
    def _text_style_defaults(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return VectorTextRenderService.text_style_defaults(payload)

    @staticmethod
    def _text_paragraph_defaults(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return VectorTextRenderService.text_paragraph_defaults(payload)

    @staticmethod
    def _text_content_from_payload(payload: Mapping[str, Any]) -> str:
        return VectorTextRenderService.text_content_from_payload(payload)

    @staticmethod
    def _text_mode_from_payload(payload: Mapping[str, Any]) -> str:
        return VectorTextRenderService.text_mode_from_payload(payload)

    def _text_align_from_payload(self, payload: Mapping[str, Any]) -> str:
        return VectorTextRenderService.text_align_from_payload(payload)

    def _text_line_height_from_payload(self, payload: Mapping[str, Any]) -> float:
        return VectorTextRenderService.text_line_height_from_payload(payload)

    def _text_vertical_flow_from_payload(self, payload: Mapping[str, Any]) -> str:
        return VectorTextRenderService.text_vertical_flow_from_payload(payload)

    @staticmethod
    def _text_wrap_mode_from_payload(payload: Mapping[str, Any], *, text_mode: str) -> str:
        return VectorTextRenderService.text_wrap_mode_from_payload(payload, text_mode=text_mode)

    @staticmethod
    def _horizontal_text_line_offsets(
        content: str,
        lines: Sequence[str],
        wrap_mode: str,
    ) -> tuple[list[int], int]:
        return VectorTextRenderService.horizontal_text_line_offsets(content, lines, wrap_mode)

    @staticmethod
    def _horizontal_text_line_y_offsets(
        *,
        lines: Sequence[str],
        line_offsets: Sequence[int],
        final_offset: int,
        line_step: float,
        line_height_runs: Sequence[Mapping[str, Any]] | None,
        base_line_spacing: float,
        line_height: float,
    ) -> list[float]:
        return VectorTextRenderService.horizontal_text_line_y_offsets(
            lines=lines,
            line_offsets=line_offsets,
            final_offset=final_offset,
            line_step=line_step,
            line_height_runs=line_height_runs,
            base_line_spacing=base_line_spacing,
            line_height=line_height,
        )

    @staticmethod
    def _horizontal_text_selection_span(
        selection: tuple[int, int] | None,
        *,
        cursor_color: QColor | None,
        fallback_color: QColor,
    ) -> HorizontalTextSelectionSpan:
        return VectorTextRenderService.horizontal_text_selection_span(
            selection,
            cursor_color=cursor_color,
            fallback_color=fallback_color,
        )

    @staticmethod
    def _horizontal_text_tracking_at(
        global_index: int,
        *,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
    ) -> float:
        return VectorTextRenderService.horizontal_text_tracking_at(
            global_index,
            tracking=tracking,
            tracking_runs=tracking_runs,
        )

    @staticmethod
    def _horizontal_text_char_x_offsets(
        line_text: str,
        line_start_offset: int,
        *,
        metrics: QFontMetricsF,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
        extra: float = 0.0,
    ) -> list[float]:
        return VectorTextRenderService.horizontal_text_char_x_offsets(
            line_text,
            line_start_offset,
            metrics=metrics,
            tracking=tracking,
            tracking_runs=tracking_runs,
            extra=extra,
        )

    @staticmethod
    def _horizontal_text_total_width(
        line_text: str,
        line_start_offset: int,
        *,
        metrics: QFontMetricsF,
        tracking: float,
        tracking_runs: Sequence[Mapping[str, Any]] | None,
    ) -> float:
        return VectorTextRenderService.horizontal_text_total_width(
            line_text,
            line_start_offset,
            metrics=metrics,
            tracking=tracking,
            tracking_runs=tracking_runs,
        )

    @staticmethod
    def _horizontal_text_justify_extra(
        *,
        is_justify: bool,
        line_index: int,
        line_count: int,
        line_text: str,
        line_width: float,
        available_width: float,
    ) -> float:
        return VectorTextRenderService.horizontal_text_justify_extra(
            is_justify=is_justify,
            line_index=line_index,
            line_count=line_count,
            line_text=line_text,
            line_width=line_width,
            available_width=available_width,
        )

    @staticmethod
    def _horizontal_text_aligned_x(
        *,
        x: float,
        width: float,
        line_width: float,
        align: str,
    ) -> float:
        return VectorTextRenderService.horizontal_text_aligned_x(
            x=x,
            width=width,
            line_width=line_width,
            align=align,
        )

    @staticmethod
    def _horizontal_text_caret_line_position(
        cursor_pos: int,
        *,
        line_offsets: Sequence[int],
        lines: Sequence[str],
    ) -> tuple[int, int] | None:
        return VectorTextRenderService.horizontal_text_caret_line_position(
            cursor_pos,
            line_offsets=line_offsets,
            lines=lines,
        )

    @staticmethod
    def _text_inline_cursor_from_payload(payload: Mapping[str, Any]) -> int | None:
        return VectorTextRenderService.text_inline_cursor_from_payload(payload)

    @staticmethod
    def _text_inline_selection_from_payload(
        payload: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        return VectorTextRenderService.text_inline_selection_from_payload(payload)

    @staticmethod
    def _text_inline_cursor_for_content(
        payload: Mapping[str, Any],
        content_length: int,
    ) -> int | None:
        return VectorTextRenderService.text_inline_cursor_for_content(payload, content_length)

    @staticmethod
    def _text_inline_selection_for_content(
        payload: Mapping[str, Any],
        content_length: int,
    ) -> tuple[int, int] | None:
        return VectorTextRenderService.text_inline_selection_for_content(payload, content_length)

    @staticmethod
    def _text_selection_render_policy(
        *,
        selection: Mapping[str, Any] | None,
        object_id: Any,
        layer_id: str,
        vertical: bool,
        is_preview_object: bool,
    ) -> TextSelectionRenderPolicy:
        return VectorTextRenderService.text_selection_render_policy(
            selection=selection,
            object_id=object_id,
            layer_id=layer_id,
            vertical=vertical,
            is_preview_object=is_preview_object,
        )

    @staticmethod
    def _text_object_render_context(
        *,
        payload: Mapping[str, Any],
        bounds: tuple[float, float, float, float] | None,
        selection: Mapping[str, Any] | None,
        object_id: Any,
        layer_id: str,
        is_preview_object: bool,
    ) -> TextObjectRenderContext | None:
        return VectorTextRenderService.text_object_render_context(
            payload=payload,
            bounds=bounds,
            selection=selection,
            object_id=object_id,
            layer_id=layer_id,
            is_preview_object=is_preview_object,
        )

    def _text_shape_path_from_payload(  # noqa: PLR0912, PLR0915
        self,
        payload: Mapping[str, Any],
        *,
        x: float,
        y: float,
        w: float,
        h: float,
    ) -> QPainterPath | _MaskShapeContour | None:
        return VectorTextRenderService.text_shape_path_from_payload(
            payload,
            x=x,
            y=y,
            w=w,
            h=h,
            contour_factory=_MaskShapeContour,
        )

    @staticmethod
    def _resolve_shape_handle_point(
        *,
        raw_x: float,
        raw_y: float,
        anchor_x: float,
        anchor_y: float,
        frame_x: float,
        frame_y: float,
        frame_w: float,
        frame_h: float,
        handle_space: str,
    ) -> tuple[float, float]:
        return VectorTextRenderService.resolve_shape_handle_point(
            raw_x=raw_x,
            raw_y=raw_y,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            frame_x=frame_x,
            frame_y=frame_y,
            frame_w=frame_w,
            frame_h=frame_h,
            handle_space=handle_space,
        )

    @staticmethod
    def _parse_rgba(raw: Any) -> QColor:
        return VectorTextRenderService.parse_rgba(raw)

    @staticmethod
    def _parse_vector_style_color(raw: Any) -> QColor | None:
        return VectorTextRenderService.parse_vector_style_color(raw)

    @staticmethod
    def _vector_style_paint(style: Any) -> tuple[QPen, QBrush | Qt.BrushStyle]:
        return VectorTextRenderService.vector_style_paint(style)

    @staticmethod
    def _text_bbox_from_payload(
        payload: Mapping[str, Any],
    ) -> tuple[float, float, float, float] | None:
        return VectorTextRenderService.text_bbox_from_payload(payload)

    @staticmethod
    def _text_padding_from_payload(
        payload: Mapping[str, Any],
    ) -> tuple[float, float, float, float]:
        return VectorTextRenderService.text_padding_from_payload(payload)

    @staticmethod
    def _text_padded_rect(
        x: float,
        y: float,
        width: float,
        height: float,
        padding: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        return VectorTextRenderService.text_padded_rect(x, y, width, height, padding)

    @staticmethod
    def _text_rotation_from_payload(payload: Mapping[str, Any]) -> float:
        return VectorTextRenderService.text_rotation_from_payload(payload)

    @staticmethod
    def _text_tracking_from_payload(
        payload: Mapping[str, Any],
        style_defaults: Mapping[str, Any] | None = None,
    ) -> float:
        return VectorTextRenderService.text_tracking_from_payload(payload, style_defaults)

    @staticmethod
    def _text_range_runs_from_payload(
        payload: Mapping[str, Any],
        key: str,
    ) -> list[dict[str, Any]] | None:
        return VectorTextRenderService.text_range_runs_from_payload(payload, key)

    @staticmethod
    def _text_object_transform_overlay_bounds(
        *,
        selection_policy: TextSelectionRenderPolicy,
        selection: Mapping[str, Any] | None,
        fallback_bounds: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float] | None:
        return VectorTextRenderService.text_object_transform_overlay_bounds(
            selection_policy=selection_policy,
            selection=selection,
            fallback_bounds=fallback_bounds,
        )
