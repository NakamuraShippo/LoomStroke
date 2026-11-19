"""Projective raster sampling for an existing selection payload."""

from __future__ import annotations

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from .command_image_utils import _numpy_bgra_to_rgba, _numpy_rgba_to_bgra, _premultiply_rgba
from .commands_selection_mask import UpdateSelectionMaskCommand
from .selection_clipboard import SelectionPayload

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


def _image_array(payload: SelectionPayload, *, premultiplied: bool) -> NDArray[np.uint8] | None:
    image = payload.image
    if isinstance(image, np.ndarray):
        rgba = image.astype(np.uint8, copy=False)
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            return None
        return _numpy_rgba_to_bgra(_premultiply_rgba(rgba)) if premultiplied else rgba
    from PySide6.QtGui import QImage  # noqa: PLC0415

    if not isinstance(image, QImage) or image.isNull():
        return None
    fmt = (
        QImage.Format.Format_ARGB32_Premultiplied if premultiplied else QImage.Format.Format_ARGB32
    )
    source = image.convertToFormat(fmt)
    width, height, stride = source.width(), source.height(), source.bytesPerLine()
    bgra = np.frombuffer(source.constBits(), dtype=np.uint8).reshape(height, stride)
    pixels = bgra[:, : width * 4].reshape(height, width, 4)
    return pixels.copy() if premultiplied else _numpy_bgra_to_rgba(pixels)


def _mask_array(payload: SelectionPayload) -> NDArray[np.uint8] | None:
    mask = payload.mask
    if isinstance(mask, np.ndarray):
        return mask.astype(np.uint8, copy=False) if mask.ndim == 2 else None
    from PySide6.QtGui import QImage  # noqa: PLC0415

    if isinstance(mask, QImage) and not mask.isNull():
        return UpdateSelectionMaskCommand._mask_to_numpy(mask, mask.width(), mask.height())
    return None


def valid_quad(quad: Quad) -> bool:
    """Reject crossed, collapsed, and non-finite quadrilaterals before sampling."""
    if len(quad) != 4 or not all(math.isfinite(v) for point in quad for v in point):
        return False
    points = np.asarray(quad, dtype=np.float64)
    edges = np.roll(points, -1, axis=0) - points
    following = np.roll(edges, -1, axis=0)
    crosses = edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]
    return bool(np.all(crosses > 1e-4) or np.all(crosses < -1e-4))


def warp_selection_payload(
    payload: SelectionPayload,
    quad: Quad,
    *,
    premultiplied: bool,
    mask_mode: str,
    canvas_size: tuple[int, int] | None = None,
) -> tuple[NDArray[np.uint8], NDArray[np.uint8], tuple[int, int, int, int]] | None:
    """Map the selected image and mask to four canvas-space corners."""
    if not valid_quad(quad):
        return None
    image = _image_array(payload, premultiplied=premultiplied)
    mask = _mask_array(payload)
    if image is None or mask is None or image.shape[:2] != mask.shape:
        return None
    height, width = mask.shape
    if width < 1 or height < 1:
        return None
    source = np.asarray(((0, 0), (width, 0), (width, height), (0, height)), dtype=np.float32)
    dest = np.asarray(quad, dtype=np.float32)
    x0 = math.floor(float(np.min(dest[:, 0])))
    y0 = math.floor(float(np.min(dest[:, 1])))
    x1 = math.ceil(float(np.max(dest[:, 0])))
    y1 = math.ceil(float(np.max(dest[:, 1])))
    if canvas_size is not None:
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(canvas_size[0], x1), min(canvas_size[1], y1)
    dest_w, dest_h = x1 - x0, y1 - y0
    if dest_w < 1 or dest_h < 1 or dest_w * dest_h > 100_000_000:
        return None
    matrix = cv2.getPerspectiveTransform(source - 0.5, dest - 0.5)
    if not np.isfinite(matrix).all():
        return None
    matrix[0, :] -= float(x0) * matrix[2, :]
    matrix[1, :] -= float(y0) * matrix[2, :]
    flags = cv2.INTER_LINEAR
    sampled_image = cv2.warpPerspective(
        image, matrix, (dest_w, dest_h), flags=flags, borderMode=cv2.BORDER_CONSTANT
    )
    sampled_mask = cv2.warpPerspective(
        mask,
        matrix,
        (dest_w, dest_h),
        flags=flags if mask_mode == "soft" else cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
    )
    if mask_mode == "binary":
        sampled_mask = np.where(sampled_mask > 127, 255, 0).astype(np.uint8)
    return sampled_image, sampled_mask, (x0, y0, dest_w, dest_h)
