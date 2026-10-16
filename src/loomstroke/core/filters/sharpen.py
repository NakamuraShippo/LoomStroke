"""Unsharp Mask (Sharpen) live-filter filter."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra


class SharpenFilter(BaseFilter):
    """Unsharp-mask sharpening using OpenCV."""

    def name(self) -> str:
        return "Sharpen"

    def category(self) -> str:
        return "detail"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="amount",
                label="Amount",
                param_type="float",
                default=50.0,
                min_val=0.0,
                max_val=500.0,
                step=5.0,
            ),
            FilterParamSchema(
                key="radius",
                label="Radius",
                param_type="float",
                default=1.0,
                min_val=0.1,
                max_val=50.0,
                step=0.5,
            ),
            FilterParamSchema(
                key="threshold",
                label="Threshold",
                param_type="int",
                default=0,
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
        amount = float(params.get("amount", 50.0)) / 100.0
        radius = float(params.get("radius", 1.0))
        threshold = int(params.get("threshold", 0))

        if amount <= 0 or radius <= 0:
            return source.copy()

        arr = _qimage_to_bgra(source)
        ksize = max(int(radius) * 2 + 1, 3)
        blurred = cv2.GaussianBlur(arr, (ksize, ksize), 0)

        # Unsharp mask: sharpened = arr + amount * (arr - blurred).
        # Hot path (threshold=0): cv2.addWeighted does the math in uint8
        # with saturation, avoiding ~120 MB/frame of float32 buffers at
        # 1920x1080.  Threshold path stays in float32 — it is rarely used
        # and needs the signed diff for per-pixel masking.
        if threshold > 0:
            diff = arr.astype(np.float32) - blurred.astype(np.float32)
            mag = np.abs(diff).max(axis=2, keepdims=True)
            mask_thr = (mag >= threshold).astype(np.float32)
            diff = diff * mask_thr
            sharpened_f = arr.astype(np.float32) + amount * diff
            sharpened = np.clip(sharpened_f, 0, 255).astype(np.uint8)
        else:
            sharpened = cv2.addWeighted(arr, 1.0 + amount, blurred, -amount, 0.0)
        result = _bgra_to_qimage(sharpened)

        if mask is not None:
            result = self._apply_mask(source, result, mask)
        return result

    @staticmethod
    def _apply_mask(original: QImage, sharpened: QImage, mask: QImage) -> QImage:
        orig_arr = _qimage_to_bgra(original)
        sharp_arr = _qimage_to_bgra(sharpened)
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
            + (sharp_arr.astype(np.float32) - orig_arr.astype(np.float32)) * mask_arr
        )
        result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)
        return _bgra_to_qimage(result_arr)


SharpenEffect = SharpenFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
