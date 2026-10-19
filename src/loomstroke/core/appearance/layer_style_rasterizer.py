"""Independent RGB appearance baking/reference, with the fixed ten-style order.

Gaussian preprocesses content; outer decoration sits below it, overlays and
relief shade the content, and the outline sits above. Never mutates its source.
Live canvas rendering uses the GPU implementations.
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np

from .appearance_registry import AppearanceRegistry
from .extended_style_rasterizer import blur_image, clipped_color, gradient_overlay, relief
from .layer_style_stack import LayerStyleStack
from .live_styles import style_padding
from .style_blending import style_blend, style_over

__all__ = ["LayerStyleRasterizer"]


class LayerStyleRasterizer:
    MAX_RADIUS: ClassVar[int] = 64

    def __init__(self, registry: AppearanceRegistry | None = None) -> None:
        self._registry = registry or AppearanceRegistry(include_adjustments=False)

    def render(self, source: np.ndarray, stack: LayerStyleStack) -> np.ndarray:
        rgba = np.asarray(source)
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            raise ValueError("source must be RGBA")
        height, width = rgba.shape[:2]
        padding = style_padding(stack)
        base = (
            np.pad(rgba, ((padding, padding), (padding, padding), (0, 0))).astype(np.float32)
            / 255.0
        )
        for style in stack.render_order():
            if style.kind == "gaussian_blur":
                params, _ = self._registry.validate("style", style.kind, style.params)
                base = blur_image(
                    base, params["radius"], style.opacity * params["opacity"], style.blend_mode
                )
        below = np.zeros_like(base)
        composed = None
        overlaid = base.copy()
        for style in stack.render_order():
            params, _issues = self._registry.validate("style", style.kind, style.params)
            opacity = float(style.opacity) * float(params.get("opacity", 1.0))
            if style.kind == "drop_shadow":
                below = style_over(below, self._shadow(base, params, opacity), style.blend_mode)
            elif style.kind == "outer_glow":
                below = style_over(below, self._glow(base, params, opacity), style.blend_mode)
            elif style.kind == "color_overlay":
                overlaid = self._overlay(overlaid, params, opacity, style.blend_mode)
            elif style.kind == "stroke":
                composed = style_over(
                    composed if composed is not None else self._over(below, overlaid),
                    self._stroke(base, params, opacity),
                    style.blend_mode,
                )
            elif style.kind == "inner_shadow":
                shifted = self._shift(
                    base[..., 3], round(params["offset_x"]), round(params["offset_y"])
                )
                overlaid = clipped_color(
                    overlaid,
                    1 - self._blur(shifted, params["blur"]),
                    params["color"],
                    opacity,
                    style.blend_mode,
                )
            elif style.kind == "inner_glow":
                eroded = 1 - self._dilate(1 - base[..., 3], params["size"])
                mask = 1 - self._blur(eroded, max(1, params["size"] // 2))
                overlaid = clipped_color(overlaid, mask, params["color"], opacity, style.blend_mode)
            elif style.kind == "gradient_overlay":
                overlaid = gradient_overlay(
                    overlaid,
                    params,
                    opacity,
                    origin=(-padding, -padding),
                    size=(width, height),
                    mode=style.blend_mode,
                )
            elif style.kind in {"bevel_emboss", "three_d"}:
                overlaid = relief(
                    overlaid,
                    base,
                    params,
                    opacity,
                    three_d=style.kind == "three_d",
                    rasterizer=self,
                    mode=style.blend_mode,
                )
        result = composed if composed is not None else self._over(below, overlaid)
        result = result[padding : padding + height, padding : padding + width]
        return np.clip(np.rint(result * 255.0), 0, 255).astype(np.uint8)

    # -- styles ------------------------------------------------------------------------

    def _shadow(self, base: np.ndarray, params: dict[str, Any], opacity: float) -> np.ndarray:
        alpha = self._shift(base[..., 3], round(params["offset_x"]), round(params["offset_y"]))
        alpha = self._blur(alpha, int(params["blur"]))
        return self._colored(alpha * opacity, params["color"])

    def _glow(self, base: np.ndarray, params: dict[str, Any], opacity: float) -> np.ndarray:
        size = min(self.MAX_RADIUS, int(params["size"]))
        alpha = self._blur(self._dilate(base[..., 3], size), max(1, size // 2))
        return self._colored(alpha * opacity, params["color"])

    def _stroke(self, base: np.ndarray, params: dict[str, Any], opacity: float) -> np.ndarray:
        size = min(self.MAX_RADIUS, int(params["size"]))
        ring = np.clip(self._dilate(base[..., 3], size) - base[..., 3], 0.0, 1.0)
        return self._colored(ring * opacity, params["color"])

    @staticmethod
    def _overlay(
        image: np.ndarray, params: dict[str, Any], opacity: float, mode: str = "normal"
    ) -> np.ndarray:
        color = np.array(params["color"][:3], dtype=np.float32) / 255.0
        amount = opacity * (params["color"][3] / 255.0)
        out = image.copy()
        out[..., :3] = (
            image[..., :3] * (1.0 - amount)
            + style_blend(color[None, None, :], image[..., :3], mode) * amount
        )
        return out

    # -- pixel helpers -----------------------------------------------------------------

    @staticmethod
    def _colored(alpha: np.ndarray, color: list[int]) -> np.ndarray:
        rgb = np.array(color[:3], dtype=np.float32) / 255.0
        out = np.zeros((*alpha.shape, 4), dtype=np.float32)
        out[..., :3] = rgb[None, None, :]
        out[..., 3] = np.clip(alpha * (color[3] / 255.0), 0.0, 1.0)
        return out

    @staticmethod
    def _over(below: np.ndarray, above: np.ndarray) -> np.ndarray:
        a_above = above[..., 3:4]
        a_below = below[..., 3:4]
        out_alpha = a_above + a_below * (1.0 - a_above)
        safe = np.where(out_alpha > 1e-6, out_alpha, 1.0)
        rgb = (above[..., :3] * a_above + below[..., :3] * a_below * (1.0 - a_above)) / safe
        out = np.zeros_like(below)
        out[..., :3] = np.where(out_alpha > 1e-6, rgb, 0.0)
        out[..., 3:4] = out_alpha
        return out

    @staticmethod
    def _shift(alpha: np.ndarray, dx: int, dy: int) -> np.ndarray:
        out = np.zeros_like(alpha)
        h, w = alpha.shape
        sx0, dx0 = (0, dx) if dx >= 0 else (-dx, 0)
        sy0, dy0 = (0, dy) if dy >= 0 else (-dy, 0)
        width, height = w - abs(dx), h - abs(dy)
        if width > 0 and height > 0:
            out[dy0 : dy0 + height, dx0 : dx0 + width] = alpha[
                sy0 : sy0 + height, sx0 : sx0 + width
            ]
        return out

    @classmethod
    def _dilate(cls, alpha: np.ndarray, radius: int) -> np.ndarray:
        radius = max(0, min(cls.MAX_RADIUS, int(radius)))
        if radius == 0:
            return alpha.copy()
        out = alpha.copy()
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy > radius * radius or (dx == 0 and dy == 0):
                    continue
                np.maximum(out, cls._shift(alpha, dx, dy), out=out)
        return out

    @staticmethod
    def _blur(alpha: np.ndarray, radius: int) -> np.ndarray:
        radius = max(0, int(radius))
        if radius == 0:
            return alpha
        size = 2 * radius + 1
        padded = np.pad(alpha, ((radius, radius), (radius, radius)), mode="constant")
        csum = np.cumsum(padded, axis=0)
        csum = np.vstack([np.zeros((1, csum.shape[1]), dtype=csum.dtype), csum])
        vertical = (csum[size:, :] - csum[:-size, :]) / size
        csum2 = np.cumsum(vertical, axis=1)
        csum2 = np.hstack([np.zeros((csum2.shape[0], 1), dtype=csum2.dtype), csum2])
        return (csum2[:, size:] - csum2[:, :-size]) / size
