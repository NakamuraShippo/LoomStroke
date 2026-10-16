"""Brightness / Contrast adjustment live-filter filter."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema


class BrightnessContrastFilter(BaseFilter):
    """Brightness / Contrast adjustment via a 256-entry LUT.

    Parameters are percentages in ``[-100, +100]``:
    - ``brightness``: shifts all values by ``brightness * 2.55`` (±255)
    - ``contrast``: scales deviation from 128 by ``(1 + contrast/100)``

    The transform is ``f(x) = (x - 128) * (1 + c/100) + 128 + b * 2.55``.
    """

    def name(self) -> str:
        return "Brightness / Contrast"

    def category(self) -> str:
        return "adjustment"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="brightness",
                label="Brightness",
                param_type="float",
                default=0.0,
                min_val=-100.0,
                max_val=100.0,
                step=5.0,
            ),
            FilterParamSchema(
                key="contrast",
                label="Contrast",
                param_type="float",
                default=0.0,
                min_val=-100.0,
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
        brightness = float(params.get("brightness", 0.0))
        contrast = float(params.get("contrast", 0.0))

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

        lut = self._build_lut(brightness, contrast)

        # Un-premultiply BGR, apply LUT, re-premultiply (BGRA: B=0, G=1, R=2, A=3).
        alpha = arr[:, :, 3].astype(np.float32)
        has_alpha = alpha > 0.0
        adjusted = arr.copy()

        straight = np.zeros_like(arr[:, :, :3], dtype=np.float32)
        for ch in range(3):
            np.divide(
                arr[:, :, ch].astype(np.float32) * 255.0,
                alpha,
                out=straight[:, :, ch],
                where=has_alpha,
            )
        straight_u8 = np.clip(straight, 0, 255).astype(np.uint8)

        for ch in range(3):
            straight_u8[:, :, ch] = lut[straight_u8[:, :, ch]]

        for ch in range(3):
            adjusted[:, :, ch] = np.where(
                has_alpha,
                (straight_u8[:, :, ch].astype(np.float32) * alpha / 255.0 + 0.5).astype(np.uint8),
                np.uint8(0),
            )

        if mask is not None:
            adjusted = self._apply_mask(arr, adjusted, mask, w, h)

        result = QImage(adjusted.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied)
        return result.copy()

    @staticmethod
    def _build_lut(brightness: float, contrast: float) -> NDArray[Any]:
        """Build a 256-entry uint8 lookup table for brightness/contrast.

        ``brightness`` and ``contrast`` are percentages in ``[-100, +100]``.
        """
        brightness = max(-100.0, min(100.0, brightness))
        contrast = max(-100.0, min(100.0, contrast))

        x = np.arange(256, dtype=np.float64)
        contrast_factor = 1.0 + contrast / 100.0
        brightness_shift = brightness * 2.55
        result = (x - 128.0) * contrast_factor + 128.0 + brightness_shift
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


BrightnessContrastEffect = BrightnessContrastFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
