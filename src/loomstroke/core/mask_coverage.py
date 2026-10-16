"""Canonical mask-coverage semantics shared by every mask producer and consumer.

LoomStroke masks (layer masks, effect masks, and future SAM3.1 video / ComfyUI
masks) encode *coverage*: 0.0 hides the pixel, 1.0 shows it. The canonical
image representation is a grayscale ``Format_ARGB32_Premultiplied`` QImage with

    R = G = B = coverage, A = 255

so black hides, white shows, and gray is partial. Producers must normalize to
this form; consumers must not read a single channel directly.

Legacy documents may still contain masks that encode coverage in the alpha
channel or as premultiplied gray. Extraction therefore reads
``min(luminance(rgb), alpha)`` over the raw (premultiplied) channel values,
which maps every known legacy form to the same coverage:

    canonical gray  RGB=c,   A=255  -> c
    alpha-encoded   RGB=255, A=c    -> premultiplied to RGB=c, A=c -> c
    premultiplied   RGB=c,   A=c    -> c
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def _premultiplied_channels(mask: QImage) -> np.ndarray:
    """Return the mask's raw premultiplied BGRA bytes as an owned (h, w, 4) array."""

    converted = mask
    if converted.format() != QImage.Format.Format_ARGB32_Premultiplied:
        converted = converted.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    width = converted.width()
    height = converted.height()
    bytes_per_line = converted.bytesPerLine()
    # A mutable bits() access detaches shared images and changes cacheKey(),
    # even without a write, invalidating the renderer's mask caches.
    bits = converted.constBits()
    if hasattr(bits, "setsize"):
        bits.setsize(bytes_per_line * height)
    rows = np.frombuffer(bits, dtype=np.uint8, count=bytes_per_line * height)
    # .copy() detaches from the QImage buffer: when *mask* needed a format
    # conversion, `converted` is a temporary whose buffer dies with this
    # frame, and a view into it would read freed memory.
    return rows.reshape(height, bytes_per_line)[:, : width * 4].reshape(height, width, 4).copy()


def _coverage_array(mask: QImage) -> np.ndarray:
    """Extract coverage as a (h, w) uint8 array via min(luminance, alpha)."""

    channels = _premultiplied_channels(mask)
    # This float32 expression (term order included) and the truncation
    # reproduce the legacy grayscale normalization byte-for-byte; existing
    # undo patches and tests pin those exact values.
    b = channels[:, :, 0].astype(np.float32)
    g = channels[:, :, 1].astype(np.float32)
    r = channels[:, :, 2].astype(np.float32)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b).astype(np.uint8)
    return np.minimum(luminance, channels[:, :, 3])


def mask_coverage_alpha8(mask: QImage) -> QImage:
    """Extract a mask's coverage as a ``Format_Alpha8`` QImage.

    This is the single compatibility read every consumer must use: renderers
    that clip through alpha (``CompositionMode_DestinationIn``, wgpu shaders
    sampling ``.a``) feed the returned image instead of the raw mask.
    ``Format_Alpha8`` inputs already are pure coverage and pass through.
    """

    if mask.format() == QImage.Format.Format_Alpha8:
        return mask
    coverage = np.ascontiguousarray(_coverage_array(mask))
    height, width = coverage.shape
    return QImage(coverage.data, width, height, width, QImage.Format.Format_Alpha8).copy()


def normalize_mask_image(mask: QImage) -> None:
    """Rewrite *mask* in place into the canonical form (RGB=coverage, A=255).

    Producers that edit masks through ordinary painting (mask brush, eraser)
    call this after the edit so the stored mask never depends on
    premultiplied-alpha side effects. Only ARGB32-family images can be
    normalized in place; other formats are left untouched.
    """

    if mask.format() not in (
        QImage.Format.Format_ARGB32_Premultiplied,
        QImage.Format.Format_ARGB32,
        QImage.Format.Format_RGB32,
    ):
        return
    width, height = mask.width(), mask.height()
    if width == 0 or height == 0:
        return
    coverage = _coverage_array(mask)
    bits = mask.bits()
    if hasattr(bits, "setsize"):
        bits.setsize(mask.bytesPerLine() * height)
    rows = np.frombuffer(bits, dtype=np.uint8).reshape(height, mask.bytesPerLine())
    channels = rows[:, : width * 4].reshape(height, width, 4)
    channels[:, :, 0] = coverage
    channels[:, :, 1] = coverage
    channels[:, :, 2] = coverage
    channels[:, :, 3] = 255


def coverage_to_mask_image(coverage: QImage | np.ndarray) -> QImage:
    """Build a canonical mask image from coverage values.

    *coverage* is either a ``Format_Alpha8`` QImage (any other format is
    reduced through :func:`mask_coverage_alpha8` first) or a (h, w) uint8
    numpy array.
    """

    if isinstance(coverage, QImage):
        alpha8 = mask_coverage_alpha8(coverage)
        if alpha8.format() != QImage.Format.Format_Alpha8:
            alpha8 = alpha8.convertToFormat(QImage.Format.Format_Alpha8)
        width = alpha8.width()
        height = alpha8.height()
        bits = alpha8.constBits()
        if hasattr(bits, "setsize"):
            bits.setsize(alpha8.bytesPerLine() * height)
        rows = np.frombuffer(bits, dtype=np.uint8, count=alpha8.bytesPerLine() * height)
        values = rows.reshape(height, alpha8.bytesPerLine())[:, :width]
    else:
        values = np.asarray(coverage, dtype=np.uint8)
        height, width = values.shape

    mask_data = np.empty((height, width, 4), dtype=np.uint8)
    mask_data[:, :, 0] = values
    mask_data[:, :, 1] = values
    mask_data[:, :, 2] = values
    mask_data[:, :, 3] = 255
    return QImage(
        mask_data.data,
        width,
        height,
        width * 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    ).copy()


def alpha8_snapshot_rgba(mask: QImage) -> np.ndarray:
    """Encode pure coverage in the existing RGBA snapshot representation.

    White RGB with coverage in alpha survives premultiplication and keeps
    legacy readers which inspect alpha compatible. A normal Alpha8-to-RGBA
    conversion yields black RGB, incorrectly hiding every covered pixel.
    """
    if mask.format() != QImage.Format.Format_Alpha8:
        raise ValueError("snapshot conversion requires an Alpha8 coverage mask")
    rows = np.frombuffer(mask.constBits(), np.uint8).reshape(mask.height(), mask.bytesPerLine())
    result = np.full((mask.height(), mask.width(), 4), 255, np.uint8)
    result[:, :, 3] = rows[:, : mask.width()]
    return result


__all__ = [
    "alpha8_snapshot_rgba",
    "coverage_to_mask_image",
    "mask_coverage_alpha8",
    "normalize_mask_image",
]
