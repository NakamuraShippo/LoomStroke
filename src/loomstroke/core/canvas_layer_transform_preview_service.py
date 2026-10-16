"""Runtime layer transform preview helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any


class CanvasLayerTransformPreviewService:
    """Coordinate non-persistent layer transform preview mutations."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def set_layer_transform_preview(self, layer_id: str, transform: Any) -> bool:
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False
        layer._preview_transform = transform
        layer._preview_transform_version += 1
        return True

    def clear_layer_transform_preview(self, layer_id: str) -> bool:
        layer = self._owner._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False
        if layer._preview_transform is None:
            return True
        layer._preview_transform = None
        layer._preview_transform_version += 1
        return True
