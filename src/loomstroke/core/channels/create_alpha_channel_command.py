"""Create a named alpha mask using the existing layer persistence contract."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.command_types import Command
from loomstroke.core.events import Events


class CreateAlphaChannelCommand(Command):
    def __init__(self, name: str, image: QImage) -> None:
        super().__init__()
        self.name = name.strip()
        self.image = image.copy()
        self.layer_id: str | None = None
        self._layer: Any = None
        self._position = 0

    def apply(self, canvas: Any) -> bool:
        if not self.name or self.image.isNull():
            return False
        active = canvas.current_layer().id
        if self._layer is None:
            ref = canvas.add_layer(self.name, layer_type="mask")
            if ref is None:
                return False
            self.layer_id = ref.id
            self._layer = canvas._get_layer_by_id(ref.id)
            self._layer.image = self.image.copy()
            self._layer.metadata = {"loomstroke.channels.alpha": {"version": 1}}
            self._position = canvas._layers.index(self._layer)
        else:
            canvas._layers.insert(self._position, self._layer)
        canvas.set_active_layer(active)
        canvas._invalidate_layer_cache()
        canvas._recompose()
        canvas._publish_layer_event(Events.LAYER_CHANGED, layer=self._layer, change_type="restored")
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        return bool(self.layer_id and canvas.remove_layer(self.layer_id))
