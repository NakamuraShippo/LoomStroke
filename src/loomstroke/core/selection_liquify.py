"""Local inverse-map deformation for raster selection brush strokes."""

from __future__ import annotations

import math

import cv2
import numpy as np
from numpy.typing import NDArray

MODES = {"push", "expand", "pinch", "twirl_right", "twirl_left", "push_left"}
Point = tuple[float, float]
Segment = tuple[Point, Point]


def pad_liquify_region(
    image: NDArray[np.uint8],
    mask: NDArray[np.uint8],
    bounds: tuple[int, int, int, int],
    canvas_size: tuple[int, int],
    padding: int,
) -> tuple[NDArray[np.uint8], NDArray[np.uint8], tuple[int, int, int, int]]:
    """Leave room for paint to move beyond the original selection rectangle."""
    x, y, width, height = bounds
    canvas_width, canvas_height = canvas_size
    if bounds == (0, 0, canvas_width, canvas_height):
        return image, mask, bounds
    left, top = min(x, max(0, x - padding)), min(y, max(0, y - padding))
    right = max(x + width, min(canvas_width, x + width + padding))
    bottom = max(y + height, min(canvas_height, y + height + padding))
    padded_image = np.zeros((bottom - top, right - left, 4), dtype=np.uint8)
    padded_mask = np.zeros((bottom - top, right - left), dtype=np.uint8)
    dx, dy = x - left, y - top
    padded_image[dy : dy + height, dx : dx + width] = image
    padded_mask[dy : dy + height, dx : dx + width] = mask
    return padded_image, padded_mask, (left, top, right - left, bottom - top)


def apply_liquify_segments(
    image: NDArray[np.uint8],
    mask: NDArray[np.uint8],
    bounds: tuple[int, int, int, int],
    segments: tuple[Segment, ...],
    *,
    mode: str,
    radius: int,
    strength: float,
    binary_mask: bool,
) -> bool:
    """Mutate detached image and mask arrays for one coalesced pointer batch."""
    if (
        mode not in MODES
        or image.ndim != 3
        or image.shape[2] != 4
        or mask.shape != image.shape[:2]
        or radius < 1
        or not 0.0 < strength <= 1.0
    ):
        return False
    changed = False
    for start, end in segments:
        if not all(math.isfinite(value) for value in (*start, *end)):
            continue
        distance = math.dist(start, end)
        if distance < 1e-6 and mode in {"push", "push_left"}:
            continue
        count = max(1, min(32, math.ceil(distance / max(1.0, radius * 0.35))))
        previous = start
        for index in range(1, count + 1):
            t = index / count
            current = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
            changed |= _apply_step(
                image,
                mask,
                bounds,
                previous,
                current,
                mode=mode,
                radius=radius,
                strength=strength,
                binary_mask=binary_mask,
            )
            previous = current
    return changed


def _apply_step(  # noqa: PLR0913
    image: NDArray[np.uint8],
    mask: NDArray[np.uint8],
    bounds: tuple[int, int, int, int],
    start: Point,
    end: Point,
    *,
    mode: str,
    radius: int,
    strength: float,
    binary_mask: bool,
) -> bool:
    cx, cy = end[0] - bounds[0], end[1] - bounds[1]
    height, width = mask.shape
    left, top = max(0, math.floor(cx - radius)), max(0, math.floor(cy - radius))
    right, bottom = min(width, math.ceil(cx + radius)), min(height, math.ceil(cy + radius))
    if right <= left or bottom <= top:
        return False
    grid_x, grid_y = np.meshgrid(
        np.arange(left, right, dtype=np.float32),
        np.arange(top, bottom, dtype=np.float32),
    )
    rx, ry = grid_x - cx, grid_y - cy
    radius_sq = float(radius * radius)
    falloff = np.clip(1.0 - (rx * rx + ry * ry) / radius_sq, 0.0, 1.0)
    falloff *= falloff * strength
    if mode in {"push", "push_left"}:
        dx, dy = end[0] - start[0], end[1] - start[1]
        if mode == "push_left":
            dx, dy = dy, -dx
        map_x = grid_x - dx * falloff
        map_y = grid_y - dy * falloff
    elif mode in {"expand", "pinch"}:
        direction = -1.0 if mode == "expand" else 1.0
        map_x = grid_x + direction * rx * falloff
        map_y = grid_y + direction * ry * falloff
    else:
        direction = -1.0 if mode == "twirl_right" else 1.0
        angle = direction * falloff * 0.35
        sine, cosine = np.sin(angle), np.cos(angle)
        map_x = cx + rx * cosine - ry * sine
        map_y = cy + rx * sine + ry * cosine
    map_x = map_x.astype(np.float32, copy=False)
    map_y = map_y.astype(np.float32, copy=False)
    sampled_image = cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    sampled_mask = cv2.remap(
        mask,
        map_x,
        map_y,
        cv2.INTER_NEAREST if binary_mask else cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
    )
    image[top:bottom, left:right] = sampled_image
    mask[top:bottom, left:right] = sampled_mask
    return True
