"""Text-tool vector deletion with tree-aware lookup and ordered default Undo."""

from __future__ import annotations

from contextlib import suppress
from inspect import getattr_static
from typing import Any

from loomstroke.core.canvas_service_simple import (
    _DEFAULT_ADD_VECTOR_OBJECT,
    SimpleCanvasService,
)
from loomstroke.core.command_layer_access import _resolve_layer_by_id
from loomstroke.plugins.vector_tools.common import VectorObject

_MISSING = object()


def _layer_objects(canvas: Any, layer_id: str) -> Any:
    snapshot = getattr(canvas, "get_vector_state_snapshot", None)
    if callable(snapshot):
        layer = snapshot().layer(layer_id)
        return layer.vector_objects if layer is not None else ()
    layer = _resolve_layer_by_id(canvas, layer_id)
    return getattr(layer, "vector_objects", ()) if layer is not None else ()


class _VectorDeleteCommand:
    """Keep the existing private Command shape and custom-canvas call contract."""

    def __init__(self, vector_object: VectorObject, *, layer_id: str) -> None:
        self.vector_object = vector_object
        self.layer_id = layer_id
        self._insert_index: int | None = None

    def apply(self, canvas: Any) -> bool:
        for index, obj in enumerate(_layer_objects(canvas, self.layer_id)):
            if getattr(obj, "id", None) != self.vector_object.id:
                continue
            self._insert_index = index
            remove = getattr(canvas, "remove_vector_object", None)
            return bool(
                callable(remove)
                and remove(
                    self.layer_id,
                    index,
                    recompose=True,
                    vec_id=self.vector_object.id,
                )
            )
        return False

    def revert(self, canvas: Any) -> bool:
        if (
            isinstance(canvas, SimpleCanvasService)
            and getattr_static(canvas, "add_vector_object", None) is _DEFAULT_ADD_VECTOR_OBJECT
        ):
            return self._restore_standard(canvas)
        add = getattr(canvas, "add_vector_object", None)
        if callable(add):
            return add(self.vector_object, layer_id=self.layer_id, recompose=True) is not None
        return False

    def _restore_standard(self, canvas: SimpleCanvasService) -> bool:
        # Public add appends and can redirect a missing target to the active
        # layer. Undo owns one exact target: never create or borrow another.
        document = getattr(canvas, "_doc", _MISSING)
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if layer is None or layer.layer_type != "vector" or self._insert_index is None:
            return False
        objects = layer.vector_objects
        if type(objects) is not list or any(
            getattr(obj, "id", None) == self.vector_object.id for obj in objects
        ):
            return False

        def current() -> bool:
            return (
                getattr(canvas, "_doc", _MISSING) is document
                and _resolve_layer_by_id(canvas, self.layer_id) is layer
                and layer.vector_objects is objects
            )

        if not current():
            return False
        index = max(0, min(self._insert_index, len(objects)))
        objects.insert(index, self.vector_object)
        # Restore order before any derived story update or redraw can observe
        # the object. There is no append-then-move tail after a callback.
        from loomstroke.core.japanese_text.story_geometry import (  # noqa: PLC0415
            refresh_story_geometry,
        )

        refresh_story_geometry(canvas, [self.vector_object])
        if not current():
            return False
        if isinstance(getattr(self.vector_object, "meta", None), dict):
            with suppress(Exception):
                self.vector_object.meta.setdefault("layer_id", layer.id)
        canvas._recompose()
        return current()
