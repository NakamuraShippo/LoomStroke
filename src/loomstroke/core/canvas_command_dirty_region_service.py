"""Command dirty-region notification helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect


class CanvasCommandDirtyRegionService:
    """Notify layer content changes from command dirty regions."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def notify_layer_dirty_region(
        self,
        dirty: Any | None,
        layer_id: str | None,
        command_name: str,
    ) -> None:
        """Emit a layer content event for a command dirty region."""

        if dirty and layer_id:
            layer = self._owner._get_layer_by_id(layer_id)
            if layer is not None:
                layer.texture_version += 1
                rect = QRect(dirty.x, dirty.y, dirty.width, dirty.height)
                self._owner._emit_layer_content_changed(
                    layer,
                    rect,
                    {"command": command_name},
                )
