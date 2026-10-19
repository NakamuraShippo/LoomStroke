"""Reuse the existing brush geometry to produce coverage without colour conversion."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.commands import StampBrushCommand

from .tool_space import document_bounds, validate_coverage_size


class NativeCmykStrokeCoverage:
    @staticmethod
    def render(
        canvas: Any,
        command: Any,
        layer: Any,
        *,
        erase: bool = False,
        tips: dict[Any, Any] | None = None,
    ) -> tuple[QRect, NDArray[Any], NDArray[Any]] | None:
        points = (
            canvas._stamp_points(command)
            if isinstance(command, StampBrushCommand)
            else canvas._stroke_points_from_command(command)
        )
        if not points:
            return None
        if isinstance(command, StampBrushCommand):
            if any(
                getattr(point, "dab_color", None) not in (None, tuple(command.brush.color[:3]))
                for point in points
            ):
                return None
            points = [
                (
                    replace(point, dab_color=None)
                    if getattr(point, "dab_color", None) is not None
                    else point
                )
                for point in points
            ]
        bounds = (
            canvas._compute_stamp_bounds(points, command.brush)
            if isinstance(command, StampBrushCommand)
            else canvas._compute_stroke_bounds(points, command.brush)
        )
        if bounds is None:
            return None
        bounds = bounds.intersected(document_bounds(layer))
        selection, selection_bounds = canvas._selection_clip_state()
        if selection_bounds is not None:
            bounds = bounds.intersected(selection_bounds)
        if bounds.isEmpty():
            return None
        validate_coverage_size(bounds)
        mask = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        if isinstance(command, StampBrushCommand):
            view = np.zeros((bounds.height(), bounds.width(), 4), np.uint8)
            cache = {
                size: tip * command.brush.opacity * command.brush.flow
                for (path, hardness, size), tip in (tips or {}).items()
                if path == command.tip_path and hardness == command.brush.hardness
            }
            for point in points:
                canvas._stroke_stamp_brush().apply_stamp_point(
                    view=view,
                    bounds=bounds,
                    point=point,
                    brush=command.brush,
                    tip_path=command.tip_path,
                    tone_params=(
                        np.zeros(3),
                        command.brush.tone_range,
                        command.brush.tone_gamma,
                        255.0,
                    ),
                    alpha_scale=command.brush.opacity * command.brush.flow,
                    alpha_cache=cache,
                    selection_alpha=None,
                    layer_offset_x=bounds.x(),
                    layer_offset_y=bounds.y(),
                    hardness_mode=command.hardness_mode,
                )
            mask = ChannelPixelPolicy.image(view)
        else:
            painter = QPainter(mask)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if erase:
                canvas._draw_erase_stroke(
                    painter,
                    points,
                    max(1, int(command.brush.size)),
                    color=QColor("white"),
                    offset=bounds,
                )
            else:
                canvas._draw_paint_stroke(
                    painter, points, command.brush, QColor("black"), offset=bounds
                )
            painter.end()
        if selection is not None:
            canvas._apply_selection_mask_to_image(mask, selection.copy(bounds))
        return NativeCmykStrokeCoverage._coverage_result(mask, bounds)

    @staticmethod
    def _coverage_result(
        mask: QImage,
        bounds: QRect,
    ) -> tuple[QRect, NDArray[Any], NDArray[Any]] | None:
        neutral = ChannelPixelPolicy.pixels(mask, straight=True)
        coverage = neutral[:, :, 3].copy()
        rows = np.flatnonzero(np.any(coverage, axis=1))
        columns = np.flatnonzero(np.any(coverage, axis=0))
        if not len(rows) or not len(columns):
            return None
        x, y = int(columns[0]), int(rows[0])
        right, bottom = int(columns[-1]) + 1, int(rows[-1]) + 1
        bounds = QRect(bounds.x() + x, bounds.y() + y, right - x, bottom - y)
        return (
            bounds,
            coverage[y:bottom, x:right].copy(),
            (255 - neutral[y:bottom, x:right, 0]).copy(),
        )
