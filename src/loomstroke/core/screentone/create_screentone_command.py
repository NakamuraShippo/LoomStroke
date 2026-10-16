"""One undoable command that creates a screentone: carrier layer + child effect.

``AddLayerCommand`` mints a fresh layer id on every apply, so a transaction
of "add layer, paint carrier, add effect" cannot be redone (the effect would
target the id of the first run).  This command owns both layers instead and
re-inserts the very same objects on redo — the layer id stays stable across
undo / redo, the contract the rest of the pipeline (SSO cache, effect cache,
Pages panel) relies on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage

from loomstroke.core.command_types import Command


class CreateScreentoneCommand(Command):
    """Create (and on undo remove) a carrier raster layer with a tone effect child."""

    def __init__(
        self,
        *,
        name: str,
        carrier_rgba: NDArray[np.uint8],
        effect_type: str,
        effect_params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self._name = name
        self._carrier_rgba = np.ascontiguousarray(carrier_rgba)
        self._effect_type = effect_type
        self._effect_params = dict(effect_params or {})
        self._layer: Any | None = None
        self._layer_id: str | None = None
        self._effect_id: str | None = None
        self._position: int = -1

    @property
    def layer_id(self) -> str | None:
        return self._layer_id

    @property
    def effect_layer_id(self) -> str | None:
        return self._effect_id

    # -- Command ------------------------------------------------------------------

    def apply(self, canvas: Any) -> bool:
        if self._layer is not None:
            return self._reinsert(canvas)
        add_layer = getattr(canvas, "add_layer", None)
        find_layer = getattr(canvas, "_find_layer_by_id_tree", None)
        add_effect = getattr(canvas, "add_effect_layer", None)
        if not callable(add_layer) or not callable(find_layer) or not callable(add_effect):
            return False
        ref = add_layer(self._name)
        layer_id = getattr(ref, "id", None)
        layer = find_layer(layer_id) if layer_id else None
        if layer is None:
            return False
        if not self._paint_carrier(canvas, layer):
            self._remove(canvas, layer_id)
            return False
        effect_id = add_effect(layer_id, self._effect_type, dict(self._effect_params))
        if not effect_id:
            self._remove(canvas, layer_id)
            return False
        self._layer = layer
        self._layer_id = str(layer_id)
        self._effect_id = str(effect_id)
        self._position = self._root_position(canvas, layer)
        self._activate(canvas)
        return True

    def revert(self, canvas: Any) -> bool:
        if self._layer_id is None:
            return False
        self._position = self._root_position(canvas, self._layer)
        return self._remove(canvas, self._layer_id)

    # -- helpers ------------------------------------------------------------------

    def _paint_carrier(self, canvas: Any, layer: Any) -> bool:
        rgba = self._carrier_rgba
        if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
            return False
        height, width = rgba.shape[:2]
        if (int(getattr(canvas, "_width", width)), int(getattr(canvas, "_height", height))) != (
            width,
            height,
        ):
            return False
        image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
        layer.image = image.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        layer.offset_x = 0
        layer.offset_y = 0
        if hasattr(layer, "texture_version"):
            layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
        return True

    @staticmethod
    def _remove(canvas: Any, layer_id: Any) -> bool:
        remove = getattr(canvas, "remove_layer", None)
        if not callable(remove) or not layer_id:
            return False
        return bool(remove(layer_id))

    @staticmethod
    def _root_position(canvas: Any, layer: Any) -> int:
        layers = getattr(canvas, "_layers", None)
        if layers is None or layer is None:
            return -1
        for index, candidate in enumerate(layers):
            if candidate is layer:
                return index
        return -1

    def _reinsert(self, canvas: Any) -> bool:
        layers = getattr(canvas, "_layers", None)
        if layers is None or self._layer is None:
            return False
        if any(candidate is self._layer for candidate in layers):
            return True
        if self._position < 0 or self._position > len(layers):
            layers.append(self._layer)
        else:
            layers.insert(self._position, self._layer)
        invalidate = getattr(canvas, "_invalidate_layer_cache", None)
        if callable(invalidate):
            invalidate()
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()
        publish = getattr(canvas, "_publish_layer_event", None)
        if callable(publish):
            from loomstroke.core.events import Events  # noqa: PLC0415

            publish(Events.LAYER_CHANGED, layer=self._layer, change_type="restored")
        self._activate(canvas)
        return True

    def _activate(self, canvas: Any) -> None:
        set_active = getattr(canvas, "set_active_layer", None)
        if callable(set_active) and self._layer_id:
            try:
                set_active(self._layer_id)
            except Exception:  # pragma: no cover - defensive
                return
