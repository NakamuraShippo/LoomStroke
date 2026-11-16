"""Shared local-brightness weighting for adaptive light and dark filters."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def local_context_params(params: dict[str, Any]) -> tuple[int, float]:
    """Return a bounded sample radius in pixels and local influence fraction."""
    radius = float(params.get("context_radius", 4.0))
    strength = float(params.get("local_strength", 100.0))
    radius = max(1.0, min(32.0, radius)) if math.isfinite(radius) else 4.0
    strength = max(0.0, min(100.0, strength)) if math.isfinite(strength) else 100.0
    return int(radius), strength / 100.0


def local_contrast_gate(
    rgb: np.ndarray, alpha: np.ndarray, radius: int, strength: float, *, light: bool
) -> np.ndarray:
    """Find directional contrast without opposite neighbors cancelling out."""
    if strength == 0.0:
        return np.zeros((*rgb.shape[:2], 1), dtype=np.float32)

    luma = rgb[:, :, 0] * 0.2126 + rgb[:, :, 1] * 0.7152 + rgb[:, :, 2] * 0.0722
    height, width = luma.shape
    pad = ((radius, radius), (radius, radius))
    luma_pad = np.pad(luma, pad, mode="edge")
    alpha_pad = np.pad(alpha, pad, mode="edge")
    contrast_sum = np.zeros_like(luma)
    coverage_sum = np.zeros_like(luma)
    neighbors = (
        (luma_pad[radius : radius + height, :width], alpha_pad[radius : radius + height, :width]),
        (
            luma_pad[radius : radius + height, 2 * radius : 2 * radius + width],
            alpha_pad[radius : radius + height, 2 * radius : 2 * radius + width],
        ),
        (luma_pad[:height, radius : radius + width], alpha_pad[:height, radius : radius + width]),
        (
            luma_pad[2 * radius : 2 * radius + height, radius : radius + width],
            alpha_pad[2 * radius : 2 * radius + height, radius : radius + width],
        ),
    )
    for neighbor_luma, neighbor_alpha in neighbors:
        signed = luma - neighbor_luma if light else neighbor_luma - luma
        contrast_sum += np.maximum(signed, 0.0) * neighbor_alpha
        coverage_sum += neighbor_alpha

    t = np.clip(4.0 * contrast_sum / np.maximum(coverage_sum, 1e-6), 0.0, 1.0)
    return (strength * t * t * (3.0 - 2.0 * t))[:, :, None]
