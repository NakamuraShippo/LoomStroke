"""Gaussian Blur live-filter filter."""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema

logger = logging.getLogger(__name__)


def _qimage_to_bgra(qimg: QImage) -> NDArray[Any]:
    """Convert QImage to a numpy BGRA array (zero-copy where possible)."""
    src = qimg.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    w, h = src.width(), src.height()
    bits = src.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(src.bytesPerLine() * h)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape(h, src.bytesPerLine())
    return arr[:, : w * 4].reshape(h, w, 4).copy()


def _bgra_to_qimage(arr: NDArray[Any]) -> QImage:
    """Convert a BGRA numpy array to QImage."""
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


class GaussianBlurFilter(BaseFilter):
    """Gaussian blur using OpenCV's optimized implementation."""

    def name(self) -> str:
        return "Gaussian Blur"

    def category(self) -> str:
        return "blur"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="radius",
                label="Radius",
                param_type="float",
                default=5.0,
                min_val=0.0,
                max_val=250.0,
                step=0.5,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        radius = float(params.get("radius", 5.0))
        if radius <= 0:
            return source.copy()

        arr = _qimage_to_bgra(source)
        # cv2.GaussianBlur requires odd kernel size
        ksize = int(radius) * 2 + 1
        blurred_arr = cv2.GaussianBlur(arr, (ksize, ksize), 0)
        blurred = _bgra_to_qimage(blurred_arr)

        if mask is not None:
            blurred = self._apply_mask(source, blurred, mask)
        return blurred

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    @staticmethod
    def _apply_mask(original: QImage, blurred: QImage, mask: QImage) -> QImage:
        """Blend original and blurred using mask intensity."""
        orig_arr = _qimage_to_bgra(original)
        blur_arr = _qimage_to_bgra(blurred)
        w, h = original.width(), original.height()

        # Scale mask to source size
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

        orig_f = orig_arr.astype(np.float32)
        blur_f = blur_arr.astype(np.float32)
        result_arr = orig_f + (blur_f - orig_f) * mask_arr
        result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)
        return _bgra_to_qimage(result_arr)


GaussianBlurEffect = GaussianBlurFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
