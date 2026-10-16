"""Noise Reduction (Denoise) live-filter filter."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra

# Kernel reach, in pixels, at the schema's maximum Strength.
_MAX_DENOISE_RADIUS = 10


def bilateral_denoise_params(strength: float, color_strength: float) -> tuple[int, float, float]:
    """Return ``(radius, sigma_luma, sigma_chroma)`` for the denoise kernel.

    Shared with ``WgpuComputeProcessor.bilateral`` so the canvas, merge and
    export all run the same filter.  Strength sets both the spatial reach and
    the luma range falloff; Color Strength sets the chroma range falloff, the
    same split OpenCV exposes as ``h`` / ``hColor``.
    """

    value = max(0.0, float(strength))
    if value <= 0.0:
        return (0, 0.0, 0.0)
    radius = max(1, min(_MAX_DENOISE_RADIUS, round(value / 4.0)))
    sigma_luma = max(value, 1.0) / 100.0
    sigma_chroma = max(float(color_strength), 1.0) / 100.0
    return (radius, sigma_luma, sigma_chroma)


def bilateral_denoise(rgb: np.ndarray, radius: int, sigma_luma: float, sigma_chroma: float):
    """Port of the ``bilateral`` compute kernel, on straight float RGB in 0..1.

    Kept a plain function so the shader and this stay diffable side by side.
    """

    luma_weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    centre_luma = rgb @ luma_weights
    centre_chroma = rgb - centre_luma[..., None]

    spatial_sigma = max(radius * 0.5, 1e-4)
    spatial_denom = 2.0 * spatial_sigma * spatial_sigma
    luma_denom = 2.0 * max(sigma_luma, 1e-4) ** 2
    chroma_denom = 2.0 * max(sigma_chroma, 1e-4) ** 2

    # ``sample_px`` clamps to the edge; padding gives the same neighbourhood.
    height, width = rgb.shape[:2]
    padded = np.pad(rgb, ((radius, radius), (radius, radius), (0, 0)), mode="edge")

    accum = np.zeros_like(rgb)
    weight_sum = np.zeros(rgb.shape[:2], dtype=np.float32)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            shifted = padded[
                radius + dy : radius + dy + height,
                radius + dx : radius + dx + width,
            ]
            sample_luma = shifted @ luma_weights
            luma_diff = sample_luma - centre_luma
            chroma_diff = np.linalg.norm((shifted - sample_luma[..., None]) - centre_chroma, axis=2)
            weight = np.exp(
                -(dx * dx + dy * dy) / spatial_denom
                - (luma_diff * luma_diff) / luma_denom
                - (chroma_diff * chroma_diff) / chroma_denom
            ).astype(np.float32)
            accum += shifted * weight[..., None]
            weight_sum += weight
    return accum / np.maximum(weight_sum, 1e-6)[..., None]


class NoiseReductionFilter(BaseFilter):
    """Non-local means denoising using OpenCV."""

    def name(self) -> str:
        return "Noise Reduction"

    def category(self) -> str:
        return "detail"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="strength",
                label="Strength",
                param_type="float",
                default=10.0,
                min_val=1.0,
                max_val=40.0,
                step=1.0,
            ),
            FilterParamSchema(
                key="color_strength",
                label="Color Strength",
                param_type="float",
                default=10.0,
                min_val=1.0,
                max_val=40.0,
                step=1.0,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        strength = float(params.get("strength", 10.0))
        color_strength = float(params.get("color_strength", 10.0))

        radius, sigma_luma, sigma_chroma = bilateral_denoise_params(strength, color_strength)
        if radius <= 0:
            return source.copy()

        # This used to be ``cv2.fastNlMeansDenoisingColored``, which the wgpu
        # canvas has no way to run: merging or exporting applied a heavy
        # non-local-means pass the user never saw while painting.  Both sides
        # now run the bilateral kernel below.
        arr = _qimage_to_bgra(source)
        rgb = arr[:, :, :3].astype(np.float32) / 255.0
        denoised = bilateral_denoise(rgb, radius, sigma_luma, sigma_chroma)

        result_arr = np.empty_like(arr)
        result_arr[:, :, :3] = np.clip(denoised * 255.0 + 0.5, 0, 255).astype(np.uint8)
        result_arr[:, :, 3] = arr[:, :, 3]
        result = _bgra_to_qimage(result_arr)

        if mask is not None:
            result = self._apply_mask(source, result, mask)
        return result

    @staticmethod
    def _apply_mask(original: QImage, denoised: QImage, mask: QImage) -> QImage:
        orig_arr = _qimage_to_bgra(original)
        den_arr = _qimage_to_bgra(denoised)
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
            + (den_arr.astype(np.float32) - orig_arr.astype(np.float32)) * mask_arr
        )
        result_arr = np.clip(result_arr, 0, 255).astype(np.uint8)
        return _bgra_to_qimage(result_arr)


NoiseReductionEffect = NoiseReductionFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
