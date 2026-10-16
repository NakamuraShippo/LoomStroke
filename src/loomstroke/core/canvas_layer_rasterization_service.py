"""Layer rasterization helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QRect

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerRasterizationService:
    """Coordinate vector layer rasterization without owning canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def rasterize_layer(self, layer_id: str) -> bool:
        """Convert a vector layer into a raster layer in-place."""

        target_layer = self._owner._get_layer_by_id(layer_id)
        if target_layer is None:
            logger.warning("Layer not found for rasterize: %s", layer_id)
            return False
        if target_layer.locked:
            logger.info("Layer %s is locked; rasterize skipped", target_layer.name)
            return False
        if getattr(target_layer, "layer_type", "raster") != "vector":
            logger.debug("Layer %s is not vector; rasterize ignored", target_layer.name)
            return False

        target_layer.image = self._owner._rasterize_vector_layer(target_layer)
        target_layer.layer_type = "raster"
        target_layer.vector_objects.clear()
        target_layer.offset_x = 0
        target_layer.offset_y = 0
        target_layer.texture_version += 1

        if getattr(self._owner, "_vector_selection", None):
            try:
                if self._owner._vector_selection.get("layer_id") in (None, target_layer.id):
                    self._owner._vector_selection = None
            except Exception:
                self._owner._vector_selection = None
        if getattr(self._owner, "_vector_preview_layer_id", None) == target_layer.id:
            self._owner._vector_preview = None
            self._owner._vector_preview_layer_id = None

        self._owner._recompose()
        rect = QRect(0, 0, self._owner._width, self._owner._height)
        self._owner._emit_layer_content_changed(
            target_layer,
            rect,
            {"command": "rasterize"},
        )
        logger.info("Rasterized vector layer: %s", target_layer.name)
        return True
