"""Diagnostic CPU compositing helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from loomstroke.core.canvas_composited_pixmap_state_service import (
    CanvasCompositedPixmapStateService,
)
from loomstroke.core.canvas_layer_query_service import CanvasLayerQueryService


class CanvasCpuCompositeService:
    """Build CPU-composited pixmaps for the opt-in diagnostic canvas path."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_cpu_composited_pixmap(self) -> QPixmap:
        """Return a CPU-composited layer stack for diagnostic rendering."""

        owner = self._owner
        visible = CanvasLayerQueryService.collect_visible_content_layers(owner._layers)

        if visible:
            result_img = owner._composite_visible(visible)
        else:
            result_img = QImage(
                owner._width,
                owner._height,
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            result_img.fill(Qt.GlobalColor.transparent)

        pixmap = QPixmap.fromImage(result_img)
        return CanvasCompositedPixmapStateService.store_composited_pixmap(owner, pixmap)
