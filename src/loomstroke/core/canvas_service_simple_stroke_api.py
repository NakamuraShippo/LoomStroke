"""Canvas service StrokeApi mixin."""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
)

from loomstroke.core.canvas_alpha_preservation_service import CanvasAlphaPreservationService
from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.canvas_mask_stroke_service import CanvasMaskStrokeService
from loomstroke.core.canvas_stroke_draw_primitive_service import (
    CanvasStrokeDrawPrimitiveService,
)
from loomstroke.core.canvas_stroke_target_service import CanvasStrokeTargetService
from loomstroke.core.commands import (
    EraseCommand,
    PaintCommand,
    StampBrushCommand,
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

    class _CanvasServiceStrokeApiBase(CanvasServiceAccessorsMixin):
        _pen_lag_diag_count: int

else:

    class _CanvasServiceStrokeApiBase:
        pass


class CanvasServiceStrokeApiMixin(_CanvasServiceStrokeApiBase):
    @contextlib.contextmanager
    def _mask_edit_swap(self, layer: _Layer | None) -> Iterator[None]:
        """Temporarily swap image ↔ layer_mask when mask_edit_mode is active.

        After the paint operation, convert the mask to grayscale so that
        any brush colour produces a correct luminance value.
        """
        with self._mask_stroke().mask_edit_swap(layer):
            yield

    @staticmethod
    def _normalize_mask_to_grayscale(mask: QImage) -> None:
        """Convert mask pixels to grayscale in-place (BGRA byte order).

        Uses standard luminance: 0.299*R + 0.587*G + 0.114*B.
        This ensures any brush colour is mapped to the correct brightness.
        """
        CanvasMaskStrokeService.normalize_mask_to_grayscale(mask)

    def _draw_mask_stroke_preview(self, dirty: QRect, layer: _Layer) -> None:
        """Draw semi-transparent red overlay for mask stroke on composited."""
        self._mask_stroke().draw_mask_stroke_preview(dirty, layer)

    def _schedule_mask_commit(self) -> None:
        """Restart the mask commit debounce timer."""
        self._mask_stroke_commit().schedule_mask_commit()

    def _commit_mask_paint(self) -> None:
        """Apply full recompose after mask stroke ends."""
        self._mask_stroke_commit().commit_mask_paint()

    def _draw_paint_stroke(
        self,
        painter: QPainter,
        points: list[Any],
        brush: Any,
        color: QColor,
        *,
        offset: QRect | None = None,
    ) -> None:
        self._stroke_draw_primitives().draw_paint_stroke(
            painter,
            points,
            brush,
            color,
            offset=offset,
        )

    def _prepare_paint_stroke_profile(
        self,
        brush: Any,
        color: QColor,
    ) -> tuple[QPen, float, int, tuple[int, int, int], float, float]:
        return self._stroke_draw_primitives().prepare_paint_stroke_profile(brush, color)

    @staticmethod
    def _paint_tilt_pair(point: Any) -> tuple[float, float] | None:
        return CanvasStrokeDrawPrimitiveService.paint_tilt_pair(point)

    @staticmethod
    def _paint_segment_pressure(p1: Any, p2: Any) -> float:
        return CanvasStrokeDrawPrimitiveService.paint_segment_pressure(p1, p2)

    @staticmethod
    def _paint_point_pressure(point: Any) -> float:
        return CanvasStrokeDrawPrimitiveService.paint_point_pressure(point)

    @classmethod
    def _paint_tilt_scale(cls, p1: Any, p2: Any) -> tuple[float, float]:
        del cls
        return CanvasStrokeDrawPrimitiveService.paint_tilt_scale(p1, p2)

    @staticmethod
    def _paint_tone_color(
        *,
        base_rgb: tuple[int, int, int],
        pressure: float,
        tone_range: float,
        tone_gamma: float,
    ) -> QColor:
        return CanvasStrokeDrawPrimitiveService.paint_tone_color(
            base_rgb=base_rgb,
            pressure=pressure,
            tone_range=tone_range,
            tone_gamma=tone_gamma,
        )

    def _draw_paint_stroke_polyline(
        self,
        painter: QPainter,
        points: list[Any],
        pen: QPen,
        *,
        base_size: float,
        base_alpha: int,
        base_rgb: tuple[int, int, int],
        tone_range: float,
        tone_gamma: float,
    ) -> None:
        self._stroke_draw_primitives().draw_paint_stroke_polyline(
            painter,
            points,
            pen,
            base_size=base_size,
            base_alpha=base_alpha,
            base_rgb=base_rgb,
            tone_range=tone_range,
            tone_gamma=tone_gamma,
        )

    def _draw_paint_stroke_single_point(
        self,
        painter: QPainter,
        points: list[Any],
        pen: QPen,
        *,
        base_size: float,
        base_alpha: int,
        base_rgb: tuple[int, int, int],
        tone_range: float,
        tone_gamma: float,
    ) -> None:
        self._stroke_draw_primitives().draw_paint_stroke_single_point(
            painter,
            points,
            pen,
            base_size=base_size,
            base_alpha=base_alpha,
            base_rgb=base_rgb,
            tone_range=tone_range,
            tone_gamma=tone_gamma,
        )

    def _draw_erase_stroke(
        self,
        painter: QPainter,
        points: list[Any],
        base_size: int,
        *,
        color: QColor,
        offset: QRect | None = None,
    ) -> None:
        self._stroke_draw_primitives().draw_erase_stroke(
            painter,
            points,
            base_size,
            color=color,
            offset=offset,
        )

    def _apply_erase(self, cmd: EraseCommand) -> QRect | None:
        """Apply erase command to a layer. Returns dirty rect or None."""
        return self._stroke_application().apply_erase(cmd)

    def _resolve_erase_target(self, cmd: EraseCommand) -> _Layer | None:
        return cast("_Layer | None", self._stroke_target().resolve_erase_target(cmd))

    @staticmethod
    def _stroke_points_from_command(cmd: object) -> list[Any]:
        return CanvasStrokeTargetService.stroke_points_from_command(cmd)

    def _apply_selection_clip(self, bounds: QRect) -> QRect | None:
        return self._stroke_target().apply_selection_clip(bounds)

    def _apply_erase_into_layer(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush: Any,
        bounds: QRect,
        offset_x: int,
        offset_y: int,
    ) -> bool:
        return self._stroke_pixel_commit().apply_erase_into_layer(
            layer=layer,
            points=points,
            brush=brush,
            bounds=bounds,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def _paint_erased_masked_stroke(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush_size: int,
        bounds: QRect,
        local_bounds: QRect,
    ) -> None:
        self._stroke_layer_paint().paint_erased_masked_stroke(
            layer=layer,
            points=points,
            brush_size=brush_size,
            bounds=bounds,
            local_bounds=local_bounds,
        )

    def _paint_erased_clear_stroke(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush_size: int,
        offset_x: int,
        offset_y: int,
    ) -> None:
        self._stroke_layer_paint().paint_erased_clear_stroke(
            layer=layer,
            points=points,
            brush_size=brush_size,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def _apply_paint(self, cmd: PaintCommand) -> QRect | None:
        """Apply paint command to active layer. Returns dirty rect or None."""
        return self._stroke_application().apply_paint(cmd)

    def _resolve_paint_target(self) -> _Layer | None:
        return cast("_Layer | None", self._stroke_target().resolve_paint_target())

    @staticmethod
    def _paint_color_from_brush(brush: Any) -> QColor:
        return CanvasStrokeTargetService.paint_color_from_brush(brush)

    def _apply_paint_into_layer(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush: Any,
        color: QColor,
        bounds: QRect,
        offset_x: int,
        offset_y: int,
    ) -> bool:
        return self._stroke_pixel_commit().apply_paint_into_layer(
            layer=layer,
            points=points,
            brush=brush,
            color=color,
            bounds=bounds,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def _paint_into_selection(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush: Any,
        color: QColor,
        bounds: QRect,
        local_bounds: QRect,
    ) -> None:
        self._stroke_layer_paint().paint_into_selection(
            layer=layer,
            points=points,
            brush=brush,
            color=color,
            bounds=bounds,
            local_bounds=local_bounds,
        )

    def _save_alpha(self, layer: _Layer, local_bounds: QRect) -> NDArray[Any] | None:
        return self._alpha_preservation().save_alpha(layer, local_bounds)

    @staticmethod
    def _restore_alpha(layer: _Layer, local_bounds: QRect, saved: NDArray[Any] | None) -> None:
        CanvasAlphaPreservationService.restore_alpha(layer, local_bounds, saved)

    def _paint_into_layer(
        self,
        *,
        layer: _Layer,
        points: list[Any],
        brush: Any,
        color: QColor,
        offset_x: int,
        offset_y: int,
    ) -> None:
        self._stroke_layer_paint().paint_into_layer(
            layer=layer,
            points=points,
            brush=brush,
            color=color,
            offset_x=offset_x,
            offset_y=offset_y,
        )

    def _apply_stamp_brush(self, cmd: StampBrushCommand) -> QRect | None:
        return self._stroke_stamp_brush().apply_stamp_brush(cmd)

    def _stamp_target_layer(self, cmd: StampBrushCommand) -> _Layer | None:
        return cast("_Layer | None", self._stroke_target().stamp_target_layer(cmd))

    def _stamp_points(self, cmd: StampBrushCommand) -> list[Any]:
        return self._stroke_target().stamp_points(cmd)

    def _stamp_bounds_and_selection_alpha(
        self,
        points: list[Any],
        brush: Any,
    ) -> tuple[QRect | None, NDArray[Any] | None]:
        return self._stroke_target().stamp_bounds_and_selection_alpha(points, brush)

    def _apply_stamp_point(
        self,
        *,
        view: NDArray[Any],
        bounds: QRect,
        point: Any,
        brush: Any,
        tip_path: str,
        tone_params: tuple[NDArray[Any], float, float, float],
        alpha_scale: float,
        alpha_cache: dict[int, NDArray[Any]],
        selection_alpha: NDArray[Any] | None,
        layer_offset_x: int = 0,
        layer_offset_y: int = 0,
    ) -> None:
        self._stroke_stamp_brush().apply_stamp_point(
            view=view,
            bounds=bounds,
            point=point,
            brush=brush,
            tip_path=tip_path,
            tone_params=tone_params,
            alpha_scale=alpha_scale,
            alpha_cache=alpha_cache,
            selection_alpha=selection_alpha,
            layer_offset_x=layer_offset_x,
            layer_offset_y=layer_offset_y,
        )

    def _compute_stroke_bounds(self, points: Sequence[Any], brush: Any) -> QRect | None:
        return self._stroke_target().compute_stroke_bounds(points, brush)

    def _compute_stamp_bounds(self, points: Sequence[Any], brush: Any) -> QRect | None:
        return self._stroke_target().compute_stamp_bounds(points, brush)

    def _point_coordinates(self, point: Any) -> tuple[float, float] | None:
        return self._stroke_target().point_coordinates(point)

    def _clamp_rect(self, rect: QRect) -> QRect | None:
        return self._stroke_target().clamp_rect(rect)

    def _log_pen_lag_diag(self, stage: str, start: float, rect: QRect | None = None) -> None:
        if not _PEN_LAG_DIAG or start <= 0.0:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        count = getattr(self, "_pen_lag_diag_count", 0) + 1
        self._pen_lag_diag_count = count
        if elapsed_ms < 8.0 and count % 60 != 0:
            return
        rect_text = ""
        if rect is not None:
            rect_text = f" rect={rect.x()},{rect.y()},{rect.width()}x{rect.height()}"
        logger.warning(
            "pen-lag diag: canvas stage=%s count=%d elapsed=%.2fms%s",
            stage,
            count,
            elapsed_ms,
            rect_text,
        )
