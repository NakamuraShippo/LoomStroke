"""Blend mode helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.commands import BlendMode

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasBlendService:
    """Stateless blend mode math used by canvas compositing helpers."""

    QPAINTER_BLEND_MODES: ClassVar[frozenset[BlendMode]] = frozenset(
        {
            BlendMode.PASS_THROUGH,
            BlendMode.NORMAL,
        }
    )

    @staticmethod
    def get_composition_mode(blend_mode: BlendMode) -> QPainter.CompositionMode:
        """Convert BlendMode enum to QPainter CompositionMode."""
        mode_map = {
            BlendMode.PASS_THROUGH: QPainter.CompositionMode.CompositionMode_SourceOver,
            BlendMode.NORMAL: QPainter.CompositionMode.CompositionMode_SourceOver,
            BlendMode.DARKEN: QPainter.CompositionMode.CompositionMode_Darken,
            BlendMode.MULTIPLY: QPainter.CompositionMode.CompositionMode_Multiply,
            BlendMode.COLOR_BURN: QPainter.CompositionMode.CompositionMode_ColorBurn,
            BlendMode.LIGHTEN: QPainter.CompositionMode.CompositionMode_Lighten,
            BlendMode.SCREEN: QPainter.CompositionMode.CompositionMode_Screen,
            BlendMode.COLOR_DODGE: QPainter.CompositionMode.CompositionMode_ColorDodge,
            BlendMode.LINEAR_DODGE: QPainter.CompositionMode.CompositionMode_Plus,
            BlendMode.OVERLAY: QPainter.CompositionMode.CompositionMode_Overlay,
            BlendMode.SOFT_LIGHT: QPainter.CompositionMode.CompositionMode_SoftLight,
            BlendMode.HARD_LIGHT: QPainter.CompositionMode.CompositionMode_HardLight,
            BlendMode.DIFFERENCE: QPainter.CompositionMode.CompositionMode_Difference,
            BlendMode.EXCLUSION: QPainter.CompositionMode.CompositionMode_Exclusion,
            # Modes without QPainter built-in fall back to SourceOver.
            # Dissolve, Hard Mix and HSL component modes require pixel-level
            # math; accurate rendering is done by the GPU compositor.
        }

        return mode_map.get(
            blend_mode,
            QPainter.CompositionMode.CompositionMode_SourceOver,
        )

    @staticmethod
    def needs_custom_blend(blend_mode: BlendMode) -> bool:
        """Return True for blend modes that require pixel-level compositing."""
        return blend_mode not in CanvasBlendService.QPAINTER_BLEND_MODES

    @staticmethod
    def normalize_blend_mode(mode: str | BlendMode) -> BlendMode:
        """Normalize a string or enum value into a BlendMode."""
        if isinstance(mode, BlendMode):
            return mode
        normalized = str(mode).strip().lower().replace(" ", "_").replace("-", "_")
        try:
            return BlendMode(normalized)
        except ValueError:
            candidate = normalized.upper()
            if candidate in BlendMode.__members__:
                return BlendMode[candidate]
        logger.warning("Unknown blend mode %s; defaulting to normal", mode)
        return BlendMode.NORMAL

    @staticmethod
    def rgb_to_hsl(rgb: NDArray[Any]) -> NDArray[Any]:
        """Convert (h, w, 3) float32 [0,255] RGB to HSL [h:0-6, s:0-1, l:0-1]."""
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        lum = (mx + mn) / 510.0
        d = mx - mn
        achromatic = d < 0.5

        denom_lo = np.maximum(mx + mn, 0.001)
        denom_hi = np.maximum(510.0 - mx - mn, 0.001)
        s = np.where(lum > 0.5, d / denom_hi, d / denom_lo)
        s[achromatic] = 0.0

        h = np.zeros_like(lum)
        d_safe = np.where(achromatic, 1.0, d)
        mask_r = (mx == r) & ~achromatic
        mask_g = (mx == g) & ~achromatic & ~mask_r
        mask_b = ~achromatic & ~mask_r & ~mask_g
        h[mask_r] = ((g[mask_r] - b[mask_r]) / d_safe[mask_r]) % 6.0
        h[mask_g] = (b[mask_g] - r[mask_g]) / d_safe[mask_g] + 2.0
        h[mask_b] = (r[mask_b] - g[mask_b]) / d_safe[mask_b] + 4.0
        return np.stack([h, s, lum], axis=-1)

    @staticmethod
    def hsl_to_rgb(hsl: NDArray[Any]) -> NDArray[Any]:
        """Convert (h, w, 3) HSL [h:0-6, s:0-1, l:0-1] to float32 RGB [0,255]."""
        h, s, lum = hsl[..., 0], hsl[..., 1], hsl[..., 2]
        q = np.where(lum < 0.5, lum * (1.0 + s), lum + s - lum * s)
        p = 2.0 * lum - q
        out = np.empty_like(hsl)
        for i, offset in enumerate([2.0, 0.0, -2.0]):
            t = (h + offset) / 6.0
            t = t - np.floor(t)
            c = np.where(
                t < 1.0 / 6.0,
                p + (q - p) * 6.0 * t,
                np.where(
                    t < 0.5,
                    q,
                    np.where(
                        t < 2.0 / 3.0,
                        p + (q - p) * (2.0 / 3.0 - t) * 6.0,
                        p,
                    ),
                ),
            )
            out[..., i] = np.where(s < 0.001, lum, c) * 255.0
        return out

    @staticmethod
    def custom_blend(  # noqa: PLR0912, PLR0915
        dst: QImage,
        src: QImage,
        blend_mode: BlendMode,
        opacity: float,
    ) -> None:
        """Composite *src* onto *dst* in-place using pixel math."""
        dw, dh = dst.width(), dst.height()
        sw, sh = src.width(), src.height()
        w = min(dw, sw)
        h = min(dh, sh)
        if w <= 0 or h <= 0:
            return

        fmt = QImage.Format.Format_ARGB32_Premultiplied
        src_c = src if src.format() == fmt else src.convertToFormat(fmt)
        if dst.format() != fmt:
            return

        src_bits = src_c.constBits()
        dst_bits = dst.bits()
        if src_bits is None or dst_bits is None:
            return
        src_arr = np.frombuffer(src_bits, dtype=np.uint8).reshape(sh, sw, 4)[:h, :w]
        dst_arr = np.frombuffer(dst_bits, dtype=np.uint8).reshape(dh, dw, 4)[:h, :w]

        if blend_mode == BlendMode.DISSOLVE:
            sa = src_arr[:, :, 3].astype(np.float32) * opacity / 255.0
            rng = np.random.random((h, w)).astype(np.float32)
            mask = rng < sa
            mask3 = mask[:, :, np.newaxis]
            src_a = src_arr[:, :, 3:4].astype(np.float32).clip(1, 255)
            straight = (src_arr[:, :, :3].astype(np.float32) / src_a * 255.0).clip(0, 255)
            dst_arr[:, :, :3] = np.where(mask3, straight.astype(np.uint8), dst_arr[:, :, :3])
            dst_arr[:, :, 3] = np.where(mask, 255, dst_arr[:, :, 3])
            return

        src_a_f = src_arr[:, :, 3:4].astype(np.float32).clip(1, 255)
        dst_a_f = dst_arr[:, :, 3:4].astype(np.float32).clip(1, 255)
        s_rgb = src_arr[:, :, :3].astype(np.float32) / src_a_f * 255.0
        d_rgb = dst_arr[:, :, :3].astype(np.float32) / dst_a_f * 255.0

        blended = CanvasBlendService.blended_rgb_straight(s_rgb, d_rgb, blend_mode)
        if blended is None:
            return

        a = (src_arr[:, :, 3:4].astype(np.float32) / 255.0) * opacity
        dst_rgb_pm = dst_arr[:, :, :3].astype(np.float32)
        blended_n = blended / 255.0
        dst_arr[:, :, :3] = (
            (blended_n * a * 255.0 + dst_rgb_pm * (1.0 - a)).clip(0, 255).astype(np.uint8)
        )
        sa_f = src_arr[:, :, 3].astype(np.float32) * opacity / 255.0
        da_f = dst_arr[:, :, 3].astype(np.float32) / 255.0
        out_alpha = sa_f + da_f * (1.0 - sa_f)
        dst_arr[:, :, 3] = (out_alpha * 255.0).clip(0, 255).astype(np.uint8)

    @staticmethod
    def blended_rgb_straight(  # noqa: PLR0912, PLR0915, PLR0911
        s_rgb: NDArray[Any],
        d_rgb: NDArray[Any],
        blend_mode: BlendMode,
    ) -> NDArray[Any] | None:
        """Blend-mode color math on straight-alpha RGB float arrays [0, 255].

        The single CPU source of truth for blend-mode color values, shared by
        the over composite (:meth:`custom_blend`) and the clipping-run atop
        composite (:meth:`custom_blend_atop`). Returns ``None`` for modes
        without pixel math (the caller decides the fallback).
        """

        if blend_mode in (BlendMode.NORMAL, BlendMode.PASS_THROUGH):
            blended = s_rgb
        elif blend_mode == BlendMode.DARKEN:
            blended = np.minimum(s_rgb, d_rgb)
        elif blend_mode == BlendMode.MULTIPLY:
            blended = s_rgb * d_rgb / 255.0
        elif blend_mode == BlendMode.COLOR_BURN:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = np.clip(1.0 - (1.0 - d_n) / np.maximum(s_n, 0.001), 0, 1) * 255.0
        elif blend_mode == BlendMode.LIGHTEN:
            blended = np.maximum(s_rgb, d_rgb)
        elif blend_mode == BlendMode.SCREEN:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = (1.0 - (1.0 - s_n) * (1.0 - d_n)) * 255.0
        elif blend_mode == BlendMode.COLOR_DODGE:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = np.clip(d_n / np.maximum(1.0 - s_n, 0.001), 0, 1) * 255.0
        elif blend_mode == BlendMode.LINEAR_DODGE:
            blended = np.clip(s_rgb + d_rgb, 0, 255)
        elif blend_mode == BlendMode.OVERLAY:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = (
                np.where(
                    d_n < 0.5,
                    2.0 * s_n * d_n,
                    1.0 - 2.0 * (1.0 - s_n) * (1.0 - d_n),
                )
                * 255.0
            )
        elif blend_mode == BlendMode.SOFT_LIGHT:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            t = 2.0 * s_n
            dark = d_n * (t + d_n * (1.0 - t))
            light = np.sqrt(np.maximum(d_n, 0.0)) * (t - 1.0) + 2.0 * d_n * (1.0 - s_n)
            blended = np.where(s_n < 0.5, dark, light) * 255.0
        elif blend_mode == BlendMode.HARD_LIGHT:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = (
                np.where(
                    s_n < 0.5,
                    2.0 * s_n * d_n,
                    1.0 - 2.0 * (1.0 - s_n) * (1.0 - d_n),
                )
                * 255.0
            )
        elif blend_mode == BlendMode.DIFFERENCE:
            blended = np.abs(s_rgb - d_rgb)
        elif blend_mode == BlendMode.EXCLUSION:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            blended = (s_n + d_n - 2.0 * s_n * d_n) * 255.0
        elif blend_mode == BlendMode.HARD_MIX:
            blended = np.where((s_rgb + d_rgb) >= 255.0, 255.0, 0.0)
        elif blend_mode == BlendMode.LINEAR_BURN:
            blended = np.clip(s_rgb + d_rgb - 255.0, 0.0, 255.0)
        elif blend_mode == BlendMode.DARKER_COLOR:
            ls = np.sum(s_rgb * [0.114, 0.587, 0.299], axis=-1, keepdims=True)
            ld = np.sum(d_rgb * [0.114, 0.587, 0.299], axis=-1, keepdims=True)
            blended = np.where(ls < ld, s_rgb, d_rgb)
        elif blend_mode == BlendMode.LIGHTER_COLOR:
            ls = np.sum(s_rgb * [0.114, 0.587, 0.299], axis=-1, keepdims=True)
            ld = np.sum(d_rgb * [0.114, 0.587, 0.299], axis=-1, keepdims=True)
            blended = np.where(ls > ld, s_rgb, d_rgb)
        elif blend_mode == BlendMode.VIVID_LIGHT:
            s_n = s_rgb / 255.0
            d_n = d_rgb / 255.0
            burn = np.clip(1.0 - (1.0 - d_n) / np.maximum(2.0 * s_n, 0.001), 0, 1)
            dodge = np.clip(d_n / np.maximum(2.0 * (1.0 - s_n), 0.001), 0, 1)
            blended = np.where(s_n <= 0.5, burn, dodge) * 255.0
        elif blend_mode == BlendMode.LINEAR_LIGHT:
            blended = np.clip(d_rgb + 2.0 * s_rgb - 255.0, 0.0, 255.0)
        elif blend_mode == BlendMode.PIN_LIGHT:
            s2 = 2.0 * s_rgb
            blended = np.where(s_rgb <= 127.5, np.minimum(d_rgb, s2), np.maximum(d_rgb, s2 - 255.0))
        elif blend_mode == BlendMode.SUBTRACT:
            blended = np.clip(d_rgb - s_rgb, 0.0, 255.0)
        elif blend_mode == BlendMode.DIVIDE:
            blended = np.clip(d_rgb / np.maximum(s_rgb, 0.004) * 255.0, 0.0, 255.0)
        elif blend_mode in (
            BlendMode.HUE,
            BlendMode.SATURATION,
            BlendMode.COLOR,
            BlendMode.LUMINOSITY,
        ):
            s_hsl = CanvasBlendService.rgb_to_hsl(s_rgb)
            d_hsl = CanvasBlendService.rgb_to_hsl(d_rgb)
            if blend_mode == BlendMode.HUE:
                out_hsl = np.stack([s_hsl[..., 0], d_hsl[..., 1], d_hsl[..., 2]], axis=-1)
            elif blend_mode == BlendMode.SATURATION:
                out_hsl = np.stack([d_hsl[..., 0], s_hsl[..., 1], d_hsl[..., 2]], axis=-1)
            elif blend_mode == BlendMode.COLOR:
                out_hsl = np.stack([s_hsl[..., 0], s_hsl[..., 1], d_hsl[..., 2]], axis=-1)
            else:
                out_hsl = np.stack([d_hsl[..., 0], d_hsl[..., 1], s_hsl[..., 2]], axis=-1)
            blended = CanvasBlendService.hsl_to_rgb(out_hsl)
        else:
            return None
        return blended

    @staticmethod
    def custom_blend_atop(
        dst: QImage,
        src: QImage,
        blend_mode: BlendMode,
        opacity: float,
    ) -> None:
        """Composite *src* atop *dst* in-place with blend-mode color math.

        The clipping-run member composite: the blend mode only affects the
        color; the coverage stays the destination's alpha (QPainter
        ``CompositionMode_SourceAtop`` semantics, wgpu ``atop_enabled``
        parity):

            out_rgb_pm = blended x src_a x dst_a + dst_rgb_pm x (1 - src_a)
            out_a      = dst_a

        Modes without pixel math (and dissolve, whose random scatter cannot
        honour partial destination coverage) fall back to normal.
        """

        dw, dh = dst.width(), dst.height()
        sw, sh = src.width(), src.height()
        w = min(dw, sw)
        h = min(dh, sh)
        if w <= 0 or h <= 0:
            return

        fmt = QImage.Format.Format_ARGB32_Premultiplied
        src_c = src if src.format() == fmt else src.convertToFormat(fmt)
        if dst.format() != fmt:
            return

        src_bits = src_c.constBits()
        dst_bits = dst.bits()
        if src_bits is None or dst_bits is None:
            return
        src_arr = np.frombuffer(src_bits, dtype=np.uint8).reshape(sh, sw, 4)[:h, :w]
        dst_arr = np.frombuffer(dst_bits, dtype=np.uint8).reshape(dh, dw, 4)[:h, :w]

        src_a_f = src_arr[:, :, 3:4].astype(np.float32).clip(1, 255)
        dst_a_f = dst_arr[:, :, 3:4].astype(np.float32).clip(1, 255)
        s_rgb = src_arr[:, :, :3].astype(np.float32) / src_a_f * 255.0
        d_rgb = dst_arr[:, :, :3].astype(np.float32) / dst_a_f * 255.0

        blended = CanvasBlendService.blended_rgb_straight(s_rgb, d_rgb, blend_mode)
        if blended is None:
            blended = s_rgb

        a = (src_arr[:, :, 3:4].astype(np.float32) / 255.0) * opacity
        da = dst_arr[:, :, 3:4].astype(np.float32) / 255.0
        dst_rgb_pm = dst_arr[:, :, :3].astype(np.float32)
        blended_n = blended / 255.0
        dst_arr[:, :, :3] = (
            (blended_n * a * da * 255.0 + dst_rgb_pm * (1.0 - a)).clip(0, 255).astype(np.uint8)
        )
        # Destination alpha is intentionally untouched: clipping coverage.
