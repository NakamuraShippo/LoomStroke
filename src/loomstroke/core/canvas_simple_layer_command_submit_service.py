"""Simple layer command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command


class CanvasSimpleLayerCommandSubmitService:
    """Submit layer commands that only need history and recompose post-processing."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, label: str) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._record_history_command(label, cmd)
        self._owner._recompose()
        return True
