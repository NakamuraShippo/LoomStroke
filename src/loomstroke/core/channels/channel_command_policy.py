"""Reject pixel operations that cannot yet isolate a colour component."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import (
    CutSelectionCommand,
    PasteSelectionCommand,
    TransformSelectionCommand,
)


class ChannelCommandPolicy:
    @staticmethod
    def allows(canvas: Any, command: Any) -> bool:
        channels = getattr(canvas, "_channels_service", None)
        if channels is None:
            return True
        layer = canvas._get_active_layer()
        if channels.selected == (0, 1, 2) and not channels.is_alpha(layer):
            return True
        return not isinstance(
            command, (CutSelectionCommand, PasteSelectionCommand, TransformSelectionCommand)
        )
