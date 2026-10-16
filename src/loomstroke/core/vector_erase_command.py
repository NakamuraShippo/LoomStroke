"""Atomically replace one layer's erased vector geometry on existing history."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .commands import Command
from .layer_tree_service import LayerTreeQueryService


class VectorEraseCommand(Command):
    def __init__(self, layer_id: str, expected: list[Any], result: list[Any]) -> None:
        super().__init__(merge_key=None)
        self._layer_id = layer_id
        self.new_object = None  # Existing vector-command routing and history.
        self.before = deepcopy(expected)
        self.after = deepcopy(result)

    def apply(self, canvas: Any) -> bool:
        layer = LayerTreeQueryService(canvas).find_layer_by_id_tree(self._layer_id)
        if (
            layer is None
            or layer.locked
            or layer.layer_type != "vector"
            or layer.vector_kind != "draw"
        ):
            return False
        if layer.vector_objects != self.before:
            return False
        self._replace(canvas, layer, self.after)
        return True

    def revert(self, canvas: Any) -> bool:
        layer = LayerTreeQueryService(canvas).find_layer_by_id_tree(self._layer_id)
        if layer is None:
            return False
        self._replace(canvas, layer, self.before)
        return True

    @staticmethod
    def _replace(canvas, layer, objects) -> None:
        cache = getattr(canvas, "_qpath_cache", {})
        for obj in layer.vector_objects:
            cache.pop(id(obj), None)
        layer.vector_objects = deepcopy(objects)
