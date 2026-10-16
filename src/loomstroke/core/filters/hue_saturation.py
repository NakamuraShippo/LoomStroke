"""Hue/Saturation/Lightness adjustment live-filter filter."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra


class HueSaturationFilter(BaseFilter):
    """Adjust hue, saturation, and lightness."""

    def name(self) -> str:
        return "Hue/Saturation"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="hue",
                label="Hue",
                param_type="float",
                default=0.0,
                min_val=-180.0,
                max_val=180.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="saturation",
                label="Saturation",
                param_type="float",
                default=0.0,
                min_val=-100.0,
                max_val=100.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="lightness",
                label="Lightness",
                param_type="float",
                default=0.0,
                min_val=-100.0,
                max_val=100.0,
                step=1.0,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        hue_shift = float(params.get("hue", 0.0))
        sat_adj = float(params.get("saturation", 0.0))
        light_adj = float(params.get("lightness", 0.0))

        if hue_shift == 0.0 and sat_adj == 0.0 and light_adj == 0.0:
            return source.copy()

        arr = _qimage_to_bgra(source)
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0

        # Un-premultiply BGR channels to get straight colour
        straight_bgr = np.zeros((arr.shape[0], arr.shape[1], 3), dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight_bgr[:, :, ch],
                where=has_alpha,
            )
        straight_bgr = np.clip(straight_bgr, 0, 255).astype(np.uint8)

        # Convert to HLS (OpenCV uses HLS, not HSL)
        hls = cv2.cvtColor(straight_bgr, cv2.COLOR_BGR2HLS).astype(np.float32)

        # Hue: 0-180 in OpenCV, shift wraps around
        hls[:, :, 0] = (hls[:, :, 0] + hue_shift / 2.0) % 180.0

        # Lightness adjustment
        if light_adj != 0.0:
            factor = 1.0 + light_adj / 100.0
            hls[:, :, 1] = np.clip(hls[:, :, 1] * factor, 0, 255)

        # Saturation adjustment
        if sat_adj != 0.0:
            factor = 1.0 + sat_adj / 100.0
            hls[:, :, 2] = np.clip(hls[:, :, 2] * factor, 0, 255)

        adjusted_bgr = cv2.cvtColor(hls.astype(np.uint8), cv2.COLOR_HLS2BGR)

        # Re-premultiply
        result_arr = np.empty_like(arr)
        for ch in range(3):
            result_arr[:, :, ch] = np.where(
                has_alpha,
                np.clip(
                    adjusted_bgr[:, :, ch].astype(np.float32) * alpha / 255.0 + 0.5,
                    0,
                    255,
                ).astype(np.uint8),
                np.uint8(0),
            )
        result_arr[:, :, 3] = arr[:, :, 3]
        result = _bgra_to_qimage(result_arr)

        if mask is not None:
            result = self._apply_mask(source, result, mask)
        return result

    @staticmethod
    def _apply_mask(original: QImage, adjusted: QImage, mask: QImage) -> QImage:
        orig_arr = _qimage_to_bgra(original)
        adj_arr = _qimage_to_bgra(adjusted)
        w, h = original.width(), original.height()

        scaled_mask = mask.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        bc = scaled_mask.bytesPerLine() * scaled_mask.height()
        mbits = scaled_mask.bits()
        if hasattr(mbits, "setsize"):
            mbits.setsize(bc)
        mask_flat = np.frombuffer(mbits, dtype=np.uint8, count=bc).reshape(
            scaled_mask.height(), scaled_mask.bytesPerLine()
        )
        mask_arr = mask_flat[:, : scaled_mask.width()].reshape(h, w, 1).astype(np.float32) / 255.0

        result_arr = (
            orig_arr.astype(np.float32)
            + (adj_arr.astype(np.float32) - orig_arr.astype(np.float32)) * mask_arr
        )
        result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)
        return _bgra_to_qimage(result_arr)


HueSaturationEffect = HueSaturationFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
