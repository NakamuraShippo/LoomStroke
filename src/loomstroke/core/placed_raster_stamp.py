"""Sample document-sized brush tips over the original placed raster pixels."""

from __future__ import annotations

import numpy as np

from .brush_dab_compositor import BrushDabCompositor
from .channels.channel_pixel_policy import ChannelPixelPolicy
from .commands import _load_tip_mask, _tone_color_for_pressure, _tone_params_from_brush
from .dab_mask_transform import DabMaskTransform
from .dab_variation import DabVariation
from .paper_texture_mask import PaperTextureMask
from .pen_taper_model import point_width_scale
from .pressure_stroke_point import point_opacity_scale, point_tone_pressure


def _sample_tip(mask, x, y):
    left, top = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    dx, dy = x - left, y - top
    result = np.zeros(x.shape, dtype=np.float32)
    for sx, sy, weight in (
        (0, 0, (1 - dx) * (1 - dy)),
        (1, 0, dx * (1 - dy)),
        (0, 1, (1 - dx) * dy),
        (1, 1, dx * dy),
    ):
        ix, iy = left + sx, top + sy
        valid = (ix >= 0) & (iy >= 0) & (ix < mask.shape[1]) & (iy < mask.shape[0])
        result[valid] += mask[iy[valid], ix[valid]] * weight[valid]
    return result


def stamp_placed_patch(command, geometry, rect, before):
    pixels = ChannelPixelPolicy.pixels(before)
    x, y = geometry.document_grid(rect)
    base_color, tone_range, tone_gamma, white_level = _tone_params_from_brush(
        command.brush, white_level=255.0
    )
    cache = {}
    brush = command.brush
    for point in command.points:
        if point_width_scale(point) <= 0:
            continue
        size = DabVariation.size(brush, point)
        mask = cache.get(size)
        if mask is None:
            mask = _load_tip_mask(command.tip_path, size, brush.hardness, command.hardness_mode)
            if getattr(brush, "round_tip", False):
                yy, xx = np.mgrid[:size, :size]
                mask = mask * (
                    ((xx + 0.5 - size / 2) ** 2 + (yy + 0.5 - size / 2) ** 2) <= (size / 2) ** 2
                )
            cache[size] = mask
        mask = DabMaskTransform.rotate(mask, getattr(point, "tip_angle", 0.0))
        ox, oy = round(point.x - mask.shape[1] / 2), round(point.y - mask.shape[0] / 2)
        alpha = _sample_tip(mask, x - ox - 0.5, y - oy - 0.5)
        alpha *= brush.opacity * brush.flow * point_opacity_scale(point)
        if getattr(brush, "texture_strength", 0):
            left, top = int(np.floor(x.min())), int(np.floor(y.min()))
            texture = PaperTextureMask.sample(
                left,
                top,
                int(np.ceil(x.max())) - left + 1,
                int(np.ceil(y.max())) - top + 1,
                scale=brush.texture_scale,
                strength=brush.texture_strength,
                seed=brush.variation_seed,
            )
            alpha *= texture[np.floor(y).astype(int) - top, np.floor(x).astype(int) - left]
        color = getattr(point, "dab_color", None)
        pigment = np.asarray(color, dtype=np.float32) if color is not None else base_color
        tone = _tone_color_for_pressure(
            pigment, tone_range, tone_gamma, white_level, point_tone_pressure(point)
        )[[2, 1, 0]]
        if hasattr(brush, "color_mixing"):
            BrushDabCompositor.apply(pixels, alpha, tone, brush.color_mixing)
        else:
            pixels[:, :, :3] = np.clip(
                pixels[:, :, :3] * (1 - alpha[:, :, None]) + tone * alpha[:, :, None], 0, 255
            )
            pixels[:, :, 3] = np.minimum(255, pixels[:, :, 3] + alpha * 255)
    return ChannelPixelPolicy.image(pixels)
