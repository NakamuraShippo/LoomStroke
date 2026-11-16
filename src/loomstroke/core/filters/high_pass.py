"""High-pass detail image centred on middle gray."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra


def high_pass_radius(params: dict[str, Any]) -> float:
    """Bound the saved radius to the range supported by the GPU blur."""
    try:
        radius = float(params.get("radius", 3.0))
    except (TypeError, ValueError):
        return 3.0
    if not np.isfinite(radius):
        return 3.0
    return max(1.0, min(50.0, radius))


class HighPassFilter(BaseFilter):
    def name(self) -> str:
        return "High Pass"

    def category(self) -> str:
        return "detail"

    def param_schema(self) -> list[FilterParamSchema]:
        return [FilterParamSchema("radius", "Radius", "float", 3.0, 1.0, 50.0, 0.5)]

    def apply(self, source: QImage, params: dict[str, Any], mask: QImage | None = None) -> QImage:
        if source.isNull():
            return source.copy()
        radius = high_pass_radius(params)
        premultiplied = _qimage_to_bgra(source)
        blurred = cv2.GaussianBlur(premultiplied, (2 * int(radius) + 1,) * 2, 0)
        blurred_image = _bgra_to_qimage(blurred).convertToFormat(QImage.Format.Format_RGBA8888)
        straight_image = source.convertToFormat(QImage.Format.Format_RGBA8888)
        height, width = source.height(), source.width()
        original = (
            np.frombuffer(
                straight_image.bits(), dtype=np.uint8, count=straight_image.bytesPerLine() * height
            )
            .reshape(height, straight_image.bytesPerLine())[:, : width * 4]
            .reshape(height, width, 4)
        )
        low = (
            np.frombuffer(
                blurred_image.bits(), dtype=np.uint8, count=blurred_image.bytesPerLine() * height
            )
            .reshape(height, blurred_image.bytesPerLine())[:, : width * 4]
            .reshape(height, width, 4)
        )
        result = original.copy()
        result[:, :, :3] = cv2.addWeighted(original[:, :, :3], 0.5, low[:, :, :3], -0.5, 128.0)
        from loomstroke.core.canvas_blend_service import CanvasBlendService  # noqa: PLC0415
        from loomstroke.core.commands import BlendMode  # noqa: PLC0415

        mode = CanvasBlendService.normalize_blend_mode(params.get("blend_mode", "normal"))
        if mode not in (BlendMode.NORMAL, BlendMode.PASS_THROUGH):
            source_rgb = original[:, :, :3].astype(np.float32)
            high_rgb = result[:, :, :3].astype(np.float32)
            if mode == BlendMode.DISSOLVE:
                y, x = np.indices((height, width), dtype=np.uint64)
                choose_high = ((x * 1664525 + y * 1013904223) & 0xFFFFFFFF) < 0x80000000
                result[:, :, :3] = np.where(choose_high[:, :, None], high_rgb, source_rgb)
            else:
                blended = CanvasBlendService.blended_rgb_straight(high_rgb, source_rgb, mode)
                assert blended is not None
                result[:, :, :3] = np.clip(blended + 0.5, 0, 255).astype(np.uint8)
        if mask is not None:
            scaled = mask.scaled(
                width, height, Qt.AspectRatioMode.IgnoreAspectRatio
            ).convertToFormat(QImage.Format.Format_Grayscale8)
            coverage = np.frombuffer(
                scaled.bits(), dtype=np.uint8, count=scaled.bytesPerLine() * height
            ).reshape(height, scaled.bytesPerLine())[:, :width]
            weight = coverage[:, :, None].astype(np.float32) / 255.0
            result[:, :, :3] = np.clip(
                original[:, :, :3]
                + (result[:, :, :3].astype(np.float32) - original[:, :, :3]) * weight
                + 0.5,
                0,
                255,
            ).astype(np.uint8)
        opacity = max(0.0, min(1.0, float(params.get("_layer_opacity", 1.0))))
        if opacity < 1.0:
            result[:, :, :3] = np.clip(
                original[:, :, :3]
                + (result[:, :, :3].astype(np.float32) - original[:, :, :3]) * opacity
                + 0.5,
                0,
                255,
            ).astype(np.uint8)
        return QImage(result.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
