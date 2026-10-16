"""Read-only layer metadata query helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loomstroke.host_api import LayerRef

from .commands import BlendMode


class CanvasLayerQueryService:
    """Expose lightweight layer metadata without mutating canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_layers(self) -> list[LayerRef]:
        return [
            LayerRef(
                id=layer.id,
                name=layer.name,
                layer_type=layer.layer_type,
                vector_kind=layer.vector_kind,
            )
            for layer in self._owner._layers
        ]

    @staticmethod
    def collect_visible_content_layers(layers: Sequence[Any]) -> list[tuple[int, Any]]:
        """Return visible layers at this level that contribute composited content.

        A group carries no image of its own, so it qualifies through its
        descendants; leaving it out drops everything inside it from
        merge/flatten while the canvas still shows it.
        """

        visible: list[tuple[int, Any]] = []
        for i, layer in enumerate(layers):
            if not layer.visible:
                continue
            if CanvasLayerQueryService._contributes_content(layer):
                visible.append((i, layer))
        return visible

    @staticmethod
    def _contributes_content(layer: Any) -> bool:
        if layer.layer_type == "effect":
            return bool(layer.filter_type)
        if layer.image is not None or layer.vector_objects:
            return True
        children = getattr(layer, "children", None) or []
        return any(
            child.visible and CanvasLayerQueryService._contributes_content(child)
            for child in children
        )

    def get_layer_blend_mode(self, layer_id: str) -> str:
        for layer in self._owner._layers:
            if layer.id == layer_id:
                return str(layer.blend_mode.value)
        return str(BlendMode.NORMAL.value)

    def get_layer_info(self, layer_id: str) -> dict[str, Any] | None:
        for layer in self._owner._layers:
            if layer.id == layer_id:
                return {
                    "id": layer.id,
                    "name": layer.name,
                    "visible": layer.visible,
                    "opacity": layer.opacity,
                    "blend_mode": str(layer.blend_mode.value),
                    "locked": layer.locked,
                    "alpha_lock": layer.alpha_lock,
                    "layer_type": layer.layer_type,
                }
        return None

    def get_vector_objects(self, *, include_hidden: bool = False) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        self._collect_vector_objects(self._owner._layers, result, include_hidden)
        return result

    def _collect_vector_objects(
        self,
        layers: list[Any],
        result: list[tuple[str, Any]],
        include_hidden: bool,
    ) -> None:
        for layer in layers:
            if not include_hidden and not layer.visible:
                continue
            if layer.layer_type == "vector":
                for obj in layer.vector_objects:
                    result.append((layer.id, obj))
            if layer.children:
                self._collect_vector_objects(layer.children, result, include_hidden)
