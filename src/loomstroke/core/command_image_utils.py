"""Image utility helpers for commands."""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .command_types import SelectionRect
from .geometry import Affine2D

logger = logging.getLogger("loomstroke.core.commands")


def _is_qimage_layer(layer: Any) -> bool:
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return False
    return hasattr(layer, "image") and isinstance(getattr(layer, "image", None), QImage)


def _ensure_qimage_premultiplied(layer: Any) -> Any | None:
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    image = getattr(layer, "image", None)
    if not isinstance(image, QImage) or image.isNull():
        return None
    if image.format() != QImage.Format.Format_ARGB32_Premultiplied:
        image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        try:
            layer.image = image
        except Exception as exc:
            logger.debug("_ensure_qimage_premultiplied: layer.image setter failed: %s", exc)
            return image
    return image


def _qimage_to_numpy_view(image: Any) -> NDArray[Any] | None:
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    if not isinstance(image, QImage):
        return None
    bytes_per_line = image.bytesPerLine()
    size = bytes_per_line * image.height()
    buf = image.bits()
    if hasattr(buf, "setsize"):
        buf.setsize(size)
        view = buf
    else:
        view = memoryview(buf)
        if view.nbytes < size:
            return None
        view = view[:size]
    arr = np.frombuffer(view, dtype=np.uint8, count=size)
    arr = arr.reshape((image.height(), bytes_per_line))[:, : image.width() * 4]
    return arr.reshape((image.height(), image.width(), 4))


def _qimage_to_numpy_copy(image: Any) -> NDArray[Any] | None:
    view = _qimage_to_numpy_view(image)
    if view is None:
        return None
    return view.copy()


def _numpy_rgba_to_bgra(image: NDArray[Any]) -> NDArray[Any]:
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError("Expected RGBA image array")
    return image[..., [2, 1, 0, 3]]


def _numpy_bgra_to_rgba(image: NDArray[Any]) -> NDArray[Any]:
    if image.ndim != 3 or image.shape[2] != 4:
        raise ValueError("Expected BGRA image array")
    return image[..., [2, 1, 0, 3]]


def _premultiply_rgba(image: NDArray[Any]) -> NDArray[Any]:
    data = image.astype(np.float32, copy=False)
    alpha = data[..., 3:4] / 255.0
    data[..., :3] = data[..., :3] * alpha
    return np.clip(data, 0.0, 255.0).astype(np.uint8)


def _unpremultiply_rgba(image: NDArray[Any]) -> NDArray[Any]:
    data = image.astype(np.float32, copy=False)
    alpha = data[..., 3:4] / 255.0
    safe_alpha = np.clip(alpha, 1e-6, 1.0)
    data[..., :3] = np.where(alpha > 0, data[..., :3] / safe_alpha, 0.0)
    return np.clip(data, 0.0, 255.0).astype(np.uint8)


def _qimage_from_bgra(image: NDArray[Any], *, premultiplied: bool) -> Any | None:
    try:
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        return None
    arr = np.ascontiguousarray(image, dtype=np.uint8)
    height, width = arr.shape[:2]
    bytes_per_line = arr.strides[0]
    fmt = (
        QImage.Format.Format_ARGB32_Premultiplied if premultiplied else QImage.Format.Format_ARGB32
    )
    qimage = QImage(arr.data, width, height, bytes_per_line, fmt)
    return qimage.copy()


def _place_mask_on_canvas(
    mask_patch: NDArray[Any],
    *,
    position: tuple[int, int],
    width: int,
    height: int,
) -> tuple[NDArray[Any], SelectionRect | None, SelectionRect | None]:
    full = np.zeros((height, width), dtype=np.uint8)
    x, y = position
    h, w = mask_patch.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(width, x + w)
    y2 = min(height, y + h)
    if x1 >= x2 or y1 >= y2:
        return full, None, None
    src_x1 = x1 - x
    src_y1 = y1 - y
    src_x2 = src_x1 + (x2 - x1)
    src_y2 = src_y1 + (y2 - y1)
    full[y1:y2, x1:x2] = mask_patch[src_y1:src_y2, src_x1:src_x2]
    bounds = (x1, y1, x2 - x1, y2 - y1)
    src_bounds = (src_x1, src_y1, src_x2 - src_x1, src_y2 - src_y1)
    return full, bounds, src_bounds


def _composite_premultiplied(
    dest: NDArray[Any],
    src: NDArray[Any],
    mask: NDArray[Any],
) -> None:
    if dest.size == 0 or src.size == 0:
        return
    mask_f = mask.astype(np.float32) / 255.0
    src_alpha = src[..., 3].astype(np.float32) / 255.0
    eff_alpha = src_alpha * mask_f
    if np.all(eff_alpha <= 0):
        return
    dest_rgb = dest[..., :3].astype(np.float32) / 255.0
    dest_alpha = dest[..., 3].astype(np.float32) / 255.0
    src_rgb = src[..., :3].astype(np.float32) / 255.0
    src_rgb *= mask_f[..., None]
    out_rgb = src_rgb + dest_rgb * (1.0 - eff_alpha[..., None])
    out_alpha = eff_alpha + dest_alpha * (1.0 - eff_alpha)
    dest[..., :3] = np.clip(out_rgb * 255.0, 0.0, 255.0).astype(np.uint8)
    dest[..., 3] = np.clip(out_alpha * 255.0, 0.0, 255.0).astype(np.uint8)


def _composite_nonpremultiplied(
    dest: NDArray[Any],
    src: NDArray[Any],
    mask: NDArray[Any],
) -> None:
    if dest.size == 0 or src.size == 0:
        return
    src_alpha = src[..., 3].astype(np.float32) / 255.0
    mask_f = mask.astype(np.float32) / 255.0
    eff_alpha = src_alpha * mask_f
    if np.all(eff_alpha <= 0):
        return
    dest_alpha = dest[..., 3].astype(np.float32) / 255.0
    src_rgb = src[..., :3].astype(np.float32) / 255.0
    dest_rgb = dest[..., :3].astype(np.float32) / 255.0
    out_alpha = eff_alpha + dest_alpha * (1.0 - eff_alpha)
    out_rgb = np.where(
        out_alpha[..., None] > 0,
        (
            src_rgb * eff_alpha[..., None]
            + dest_rgb * dest_alpha[..., None] * (1.0 - eff_alpha[..., None])
        )
        / np.clip(out_alpha[..., None], 1e-6, 1.0),
        dest_rgb,
    )
    dest[..., :3] = np.clip(out_rgb * 255.0, 0.0, 255.0)
    dest[..., 3] = np.clip(out_alpha * 255.0, 0.0, 255.0)


def _affine_bounds(transform: Affine2D, width: int, height: int) -> SelectionRect | None:
    points = [
        transform.apply(0.0, 0.0),
        transform.apply(float(width), 0.0),
        transform.apply(float(width), float(height)),
        transform.apply(0.0, float(height)),
    ]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    x0 = math.floor(min_x)
    y0 = math.floor(min_y)
    x1 = math.ceil(max_x)
    y1 = math.ceil(max_y)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _union_bounds(a: SelectionRect | None, b: SelectionRect | None) -> SelectionRect | None:
    if a is None:
        return b
    if b is None:
        return a
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = min(ax, bx)
    y0 = min(ay, by)
    x1 = max(ax + aw, bx + bw)
    y1 = max(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)
