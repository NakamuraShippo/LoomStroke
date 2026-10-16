"""Thumbnail generation helper for :mod:`layer_panel`."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap


class LayerPanelThumbnailManager:
    """Generate and cache layer thumbnails for LayerPanel."""

    def __init__(self, panel: Any, thumbnail_size: int) -> None:
        self._panel = panel
        self._thumbnail_size = thumbnail_size

    def layer_thumbnail(self, layer: Any) -> QPixmap | None:
        panel = self._panel
        layer_id = getattr(layer, "id", "")
        tex_ver = getattr(layer, "texture_version", 0)
        cache_ver = getattr(panel, "_current_cache_version", 0)
        if layer_id:
            cached = panel._thumbnail_cache.get(layer_id)
            # Only key on texture_version — the global cache_ver increments on
            # every paint segment and would cause all thumbnails to regenerate.
            if cached is not None and cached[0] == tex_ver:
                return cast(QPixmap, cached[2])

        image = getattr(layer, "image", None)
        if isinstance(image, QImage) and not image.isNull():
            thumb = self.thumbnail_from_qimage(image, self._thumbnail_size)
        else:
            data = None
            if hasattr(layer, "get_image_data"):
                try:
                    data = layer.get_image_data(dtype=np.uint8)
                except Exception:
                    data = None
            if data is None and hasattr(layer, "buffer"):
                data = layer.buffer
            if data is None:
                return None
            qimage = self.qimage_from_array(data)
            if qimage is None:
                return None
            thumb = self.thumbnail_from_qimage(qimage, self._thumbnail_size)

        if thumb is not None and layer_id:
            panel._thumbnail_cache[layer_id] = (tex_ver, cache_ver, thumb)
        return thumb

    @staticmethod
    def qimage_from_array(data: Any) -> QImage | None:
        try:
            arr = np.asarray(data)
        except Exception:
            return None
        if arr.ndim != 3 or arr.shape[2] not in (3, 4):
            return None
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        height, width = arr.shape[:2]
        if arr.shape[2] == 3:
            alpha = np.full((height, width, 1), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=2)
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr)
        qimage = QImage(
            arr.data,
            width,
            height,
            arr.strides[0],
            QImage.Format.Format_RGBA8888,
        )
        return qimage.copy()

    @staticmethod
    def thumbnail_from_qimage(image: QImage, thumbnail_size: int) -> QPixmap | None:
        if image.isNull():
            return None
        scaled = image.scaled(
            thumbnail_size,
            thumbnail_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        return QPixmap.fromImage(scaled)
