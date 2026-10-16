"""Selection-change command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command


class CanvasSelectionChangeSubmitService:
    """Submit commands that change selection and share the same post-processing."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, label: str) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._record_history_command(label, cmd)
        self._owner._recompose()
        self._owner._emit_selection_changed()
        return True
