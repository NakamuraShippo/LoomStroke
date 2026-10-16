"""Freeze component-limited command results before adding them to history."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect


class ChannelCommandCapture:
    def __init__(self, canvas: Any, layer: Any) -> None:
        self.canvas = canvas
        self.layer = layer
        self.origin = (layer.offset_x, layer.offset_y)
        self.rect = layer.image.rect()
        self.before = canvas.channels().capture(layer, self.rect)

    @classmethod
    def for_layer(cls, canvas: Any, layer_id: str | None = None) -> ChannelCommandCapture | None:
        if not hasattr(canvas, "channels"):
            return None
        layer = canvas._get_layer_by_id(layer_id) if layer_id else canvas._get_active_layer()
        if layer is None or layer.image is None or not canvas.channels().enabled(layer):
            return None
        return cls(canvas, layer)

    def record(self, command: Any, label: str) -> bool:
        layer = self.layer
        if layer.image.rect() != self.rect or (layer.offset_x, layer.offset_y) != self.origin:
            # Expansion changes the patch coordinate contract; retain the original image.
            command.revert(self.canvas)
            return False
        dirty = command.dirty_region
        canvas_rect = QRect(dirty.x, dirty.y, dirty.width, dirty.height)
        local = canvas_rect.translated(-self.origin[0], -self.origin[1]).intersected(self.rect)
        before = self.before.copy(local)
        self.canvas.channels().finish(layer, local, before)
        canvas_rect = QRect(local)
        canvas_rect.translate(*self.origin)
        self.canvas._record_history_patch(
            label,
            self.canvas._history_patch_from_images(
                layer.id, canvas_rect, before, layer.image.copy(local), {"command": "channel_edit"}
            ),
        )
        return True
