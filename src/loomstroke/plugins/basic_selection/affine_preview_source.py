"""Prepare unchanged selection pixels once for an affine drag session."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.selection_clipboard import SelectionPayload


class AffinePreviewSource:
    """Cache masked pixels without changing the input images' cache keys."""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self._key: tuple[int, int] | None = None
        self._image: QImage | None = None
        self._mask: QImage | None = None
        self._masked: QImage | None = None

    def get(self, payload: SelectionPayload) -> tuple[QImage, QImage]:
        image, mask = payload.image, payload.mask
        key = (image.cacheKey(), mask.cacheKey())
        if self._key == key and self._masked is not None and self._mask is not None:
            return self._masked, self._mask

        source = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        coverage = mask.convertToFormat(QImage.Format.Format_Alpha8)
        mask_pixels = np.frombuffer(coverage.constBits(), np.uint8).reshape(
            coverage.height(), coverage.bytesPerLine()
        )[:, : coverage.width()]
        if source.size() == coverage.size() and np.all(mask_pixels == 255):
            result = source
        else:
            result = source.copy()
            height, width = result.height(), result.width()
            pixels = np.frombuffer(result.bits(), np.uint8).reshape(height, result.bytesPerLine())
            pixels = pixels[:, : width * 4].reshape(height, width, 4)
            rows, cols = min(height, coverage.height()), min(width, coverage.width())
            # The old float32 product truncated coverage/255. Integer products
            # preserve those bytes and bound temporary storage to 256 rows.
            for start in range(0, rows, 256):
                stop = min(start + 256, rows)
                product = np.multiply(
                    pixels[start:stop, :cols], mask_pixels[start:stop, :cols, None], dtype=np.uint16
                )
                product //= 255
                pixels[start:stop, :cols] = product
            pixels[rows:, :] = 0
            pixels[:, cols:] = 0

        self._key = key
        self._image = QImage(image)
        self._mask = coverage
        self._masked = result
        return result, coverage
