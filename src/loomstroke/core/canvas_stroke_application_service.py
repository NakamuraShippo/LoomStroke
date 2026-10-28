"""Stroke application helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import QRect

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.commands import EraseCommand, PaintCommand, StampBrushCommand
from loomstroke.core.placed_raster_stroke import apply_placed_stroke
from loomstroke.core.rgb_eraser_coverage import is_standard_zero_rgb_erase


class CanvasStrokeApplicationService:
    """Coordinate paint / erase / stamp application without submit-side effects."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    # Keep target, selection and no-op guards before source mutation.
    def apply_erase(self, cmd: EraseCommand) -> QRect | None:  # noqa: PLR0911
        """Apply erase command to a layer. Returns dirty rect or None."""
        owner = self._owner
        layer = owner._resolve_erase_target(cmd)
        if layer is None:
            return None

        points = owner._stroke_points_from_command(cmd)
        if not points:
            return None

        bounds = owner._compute_stroke_bounds(points, cmd.brush)
        if bounds is None:
            return None

        clipped_bounds = owner._apply_selection_clip(bounds)
        if clipped_bounds is None:
            return None

        if layer.transform != LayerTransform.identity() and not owner._mask_edit_flag:
            return apply_placed_stroke(owner, layer, cmd, clipped_bounds, erase=True)
        if is_standard_zero_rgb_erase(owner, cmd.brush):
            return clipped_bounds
        owner._ensure_layer_image_bounds(layer, clipped_bounds)
        offset_x, offset_y = owner._layer_offset(layer)
        ok = owner._apply_erase_into_layer(
            layer=layer,
            points=points,
            brush=cmd.brush,
            bounds=clipped_bounds,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        return clipped_bounds if ok else None

    def apply_paint(self, cmd: PaintCommand) -> QRect | None:
        """Apply paint command to active layer. Returns dirty rect or None."""
        owner = self._owner
        brush = cmd.brush
        layer = owner._resolve_paint_target()
        if layer is None:
            return None

        points = owner._stroke_points_from_command(cmd)
        if not points:
            return None

        color = owner._paint_color_from_brush(brush)
        bounds = owner._compute_stroke_bounds(points, brush)
        if bounds is None:
            return None

        clipped_bounds = owner._apply_selection_clip(bounds)
        if clipped_bounds is None:
            return None

        if layer.transform != LayerTransform.identity() and not owner._mask_edit_flag:
            return apply_placed_stroke(owner, layer, cmd, clipped_bounds)
        owner._ensure_layer_image_bounds(layer, clipped_bounds)
        offset_x, offset_y = owner._layer_offset(layer)
        ok = owner._apply_paint_into_layer(
            layer=layer,
            points=points,
            brush=brush,
            color=color,
            bounds=clipped_bounds,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        return clipped_bounds if ok else None

    def apply_stamp_brush(self, cmd: StampBrushCommand) -> QRect | None:
        """Apply stamp brush command to active layer. Returns dirty rect or None."""
        return cast(QRect | None, self._owner._apply_stamp_brush(cmd))
