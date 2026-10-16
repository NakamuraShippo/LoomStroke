"""Stamp brush application helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.brush_dab_compositor import BrushDabCompositor
from loomstroke.core.commands import (
    StampBrushCommand,
    _load_tip_mask,
    _qimage_to_numpy_view,
    _tone_color_for_pressure,
    _tone_params_from_brush,
)
from loomstroke.core.dab_mask_transform import DabMaskTransform
from loomstroke.core.dab_variation import DabVariation
from loomstroke.core.paper_texture_mask import PaperTextureMask
from loomstroke.core.pen_taper_model import point_width_scale
from loomstroke.core.pressure_stroke_point import point_opacity_scale, point_tone_pressure


class CanvasStrokeStampBrushService:
    """Apply stamp brush strokes and per-point stamp pixels."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def apply_stamp_brush(  # noqa: PLR0911 - reject distinct empty/invalid edits before painting
        self, cmd: StampBrushCommand
    ) -> QRect | None:
        """Apply stamp brush command to active layer. Returns dirty rect or None."""
        owner = self._owner
        layer = owner._stamp_target_layer(cmd)
        if layer is None:
            return None

        points = owner._stamp_points(cmd)
        if not points:
            return None

        bounds, selection_alpha = owner._stamp_bounds_and_selection_alpha(points, cmd.brush)
        if bounds is None:
            return None

        from .command_types import LayerTransform  # noqa: PLC0415
        from .placed_raster_stroke import apply_placed_stroke  # noqa: PLC0415

        if layer.transform != LayerTransform.identity() and not owner._mask_edit_flag:
            return apply_placed_stroke(owner, layer, cmd, bounds)
        owner._ensure_layer_image_bounds(layer, bounds)
        offset_x, offset_y = owner._layer_offset(layer)
        local_bounds = QRect(bounds)
        local_bounds.translate(-offset_x, -offset_y)

        deferred = owner._txn_try_defer_paint(layer, bounds, offset_x, offset_y)

        if not deferred:
            assert layer.image is not None
            before_patch = layer.image.copy(local_bounds)

        saved_alpha = owner._save_alpha(layer, local_bounds)
        from .comic_panels.stroke_upload import (  # noqa: PLC0415
            begin_blank_upload,
            finish_blank_upload,
        )

        initial_blank = begin_blank_upload(layer)
        channels = owner.channels() if hasattr(owner, "channels") else None
        channel_before = channels.capture(layer, local_bounds) if channels else None

        assert layer.image is not None
        if layer.image.format() != QImage.Format.Format_ARGB32_Premultiplied:
            layer.image = layer.image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        view = _qimage_to_numpy_view(layer.image)
        if view is None:
            return None

        base_color, tone_range, tone_gamma, white_level = _tone_params_from_brush(
            cmd.brush,
            white_level=255.0,
        )
        opacity = max(0.0, min(1.0, float(cmd.brush.opacity)))
        flow = max(0.0, min(1.0, float(cmd.brush.flow)))
        alpha_scale = opacity * flow
        if alpha_scale <= 0.0:
            return None

        alpha_cache: dict[int, NDArray[Any]] = {}
        tone_params = (base_color, tone_range, tone_gamma, white_level)
        for point in points:
            self.apply_stamp_point(
                view=view,
                bounds=bounds,
                point=point,
                brush=cmd.brush,
                tip_path=cmd.tip_path,
                tone_params=tone_params,
                alpha_scale=alpha_scale,
                alpha_cache=alpha_cache,
                selection_alpha=selection_alpha,
                layer_offset_x=offset_x,
                layer_offset_y=offset_y,
                hardness_mode=cmd.hardness_mode,
            )

        owner._restore_alpha(layer, local_bounds, saved_alpha)
        if channels:
            channels.finish(layer, local_bounds, channel_before)
        layer.texture_version += 1
        finish_blank_upload(layer, bounds, initial_blank)

        if not deferred:
            assert layer.image is not None
            after_patch = layer.image.copy(local_bounds)
            metadata: dict[str, Any] = {"command": "stamp_brush"}
            if getattr(owner, "_mask_edit_flag", False):
                metadata["mask_edit"] = True
            owner._record_history_patch(
                "Stamp Brush",
                owner._history_patch_from_images(
                    layer.id,
                    QRect(bounds),
                    before_patch,
                    after_patch,
                    metadata,
                ),
            )
        owner._emit_layer_content_changed(layer, bounds, {"command": "stamp_brush"})
        return QRect(bounds)

    def apply_stamp_point(
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
        hardness_mode: str | None = None,
    ) -> None:
        coords = self._owner._point_coordinates(point)
        if coords is None:
            return

        taper = point_width_scale(point)
        if taper <= 0.0:
            return
        stamp_size = DabVariation.size(brush, point)
        alpha_base = alpha_cache.get(stamp_size)
        if alpha_base is None:
            mask = _load_tip_mask(
                tip_path,
                stamp_size,
                float(getattr(brush, "hardness", 1.0)),
                (
                    hardness_mode
                    if hardness_mode is not None
                    else getattr(brush, "hardness_mode", "radial")
                ),
            )
            if mask.size == 0:
                return
            if getattr(brush, "round_tip", False):
                yy, xx = np.mgrid[0:stamp_size, 0:stamp_size]
                radius = stamp_size / 2
                mask = mask * (((xx + 0.5 - radius) ** 2 + (yy + 0.5 - radius) ** 2) <= radius**2)
            alpha_base = mask * alpha_scale
            alpha_cache[stamp_size] = alpha_base

        alpha_base = DabMaskTransform.rotate(alpha_base, getattr(point, "tip_angle", 0.0))
        origin_x = round(coords[0] - alpha_base.shape[1] / 2.0)
        origin_y = round(coords[1] - alpha_base.shape[0] / 2.0)
        x0 = max(origin_x, bounds.left())
        y0 = max(origin_y, bounds.top())
        x1 = min(origin_x + alpha_base.shape[1], bounds.right() + 1)
        y1 = min(origin_y + alpha_base.shape[0], bounds.bottom() + 1)
        if x0 >= x1 or y0 >= y1:
            return

        mx0 = x0 - origin_x
        my0 = y0 - origin_y
        mx1 = mx0 + (x1 - x0)
        my1 = my0 + (y1 - y0)

        mask_patch = alpha_base[my0:my1, mx0:mx1]
        alpha = mask_patch * point_opacity_scale(point)
        if selection_alpha is not None:
            sel_patch = selection_alpha[y0:y1, x0:x1]
            alpha = alpha * sel_patch
        if alpha.max() <= 0.0:
            return
        if getattr(brush, "texture_strength", 0.0):
            alpha = alpha * PaperTextureMask.sample(
                x0,
                y0,
                x1 - x0,
                y1 - y0,
                scale=brush.texture_scale,
                strength=brush.texture_strength,
                seed=brush.variation_seed,
            )

        base_color, tone_range, tone_gamma, white_level = tone_params
        tone_color = _tone_color_for_pressure(
            base_color,
            tone_range,
            tone_gamma,
            white_level,
            point_tone_pressure(point),
        )
        tone_bgr = tone_color[[2, 1, 0]]
        dab_color = getattr(point, "dab_color", None)
        if dab_color is not None:
            tone_bgr = _tone_color_for_pressure(
                np.array(dab_color, dtype=np.float32),
                tone_range,
                tone_gamma,
                white_level,
                point_tone_pressure(point),
            )[[2, 1, 0]]
        lx0 = x0 - layer_offset_x
        ly0 = y0 - layer_offset_y
        lx1 = x1 - layer_offset_x
        ly1 = y1 - layer_offset_y
        self._composite_dab(view[ly0:ly1, lx0:lx1], alpha, tone_bgr, brush)

    @staticmethod
    def _composite_dab(region_view, alpha, tone_bgr, brush) -> None:
        if hasattr(brush, "color_mixing"):
            BrushDabCompositor.apply(region_view, alpha, tone_bgr, brush.color_mixing)
            return
        region = region_view[:, :, :3].astype(np.float32, copy=False)
        alpha3 = alpha[..., None]
        blended = region * (1.0 - alpha3) + tone_bgr * alpha3
        region_view[:, :, :3] = np.clip(blended, 0.0, 255.0)
        alpha_channel = region_view[:, :, 3].astype(np.float32, copy=False)
        alpha_channel = np.minimum(255.0, alpha_channel + alpha * 255.0)
        region_view[:, :, 3] = alpha_channel
