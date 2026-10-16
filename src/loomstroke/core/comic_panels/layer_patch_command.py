"""Guarded updates to prepared comic parts using ordinary layer attributes."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.command_types import Command

from .models import PanelFrameError

_FIELDS = frozenset(
    {
        "vector_objects",
        "metadata",
        "layer_mask",
        "effect_mask",
        "mask_enabled",
        "transform",
        "image",
        "layer_type",
        "offset_x",
        "offset_y",
        "visible",
        "opacity",
        "locked",
        "vector_kind",
    }
)


def _copy(value: Any) -> Any:
    # A separate QImage handle preserves the snapshot by implicit sharing;
    # later fill/painting/bits access detaches before modifying its storage.
    return QImage(value) if isinstance(value, QImage) else copy.deepcopy(value)


class PanelLayerPatchCommand(Command):
    """Compare source snapshots before installing all attribute replacements."""

    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self, layer_id: str, *, before: Mapping[str, Any], after: Mapping[str, Any]
    ) -> None:
        super().__init__()
        if not layer_id or not before or set(before) != set(after) or not set(before) <= _FIELDS:
            raise PanelFrameError("invalid prepared layer patch")
        self.layer_id = layer_id
        self.before = {key: _copy(value) for key, value in before.items()}
        self.after = {key: _copy(value) for key, value in after.items()}
        self.last_error = ""

    def _install(self, canvas: Any, expected: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
        layer = canvas._get_layer_by_id(self.layer_id)
        if layer is None or any(getattr(layer, key) != value for key, value in expected.items()):
            self.last_error = "comic part changed during preparation"
            return False
        try:
            for key, value in values.items():
                setattr(layer, key, _copy(value))
            canvas.bump_layer_texture_versions([layer.id])
            canvas._invalidate_layer_cache()
        except Exception:
            for key, value in expected.items():
                setattr(layer, key, _copy(value))
            canvas._invalidate_layer_cache()
            raise
        return True

    def apply(self, canvas: Any) -> bool:
        self._executed = self._install(canvas, self.before, self.after)
        return self._executed

    def revert(self, canvas: Any) -> bool:
        if not self._executed or not self._install(canvas, self.after, self.before):
            return False
        self._executed = False
        return True
