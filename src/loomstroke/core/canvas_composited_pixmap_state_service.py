"""Shared composited pixmap cache state helpers."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QPixmap


class CanvasCompositedPixmapStateService:
    """Update the cached composited pixmap while preserving GC guard order."""

    @staticmethod
    def store_composited_pixmap(owner: Any, pixmap: QPixmap) -> QPixmap:
        owner._prev_composited = owner._composited
        owner._composited = pixmap
        return pixmap

    @staticmethod
    def make_filled_pixmap(width: int, height: int, fill: Any) -> QPixmap:
        pixmap = QPixmap(width, height)
        pixmap.fill(fill)
        return pixmap

    @staticmethod
    def reset_composited_pixmap(
        owner: Any,
        width: int,
        height: int,
        fill: Any,
    ) -> QPixmap:
        pixmap = CanvasCompositedPixmapStateService.make_filled_pixmap(
            width,
            height,
            fill,
        )
        return CanvasCompositedPixmapStateService.store_composited_pixmap(owner, pixmap)
