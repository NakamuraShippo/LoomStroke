"""Reuse the existing brush geometry to produce coverage without colour conversion."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.commands import StampBrushCommand


class NativeCmykStrokeCoverage:
    @staticmethod
    def render(canvas, command, layer, *, erase=False):
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
        bounds = bounds.intersected(
            QRect(layer.offset_x, layer.offset_y, layer.image.width(), layer.image.height())
        )
        if bounds.isEmpty():
            return None
        mask = QImage(bounds.size(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        if isinstance(command, StampBrushCommand):
            view = np.zeros((bounds.height(), bounds.width(), 4), np.uint8)
            cache = {}
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
        selection, _ = canvas._selection_clip_state()
        if selection is not None:
            canvas._apply_selection_mask_to_image(mask, selection.copy(bounds))
        neutral = ChannelPixelPolicy.pixels(mask, straight=True)
        coverage = neutral[:, :, 3].copy()
        return bounds, coverage, 255 - neutral[:, :, 0]
