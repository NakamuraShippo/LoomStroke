"""Color Invert live-filter filter."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra


class InvertFilter(BaseFilter):
    """Invert RGB channels while preserving alpha."""

    def name(self) -> str:
        return "Invert"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="strength",
                label="Strength",
                param_type="float",
                default=100.0,
                min_val=0.0,
                max_val=100.0,
                step=5.0,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        strength = float(params.get("strength", 100.0)) / 100.0
        if strength <= 0:
            return source.copy()

        arr = _qimage_to_bgra(source)
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0

        # Un-premultiply BGR channels
        straight = np.zeros((arr.shape[0], arr.shape[1], 3), dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight[:, :, ch],
                where=has_alpha,
            )
        straight = np.clip(straight, 0, 255)

        # Invert straight RGB
        inv_straight = 255.0 - straight
        if strength < 1.0:
            inv_straight = straight * (1.0 - strength) + inv_straight * strength

        # Re-premultiply
        inverted = arr.copy()
        for ch in range(3):
            inverted[:, :, ch] = np.where(
                has_alpha,
                np.clip(inv_straight[:, :, ch] * alpha / 255.0 + 0.5, 0, 255).astype(np.uint8),
                np.uint8(0),
            )

        result = _bgra_to_qimage(inverted)
        if mask is not None:
            result = self._apply_mask(source, result, mask)
        return result

    @staticmethod
    def _apply_mask(original: QImage, inverted: QImage, mask: QImage) -> QImage:
        orig_arr = _qimage_to_bgra(original)
        inv_arr = _qimage_to_bgra(inverted)
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
            + (inv_arr.astype(np.float32) - orig_arr.astype(np.float32)) * mask_arr
        )
        result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)
        return _bgra_to_qimage(result_arr)


InvertEffect = InvertFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
