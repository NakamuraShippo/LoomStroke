"""History adapter for shared color edits, keeping the existing color command."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command, SetActiveColorCommand


class ColorEditCommand(Command):
    new_object = None
    _layer_id = "__loomstroke_mcp_shared_color__"

    def __init__(self, host: Any, color: Any) -> None:
        super().__init__()
        self._host = host
        self._command = SetActiveColorCommand(
            host.color_state, color, event_bus=host.events, source="mcp"
        )

    def _persist(self) -> None:
        self._host.settings.set("color/state", self._host.color_state.to_dict())

    def apply(self, canvas: Any) -> bool:
        result = self._command.apply(canvas)
        self._persist()
        return result

    def revert(self, canvas: Any) -> bool:
        result = self._command.revert(canvas)
        self._persist()
        return result
