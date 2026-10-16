"""Levels adjustment live-filter filter."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema


class LevelsFilter(BaseFilter):
    """Levels adjustment (input black/white point, gamma, output range)."""

    def name(self) -> str:
        return "Levels"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="in_black",
                label="Input Black",
                param_type="int",
                default=0,
                min_val=0,
                max_val=255,
                step=1,
            ),
            FilterParamSchema(
                key="in_white",
                label="Input White",
                param_type="int",
                default=255,
                min_val=0,
                max_val=255,
                step=1,
            ),
            FilterParamSchema(
                key="gamma",
                label="Gamma",
                param_type="float",
                default=1.0,
                min_val=0.01,
                max_val=9.99,
                step=0.01,
            ),
            FilterParamSchema(
                key="out_black",
                label="Output Black",
                param_type="int",
                default=0,
                min_val=0,
                max_val=255,
                step=1,
            ),
            FilterParamSchema(
                key="out_white",
                label="Output White",
                param_type="int",
                default=255,
                min_val=0,
                max_val=255,
                step=1,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        in_black = int(params.get("in_black", 0))
        in_white = int(params.get("in_white", 255))
        gamma = float(params.get("gamma", 1.0))
        out_black = int(params.get("out_black", 0))
        out_white = int(params.get("out_white", 255))

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

        # Build LUT (256 entries)
        lut = self._build_lut(in_black, in_white, gamma, out_black, out_white)

        # Apply LUT to straight (un-premultiplied) RGB, then re-premultiply.
        # Channel order in memory (BGRA): B=0, G=1, R=2, A=3
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0
        adjusted = arr.copy()

        # Un-premultiply: straight = premul / alpha  (skip where alpha == 0)
        straight = np.zeros_like(arr[:, :, :3], dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight[:, :, ch],
                where=has_alpha,
            )
        straight = np.clip(straight, 0, 255).astype(np.uint8)

        # Apply LUT to straight RGB
        for ch in range(3):
            straight[:, :, ch] = lut[straight[:, :, ch]]

        # Re-premultiply: premul = straight * alpha / 255
        for ch in range(3):
            adjusted[:, :, ch] = np.where(
                has_alpha,
                (straight[:, :, ch].astype(np.float32) * alpha / 255.0 + 0.5).astype(np.uint8),
                np.uint8(0),
            )

        if mask is not None:
            adjusted = self._apply_mask(arr, adjusted, mask, w, h)

        result = QImage(adjusted.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        return result.copy()

    @staticmethod
    def _build_lut(
        in_black: int,
        in_white: int,
        gamma: float,
        out_black: int,
        out_white: int,
    ) -> NDArray[Any]:
        """Build a 256-entry uint8 lookup table for levels adjustment."""
        in_black = max(0, min(254, in_black))
        in_white = max(in_black + 1, min(255, in_white))
        gamma = max(0.01, min(9.99, gamma))
        out_black = max(0, min(255, out_black))
        out_white = max(0, min(255, out_white))

        x = np.arange(256, dtype=np.float64)
        # Normalize to input range
        normalized = (x - in_black) / (in_white - in_black)
        normalized = np.clip(normalized, 0.0, 1.0)
        # Apply gamma
        gamma_corrected = np.power(normalized, 1.0 / gamma)
        # Map to output range
        result = out_black + gamma_corrected * (out_white - out_black)
        return np.clip(result, 0, 255).astype(np.uint8)

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


LevelsEffect = LevelsFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
