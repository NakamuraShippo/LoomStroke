"""Gradient Map live-filter filter.

Maps pixel luminance to a user-defined colour gradient via a 256-entry
RGBA look-up table.  Both CPU and GPU paths use the same LUT so that
the composited result is identical regardless of the rendering backend.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema

logger = logging.getLogger(__name__)

# Default gradient stops: black → white (identity-like luminance map).
_DEFAULT_STOPS: list[list[Any]] = [
    [0.0, [0, 0, 0, 255]],
    [1.0, [255, 255, 255, 255]],
]

# Rec.601 luminance weights (matches GPU shader).
_LUM_R = 0.299
_LUM_G = 0.587
_LUM_B = 0.114


class GradientMapFilter(BaseFilter):
    """Map pixel luminance to a colour gradient."""

    def name(self) -> str:
        return "Gradient Map"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="gradient_stops",
                label="Gradient",
                param_type="gradient_stops",
                default=_DEFAULT_STOPS,
            ),
        ]

    # ------------------------------------------------------------------
    # CPU path
    # ------------------------------------------------------------------

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        stops = _parse_stops(params.get("gradient_stops", _DEFAULT_STOPS))

        src = source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        w, h = src.width(), src.height()
        if w == 0 or h == 0:
            return src.copy()

        byte_count = src.bytesPerLine() * h
        buf = src.bits()
        if hasattr(buf, "setsize"):
            buf.setsize(byte_count)
        arr = (
            np.frombuffer(buf, dtype=np.uint8, count=byte_count)
            .reshape(h, src.bytesPerLine())[:, : w * 4]
            .reshape(h, w, 4)
            .copy()
        )

        # Build 256x4 RGBA LUT (uint8)
        lut = _build_gradient_lut(stops)  # (256, 4)

        # BGRA layout: B=0 G=1 R=2 A=3
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0
        adjusted = arr.copy()

        # Un-premultiply to straight RGB
        straight = np.zeros((h, w, 3), dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight[:, :, ch],
                where=has_alpha,
            )
        straight = np.clip(straight, 0, 255)

        # Luminance (BGRA order → B*0.114 + G*0.587 + R*0.299)
        lum = straight[:, :, 0] * _LUM_B + straight[:, :, 1] * _LUM_G + straight[:, :, 2] * _LUM_R
        lum_idx = np.clip(lum, 0, 255).astype(np.uint8)

        # LUT lookup — result is in RGBA order; remap to BGRA for QImage.
        mapped = lut[lum_idx]  # (h, w, 4) RGBA
        # RGBA → BGRA: swap R(0) ↔ B(2)
        mapped[:, :, [0, 2]] = mapped[:, :, [2, 0]]

        # Re-premultiply with *original* alpha (gradient map changes colour,
        # not transparency).
        for ch in range(3):
            adjusted[:, :, ch] = np.where(
                has_alpha,
                (mapped[:, :, ch].astype(np.float32) * alpha / 255.0 + 0.5).astype(np.uint8),
                np.uint8(0),
            )
        # Alpha channel unchanged.

        if mask is not None:
            adjusted = self._apply_mask(arr, adjusted, mask, w, h)

        result = QImage(adjusted.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        return result.copy()

    # ------------------------------------------------------------------
    # Legacy shader hook (retired)
    # ------------------------------------------------------------------

    def gpu_shader_source(self) -> str | None:
        return None

    def gpu_uniforms(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        return {}

    # ------------------------------------------------------------------
    # Mask helper (same pattern as Levels/Curves)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_mask(
        original: NDArray[Any],
        adjusted: NDArray[Any],
        mask: QImage,
        w: int,
        h: int,
    ) -> NDArray[Any]:
        scaled_mask = mask.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        bc = scaled_mask.bytesPerLine() * scaled_mask.height()
        b = scaled_mask.bits()
        if hasattr(b, "setsize"):
            b.setsize(bc)
        mask_arr = (
            np.frombuffer(b, dtype=np.uint8, count=bc)
            .reshape(h, scaled_mask.bytesPerLine())[:, :w]
            .copy()
            .astype(np.float32)
            / 255.0
        )
        mask_3d = mask_arr[:, :, np.newaxis]
        blended = (
            original.astype(np.float32) * (1.0 - mask_3d) + adjusted.astype(np.float32) * mask_3d
        )
        return np.clip(blended, 0, 255).astype(np.uint8)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _parse_stops(
    raw: Any,
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Normalise gradient stops from various input formats.

    Accepts ``list[list[float, list[int]]]`` (JSON round-trip) or
    ``list[tuple[float, tuple[int, ...]]]`` (native Python).
    Returns a sorted list of ``(position, (R, G, B, A))`` tuples.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return [(0.0, (0, 0, 0, 255)), (1.0, (255, 255, 255, 255))]

    stops: list[tuple[float, tuple[int, int, int, int]]] = []
    for entry in raw:
        try:
            pos = float(entry[0])
            rgba = entry[1]
            r = int(rgba[0])
            g = int(rgba[1])
            b = int(rgba[2])
            a = int(rgba[3]) if len(rgba) > 3 else 255
            stops.append((pos, (r, g, b, a)))
        except (TypeError, IndexError, ValueError):
            continue

    if len(stops) < 2:
        return [(0.0, (0, 0, 0, 255)), (1.0, (255, 255, 255, 255))]

    stops.sort(key=lambda s: s[0])
    return stops


def _build_gradient_lut(
    stops: list[tuple[float, tuple[int, int, int, int]]],
) -> NDArray[Any]:
    """Build a 256x4 uint8 RGBA look-up table from gradient stops.

    Piecewise linear interpolation between adjacent stops.
    """
    lut = np.zeros((256, 4), dtype=np.uint8)
    positions = np.array([s[0] for s in stops], dtype=np.float64)
    colours = np.array([s[1] for s in stops], dtype=np.float64)  # (N, 4)

    x = np.linspace(0.0, 1.0, 256)
    for ch in range(4):
        lut[:, ch] = np.clip(np.interp(x, positions, colours[:, ch]), 0, 255).astype(np.uint8)

    return lut


GradientMapEffect = GradientMapFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
