"""One undoable layer for all copies placed along a path."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage

from .commands_layer_tree import AddLayerCommand


class PlaceObjectsAlongPathCommand(AddLayerCommand):
    def __init__(
        self,
        name: str,
        *,
        image: QImage | None = None,
        vector_objects: list[Any] | None = None,
        vector_kind: str = "draw",
        opacity: float = 1.0,
    ) -> None:
        if (image is None) == (vector_objects is None):
            raise ValueError("Exactly one layer payload is required")
        super().__init__(name, layer_type="raster" if image is not None else "vector")
        self._values = (
            {"image": image.copy()}
            if image is not None
            else {"vector_objects": list(vector_objects or []), "vector_kind": vector_kind}
        )
        self._values["opacity"] = opacity

    def apply(self, canvas: Any) -> bool:
        if not self._created.supports(canvas):
            return False
        succeeded = self._created.create(canvas, self._name, self._layer_type, self._values)
        if succeeded:
            self._layer_id = self._created.layer.id
            self._executed = True
        return succeeded
