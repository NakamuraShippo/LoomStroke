"""Blend a dab with real underlying premultiplied pixels."""

from __future__ import annotations

import numpy as np


class BrushDabCompositor:
    @staticmethod
    def apply(region: np.ndarray, alpha: np.ndarray, color_bgr: np.ndarray, mixing: float) -> None:
        pixels = region.astype(np.float32)
        destination_alpha = pixels[:, :, 3] / 255.0
        pigment = np.asarray(color_bgr, dtype=np.float32)
        if mixing > 0:
            weights = destination_alpha * alpha
            weight = float(weights.sum())
            if weight > 1e-6:
                straight = pixels[:, :, :3] / np.maximum(destination_alpha[:, :, None], 1e-6)
                pickup = (straight * weights[:, :, None]).sum(axis=(0, 1)) / weight
                pigment = pigment * (1 - mixing) + pickup * mixing
        region[:, :, :3] = np.clip(
            pixels[:, :, :3] * (1 - alpha[:, :, None]) + pigment * alpha[:, :, None], 0, 255
        )
        region[:, :, 3] = np.clip(
            (destination_alpha + alpha * (1 - destination_alpha)) * 255, 0, 255
        )
