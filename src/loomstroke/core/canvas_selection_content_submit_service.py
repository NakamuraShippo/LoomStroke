"""Selection content command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command


class CanvasSelectionContentSubmitService:
    """Submit selection commands that mutate active layer content."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, label: str) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._bump_active_texture_version()
        self._owner._record_selection_command_history(cmd, label=label)
        self._owner._recompose()
        return True
