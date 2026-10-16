"""Layer content mutation helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerContentMutationService:
    """Coordinate non-history layer content mutations."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def clear_layer(self, layer_id: str | None = None) -> None:
        """Clear a layer or the active layer."""

        target_layer = None

        if layer_id:
            # Root-level lookup only; pinned by
            # test_clear_layer_explicit_id_preserves_top_level_only_lookup.
            for layer in self._owner._layers:
                if layer.id == layer_id:
                    target_layer = layer
                    break
        elif self._owner._layers:
            # The active layer, not the root entry at _active_index -- for a
            # nested active layer the index addresses its ancestor group, so
            # indexing here would write pixels into the group instead.
            target_layer = self._owner._get_active_layer()

        if not target_layer:
            return
        if target_layer.locked:
            logger.info("Layer %s is locked; clear skipped", target_layer.name)
            return
        if target_layer.layer_type == "vector":
            target_layer.vector_objects.clear()
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
            logger.info("Vector layer cleared: %s", target_layer.name)
            return
        self._clear_image(target_layer)

    def _clear_image(self, layer: Any) -> None:
        if getattr(layer, "native_cmyk", None) is not None:
            self._owner.native_cmyk().clear(layer.id)
        elif layer.image:
            layer.image.fill(Qt.GlobalColor.transparent)
            self._owner._recompose()
            logger.info(f"Layer cleared: {layer.name}")
