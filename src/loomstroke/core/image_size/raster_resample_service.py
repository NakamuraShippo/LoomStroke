"""Pillow-backed resampling kernels for canvas rasters and masks (CT-13 R2).

Layer buffers are premultiplied ARGB32.  They are handed to Pillow in its
premultiplied ``RGBa`` mode, so every kernel (nearest ... lanczos) filters
premultiplied channels and transparent pixels cannot bleed dark or white
fringes into their neighbours (spec §10.1, §17).  Coverage masks go through
the canonical ``min(luminance, alpha)`` extraction and come back in the
canonical grayscale form, so legacy alpha-encoded masks are normalized in
passing instead of being resampled channel by channel.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
from PIL import Image
from PySide6.QtGui import QImage

from loomstroke.core.mask_coverage import coverage_to_mask_image, mask_coverage_alpha8

from .interpolation import InterpolationMode


class RasterResampleService:
    """Resample premultiplied rasters, coverage masks and Alpha8 selections."""

    _FILTERS: ClassVar[dict[InterpolationMode, Image.Resampling]] = {
        InterpolationMode.NEAREST: Image.Resampling.NEAREST,
        InterpolationMode.BILINEAR: Image.Resampling.BILINEAR,
        InterpolationMode.BICUBIC: Image.Resampling.BICUBIC,
        InterpolationMode.LANCZOS: Image.Resampling.LANCZOS,
        InterpolationMode.HAMMING: Image.Resampling.HAMMING,
        InterpolationMode.BOX: Image.Resampling.BOX,
    }

    def resample_premultiplied(
        self,
        image: QImage,
        width: int,
        height: int,
        mode: InterpolationMode,
    ) -> QImage:
        """Return *image* scaled to ``width`` x ``height`` as ARGB32 premultiplied."""
        target_w, target_h = self._target_size(width, height)
        source = image
        if source.format() != QImage.Format.Format_RGBA8888_Premultiplied:
            source = source.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        data = self._packed_array(source, 4).tobytes()
        pil = Image.frombuffer("RGBa", (source.width(), source.height()), data, "raw", "RGBa", 0, 1)
        resized = pil.resize((target_w, target_h), resample=self._filter(mode))
        raw = resized.tobytes()
        result = QImage(
            raw,
            target_w,
            target_h,
            target_w * 4,
            QImage.Format.Format_RGBA8888_Premultiplied,
        ).copy()
        return result.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def resample_coverage_mask(
        self,
        mask: QImage,
        width: int,
        height: int,
        mode: InterpolationMode,
    ) -> QImage:
        """Resample a layer / effect mask; the result is the canonical gray form."""
        coverage = self.resample_alpha8(mask_coverage_alpha8(mask), width, height, mode)
        return coverage_to_mask_image(self._packed_array(coverage, 1))

    def resample_alpha8(
        self,
        mask: QImage,
        width: int,
        height: int,
        mode: InterpolationMode,
    ) -> QImage:
        """Resample an 8-bit coverage / selection mask, returning ``Format_Alpha8``."""
        target_w, target_h = self._target_size(width, height)
        source = mask
        if source.format() != QImage.Format.Format_Alpha8:
            source = source.convertToFormat(QImage.Format.Format_Alpha8)
        data = self._packed_array(source, 1).tobytes()
        pil = Image.frombuffer("L", (source.width(), source.height()), data, "raw", "L", 0, 1)
        resized = pil.resize((target_w, target_h), resample=self._filter(mode))
        raw = resized.tobytes()
        return QImage(raw, target_w, target_h, target_w, QImage.Format.Format_Alpha8).copy()

    # -- helpers --------------------------------------------------------------

    def _filter(self, mode: InterpolationMode) -> Image.Resampling:
        normalized = InterpolationMode.normalize(mode)
        if normalized is None:
            raise ValueError(f"unsupported interpolation mode: {mode!r}")
        return self._FILTERS[normalized]

    @staticmethod
    def _target_size(width: int, height: int) -> tuple[int, int]:
        target_w = int(width)
        target_h = int(height)
        if target_w < 1 or target_h < 1:
            raise ValueError(f"resample target must be positive, got {target_w}x{target_h}")
        return target_w, target_h

    @staticmethod
    def _packed_array(image: QImage, channels: int) -> np.ndarray:
        """Return the pixel rows of *image* without scanline padding (owned copy)."""
        width = image.width()
        height = image.height()
        bytes_per_line = image.bytesPerLine()
        bits = image.constBits()
        if hasattr(bits, "setsize"):
            bits.setsize(bytes_per_line * height)
        rows = np.frombuffer(bits, dtype=np.uint8, count=bytes_per_line * height)
        packed = rows.reshape(height, bytes_per_line)[:, : width * channels]
        if channels > 1:
            packed = packed.reshape(height, width, channels)
        return np.ascontiguousarray(packed)
