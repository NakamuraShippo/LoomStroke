"""Shared style mode names and independent RGB baking color math."""

from __future__ import annotations

import numpy as np

from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.commands import BlendMode

STYLE_BLEND_MODES = (
    "normal",
    "darken",
    "multiply",
    "color_burn",
    "linear_burn",
    "darker_color",
    "lighten",
    "screen",
    "color_dodge",
    "linear_dodge",
    "lighter_color",
    "overlay",
    "soft_light",
    "hard_light",
    "vivid_light",
    "linear_light",
    "pin_light",
    "hard_mix",
    "difference",
    "exclusion",
    "subtract",
    "divide",
)


def style_blend(source: np.ndarray, destination: np.ndarray, mode: str = "normal") -> np.ndarray:
    if mode not in STYLE_BLEND_MODES or mode == "normal":
        return source
    if mode == "divide":
        return np.asarray(np.clip(destination / np.maximum(source, 0.001), 0, 1))
    # The canvas CPU helper receives QImage's BGR order; these arrays are RGB.
    value = CanvasBlendService.blended_rgb_straight(
        source[..., ::-1] * 255, destination[..., ::-1] * 255, BlendMode(mode)
    )
    return source if value is None else np.asarray(value[..., ::-1] / 255)


def style_over(below: np.ndarray, above: np.ndarray, mode: str = "normal") -> np.ndarray:
    sa, da = above[..., 3:4], below[..., 3:4]
    a = sa + da * (1 - sa)
    color = style_blend(above[..., :3], below[..., :3], mode)
    premul = above[..., :3] * sa * (1 - da) + below[..., :3] * da * (1 - sa) + color * sa * da
    return np.concatenate((premul / np.maximum(a, 1e-6), a), axis=-1)
