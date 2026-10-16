"""ROI-limited QImage to straight RGBA conversion (RJS P0-02).

``WgpuTextureManager.sync_layer_texture`` keeps its cache-hit-before-
conversion fast path, but a texture-version miss used to convert the whole
layer image even when only a small dirty rect would be uploaded.  This
converter turns exactly the clipped ROI into straight-alpha RGBA8 so the
CPU conversion cost and the temporary ndarray allocation scale with the
edit, not with the layer (8K layers pay ~256 MB per edit otherwise).

Pixel oracle: the result must equal
``qimage_to_straight_rgba(full_image)[clipped_roi]`` — ``QImage.copy`` of
the ROI (an ROI-sized allocation, never a full-image copy) followed by the
same per-pixel ``Format_RGBA8888`` conversion guarantees this because the
conversion has no cross-pixel dependencies.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage


class WgpuQImageRegionConverter:
    """Convert a clipped QImage ROI into straight-alpha RGBA8 storage."""

    def convert_region(
        self,
        image: QImage,
        rect: QRect,
    ) -> tuple[np.ndarray, QRect] | None:
        """Convert only ``rect`` (clipped to the image bounds) to RGBA8.

        Returns ``(array, clipped_rect)`` where ``array`` has shape
        ``(clipped.height(), clipped.width(), 4)``, or ``None`` when the
        image is unusable or the clipped ROI is empty — callers fall back
        to the existing full conversion path in that case.
        """

        if image is None or image.isNull():
            return None
        clipped = QRect(rect).intersected(QRect(0, 0, image.width(), image.height()))
        if clipped.isEmpty():
            return None
        # ROI-sized allocation only; QImage.copy never touches pixels
        # outside the requested rectangle.
        roi_image = image.copy(clipped)
        if roi_image.isNull():
            return None
        converted = roi_image.convertToFormat(QImage.Format.Format_RGBA8888)
        if converted.isNull():
            return None
        width = converted.width()
        height = converted.height()
        if width != clipped.width() or height != clipped.height():
            return None
        row_bytes = converted.bytesPerLine()
        raw = np.frombuffer(converted.constBits(), dtype=np.uint8).reshape(height, row_bytes)
        array = raw[:, : width * 4].reshape(height, width, 4).copy()
        return array, clipped


__all__ = ("WgpuQImageRegionConverter",)
