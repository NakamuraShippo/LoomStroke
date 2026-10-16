"""Undo adapter preserving the renderer's ordinary filter-mask contract."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.commands import Command
from loomstroke.core.commands_layer_tree import _set_layer_mask_state


class FilterMaskCommand(Command):
    def __init__(self, filter_id: str, mask: QImage | None, enabled: bool) -> None:
        super().__init__()
        self.filter_id = filter_id
        self.mask = QImage(mask) if mask is not None else None
        self.enabled = enabled
        self.before: tuple[Any, Any, bool] | None = None

    @property
    def effect_id(self) -> str:
        return self.filter_id

    @effect_id.setter
    def effect_id(self, value: str) -> None:
        self.filter_id = value

    def apply(self, canvas: Any) -> bool:
        layer = canvas._get_layer_by_id(self.filter_id)
        if layer is None or layer.layer_type != "effect":
            return False
        self.before = (
            QImage(layer.layer_mask) if layer.layer_mask is not None else None,
            QImage(layer.filter_mask) if layer.filter_mask is not None else None,
            layer.mask_enabled,
        )
        self._set(canvas, layer, self.mask, self.mask, self.enabled)
        return True

    def revert(self, canvas: Any) -> bool:
        layer = canvas._get_layer_by_id(self.filter_id)
        if self.before is None or layer is None:
            return False
        self._set(canvas, layer, *self.before)
        return True

    @staticmethod
    def _set(canvas: Any, layer: Any, paint_mask: Any, filter_mask: Any, enabled: bool) -> None:
        layer.filter_mask = QImage(filter_mask) if filter_mask is not None else None
        layer.filter_mask_version += 1
        canvas._clear_filter_cache(layer)
        _set_layer_mask_state(
            canvas,
            layer,
            QImage(paint_mask) if paint_mask is not None else None,
            enabled,
            change_type="effect_mask",  # Legacy document event contract.
        )


EffectMaskCommand = FilterMaskCommand
