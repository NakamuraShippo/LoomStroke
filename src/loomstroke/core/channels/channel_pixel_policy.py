"""Channel edits on premultiplied QImages without changing unselected bytes."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PySide6.QtGui import QImage


class ChannelPixelPolicy:
    """Keep colour-component selection separate from transparency."""

    @staticmethod
    def pixels(image: QImage, *, straight: bool = False) -> np.ndarray:
        fmt = (
            QImage.Format.Format_RGBA8888 if straight else QImage.Format.Format_ARGB32_Premultiplied
        )
        converted = image.convertToFormat(fmt)
        rows = np.frombuffer(converted.constBits(), dtype=np.uint8).reshape(
            converted.height(), converted.bytesPerLine()
        )
        return (
            rows[:, : converted.width() * 4]
            .reshape(converted.height(), converted.width(), 4)
            .copy()
        )

    @staticmethod
    def image(pixels: np.ndarray, *, straight: bool = False) -> QImage:
        data = np.ascontiguousarray(pixels, dtype=np.uint8)
        height, width, channels = data.shape
        if channels != 4 or not width or not height:
            raise ValueError("RGBA/BGRA pixels must have four channels and nonzero dimensions")
        fmt = (
            QImage.Format.Format_RGBA8888 if straight else QImage.Format.Format_ARGB32_Premultiplied
        )
        return QImage(data.data, width, height, width * 4, fmt).copy()

    @classmethod
    def merge(
        cls, before: QImage, after: QImage, channels: Sequence[int], *, erase: bool = False
    ) -> QImage:
        """Use selected R/G/B results and retain the original premultiplied alpha."""
        selected = tuple(channels)
        if not selected or any(c not in (0, 1, 2) for c in selected):
            raise ValueError("Select at least one RGB component")
        if before.size() != after.size() or before.isNull():
            raise ValueError("Channel patches must have matching nonzero dimensions")
        result = cls.pixels(before)
        source = cls.pixels(after, straight=not erase)
        for channel in selected:
            bgra = 2 - channel
            if erase:
                result[:, :, bgra] = np.minimum(source[:, :, bgra], result[:, :, 3])
            else:
                value = source[:, :, channel].astype(np.uint16)
                alpha = result[:, :, 3].astype(np.uint16)
                result[:, :, bgra] = ((value * alpha + 127) // 255).astype(np.uint8)
        return cls.image(result)

    @classmethod
    def replace_alpha(cls, image: QImage, coverage: np.ndarray) -> QImage:
        straight = cls.pixels(image, straight=True)
        if coverage.shape != straight.shape[:2] or coverage.dtype != np.uint8:
            raise ValueError("Alpha coverage must be matching uint8 pixels")
        straight[:, :, 3] = coverage
        return cls.image(straight, straight=True).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
