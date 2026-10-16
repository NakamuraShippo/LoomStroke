from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

logger = logging.getLogger(__name__)


class TipCache:
    """Cache brush tip images and scaled pixmaps for UI previews."""

    _MAX_IMAGES = 256
    _MAX_PIXMAPS = 512

    def __init__(self) -> None:
        self._image_cache: dict[str, QImage] = {}
        self._pixmap_cache: dict[tuple[str, int], QPixmap] = {}

    def load_tip_image(self, abs_path: str) -> QImage:
        if abs_path in self._image_cache:
            return self._image_cache[abs_path]
        if len(self._image_cache) >= self._MAX_IMAGES:
            # Evict oldest entry
            oldest = next(iter(self._image_cache))
            self._image_cache.pop(oldest, None)
        image = QImage(abs_path)
        if image.isNull():
            logger.warning("Tip image missing or invalid: %s", abs_path)
        self._image_cache[abs_path] = image
        return image

    def get_pixmap(self, abs_path: str, size: int) -> QPixmap:
        key = (abs_path, int(size))
        if key in self._pixmap_cache:
            return self._pixmap_cache[key]
        if len(self._pixmap_cache) >= self._MAX_PIXMAPS:
            oldest = next(iter(self._pixmap_cache))
            self._pixmap_cache.pop(oldest, None)
        image = self.load_tip_image(abs_path)
        if image.isNull():
            placeholder = QPixmap(int(size), int(size))
            placeholder.fill(Qt.GlobalColor.transparent)
            self._pixmap_cache[key] = placeholder
            return placeholder
        scaled = image.scaled(
            int(size),
            int(size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap = QPixmap.fromImage(scaled)
        self._pixmap_cache[key] = pixmap
        return pixmap

    def clear(self) -> None:
        self._image_cache.clear()
        self._pixmap_cache.clear()

    def invalidate(self, abs_path: str) -> None:
        self._image_cache.pop(abs_path, None)
        keys = [key for key in self._pixmap_cache if key[0] == abs_path]
        for key in keys:
            self._pixmap_cache.pop(key, None)
