"""Shadow-weighted Multiply / Soft Light filter for a single layer."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .local_context import local_context_params, local_contrast_gate


def adaptive_dark_params(params: dict[str, Any]) -> tuple[float, float, str]:
    """Return bounded threshold/strength fractions and a stable blend mode."""
    threshold = float(params.get("threshold", 55.0))
    strength = float(params.get("strength", 70.0))
    threshold = max(1.0, min(100.0, threshold)) if math.isfinite(threshold) else 55.0
    strength = max(0.0, min(100.0, strength)) if math.isfinite(strength) else 70.0
    mode = "soft_light" if params.get("mode") == "soft_light" else "multiply"
    return threshold / 100.0, strength / 100.0, mode


class AdaptiveDarkFilter(BaseFilter):
    """Deepen dark pixels while preserving brighter areas and alpha."""

    def name(self) -> str:
        return "Adaptive Dark"

    def category(self) -> str:
        return "shadow"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema("threshold", "Threshold", "float", 55.0, 1.0, 100.0, 1.0),
            FilterParamSchema("strength", "Strength", "float", 70.0, 0.0, 100.0, 1.0),
            FilterParamSchema("context_radius", "Local Radius", "float", 4.0, 1.0, 32.0, 1.0),
            FilterParamSchema("local_strength", "Local Strength", "float", 100.0, 0.0, 100.0, 1.0),
            FilterParamSchema(
                "mode", "Blend Mode", "enum", "multiply", choices=["multiply", "soft_light"]
            ),
        ]

    def apply(self, source: QImage, params: dict[str, Any], mask: QImage | None = None) -> QImage:
        threshold, strength, mode = adaptive_dark_params(params)
        if strength == 0.0 or source.isNull():
            return source.copy()

        image = source.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        byte_count = image.bytesPerLine() * height
        pixels = (
            np.frombuffer(image.bits(), dtype=np.uint8, count=byte_count)
            .reshape(height, image.bytesPerLine())[:, : width * 4]
            .reshape(height, width, 4)
            .copy()
        )
        rgb = pixels[:, :, :3].astype(np.float32) / 255.0
        peak = np.max(rgb, axis=2, keepdims=True)
        t = np.clip((threshold - peak) / threshold, 0.0, 1.0)
        base_gate = t * t * (3.0 - 2.0 * t)
        radius, local_strength = local_context_params(params)
        local_gate = local_contrast_gate(
            rgb, pixels[:, :, 3].astype(np.float32) / 255.0, radius, local_strength, light=False
        )
        weight = strength * np.maximum(base_gate, local_gate)

        if mask is not None:
            scaled = mask.scaled(
                width, height, Qt.AspectRatioMode.IgnoreAspectRatio
            ).convertToFormat(QImage.Format.Format_Grayscale8)
            mask_bytes = scaled.bytesPerLine() * height
            coverage = np.frombuffer(scaled.bits(), dtype=np.uint8, count=mask_bytes).reshape(
                height, scaled.bytesPerLine()
            )[:, :width]
            weight *= coverage[:, :, None].astype(np.float32) / 255.0

        if not np.any(weight):
            return source.copy()

        if mode == "soft_light":
            d = np.where(
                rgb <= 0.25,
                ((16.0 * rgb - 12.0) * rgb + 4.0) * rgb,
                np.sqrt(rgb),
            )
            soft_light = np.where(
                rgb <= 0.5,
                rgb - (1.0 - 2.0 * rgb) * rgb * (1.0 - rgb),
                rgb + (2.0 * rgb - 1.0) * (d - rgb),
            )
            blended = np.minimum(rgb, soft_light)
        else:
            blended = rgb * rgb

        pixels[:, :, :3] = np.clip((rgb + (blended - rgb) * weight) * 255.0 + 0.5, 0, 255)
        return QImage(pixels.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
