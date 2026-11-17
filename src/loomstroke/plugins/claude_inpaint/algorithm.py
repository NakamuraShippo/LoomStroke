"""Coherence-transport inpainting kernel for the trial Claude inpaint pen.

Hole pixels are filled from the boundary inward. Each pixel averages the
already known pixels around it, weighted towards the ones lying along the local
contour direction, so edges continue across the hole instead of diffusing into
a blur. The direction comes from a structure tensor that is re-estimated from
the pixels filled so far (after Bornemann and Maerz, 2007).
"""

from __future__ import annotations

import logging
from functools import cache
from typing import Any

import cv2
import numpy as np

from loomstroke.core.telea_inpaint import telea_pixels

logger = logging.getLogger(__name__)

# Tuned on held-out brush masks over four colour illustrations; see
# docs/dev/claude_inpaint_research_20261117.md.
_SHARPNESS = 12.0
_TENSOR_SCALE = 3.0
_NOISE_FLOOR = 4.0
MAX_FEATHER = 20


@cache
def _transport_kernel() -> Any | None:  # noqa: PLR0915 - wraps the inline JIT kernel
    """Compile the sequential fill once. Return None if numba is unavailable."""
    try:
        from numba import njit  # noqa: PLC0415 - optional acceleration dependency
    except Exception:
        return None

    @njit(cache=True, nogil=True)  # type: ignore[misc]
    def _transport(  # noqa: PLR0912, PLR0913, PLR0915 - one tight per-pixel loop
        img: Any,
        hole: Any,
        order_y: Any,
        order_x: Any,
        reach: float,
        sharpness: float,
        tensor_scale: float,
        noise_floor: float,
    ) -> None:
        height, width = hole.shape
        channels = img.shape[2]
        known = ~hole
        tensor_radius = int(np.ceil(2 * tensor_scale))
        reach_radius = int(np.ceil(reach))
        falloff = 1.0 / (2 * tensor_scale * tensor_scale)
        total = np.zeros(channels, np.float64)
        for index in range(len(order_y)):
            y, x = order_y[index], order_x[index]
            jxx = jxy = jyy = weight_sum = 0.0
            for qy in range(max(1, y - tensor_radius), min(height - 1, y + tensor_radius + 1)):
                for qx in range(max(1, x - tensor_radius), min(width - 1, x + tensor_radius + 1)):
                    if not (
                        known[qy, qx]
                        and known[qy, qx - 1]
                        and known[qy, qx + 1]
                        and known[qy - 1, qx]
                        and known[qy + 1, qx]
                    ):
                        continue
                    g = np.exp(-((qy - y) ** 2 + (qx - x) ** 2) * falloff)
                    for c in range(channels):
                        gx = (img[qy, qx + 1, c] - img[qy, qx - 1, c]) * 0.5
                        gy = (img[qy + 1, qx, c] - img[qy - 1, qx, c]) * 0.5
                        jxx += g * gx * gx
                        jxy += g * gx * gy
                        jyy += g * gy * gy
                    weight_sum += g
            # Unit normal across the dominant contour, and how much to trust it.
            nx = ny = anisotropy = 0.0
            if weight_sum > 0:
                jxx /= weight_sum
                jxy /= weight_sum
                jyy /= weight_sum
                spread = np.sqrt((jxx - jyy) ** 2 + 4 * jxy * jxy)
                trace = jxx + jyy
                if abs(jxy) > 1e-9:  # noqa: PLR2004
                    nx, ny = jxy, (trace + spread) * 0.5 - jxx
                elif jxx >= jyy:
                    nx = 1.0
                else:
                    ny = 1.0
                norm = np.sqrt(nx * nx + ny * ny)
                if norm > 0:
                    nx /= norm
                    ny /= norm
                anisotropy = sharpness * spread / (trace + noise_floor)
            across = anisotropy * anisotropy / (2 * reach * reach)
            total[:] = 0.0
            weight_sum = 0.0
            for qy in range(max(0, y - reach_radius), min(height, y + reach_radius + 1)):
                for qx in range(max(0, x - reach_radius), min(width, x + reach_radius + 1)):
                    if not known[qy, qx]:
                        continue
                    dy, dx = qy - y, qx - x
                    distance2 = dy * dy + dx * dx
                    if distance2 > reach * reach:
                        continue
                    offset = nx * dx + ny * dy
                    weight = np.exp(-across * offset * offset) / np.sqrt(distance2)
                    for c in range(channels):
                        total[c] += weight * img[qy, qx, c]
                    weight_sum += weight
            if weight_sum > 1e-30:  # noqa: PLR2004
                for c in range(channels):
                    img[y, x, c] = total[c] / weight_sum
            known[y, x] = True

    return _transport


def claude_inpaint_pixels(
    pixels: np.ndarray, mask: np.ndarray, radius: int, *, feather: int = 0
) -> np.ndarray:
    """Inpaint premultiplied BGRA without changing pixels outside the mask.

    ``feather`` is the width in pixels, measured inward from the mask edge,
    over which the fill fades into the pixels that were there before.
    """
    return _inpaint(pixels, mask, radius, feather)


def _inpaint(pixels: np.ndarray, mask: np.ndarray, radius: int, feather: int) -> np.ndarray:
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:  # noqa: PLR2004
        raise ValueError("Expected premultiplied BGRA8 pixels.")
    if mask.dtype != np.uint8 or mask.shape != pixels.shape[:2]:
        raise ValueError("Expected a matching Alpha8 mask.")
    if not 1 <= radius <= 15:  # noqa: PLR2004
        raise ValueError("Sample radius must be between 1 and 15 pixels.")
    if not 0 <= feather <= MAX_FEATHER:
        raise ValueError(f"Edge blend must be between 0 and {MAX_FEATHER} pixels.")
    if not np.any(mask) or np.all(mask):
        raise ValueError("Inpainting requires both repair pixels and surrounding source pixels.")

    hole = mask != 0
    # Distance from the known pixels: the fill order, and the feather ramp.
    depth = cv2.distanceTransform(hole.astype(np.uint8), cv2.DIST_L2, 5)
    kernel = _transport_kernel()
    if kernel is None:
        logger.warning("numba is unavailable; Claude inpaint pen falls back to Telea.")
        working = telea_pixels(pixels, mask, radius).astype(np.float32)
    else:
        # Fill nearest-to-known first, so every pixel sees resolved neighbours.
        hole_y, hole_x = np.nonzero(hole)
        order = np.argsort(depth[hole_y, hole_x], kind="stable")
        working = pixels.astype(np.float32)
        kernel(
            working,
            hole,
            hole_y[order].astype(np.int32),
            hole_x[order].astype(np.int32),
            float(radius + 2),
            _SHARPNESS,
            _TENSOR_SCALE,
            _NOISE_FLOOR,
        )
    result = working[hole]
    if feather > 0:
        # The outermost ring keeps most of the previous pixel; depth feather+1
        # and deeper is pure fill. A mix of premultiplied colours stays valid.
        amount = np.minimum(depth[hole] / (feather + 1), 1.0)[:, None]
        result = pixels[hole] * (1.0 - amount) + result * amount
    filled = np.clip(np.rint(result), 0, 255).astype(np.uint8)
    np.minimum(filled[:, :3], filled[:, 3:4], out=filled[:, :3])
    output = pixels.copy()
    output[hole] = filled
    return output
