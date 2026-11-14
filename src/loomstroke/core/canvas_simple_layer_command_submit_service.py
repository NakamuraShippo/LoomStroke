"""Simple layer command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command

from .applied_layer_history import AppliedLayerHistoryAttempt
from .commands_layer_rename import _is_standard_rename_command
from .layer_rename_submit import _submit_rename_command


class CanvasSimpleLayerCommandSubmitService:
    """Submit layer commands that only need history and recompose post-processing."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, label: str) -> bool:
        if _is_standard_rename_command(cmd):
            return _submit_rename_command(self._owner, cmd, label)
        attempt = AppliedLayerHistoryAttempt(self._owner)
        result = cmd.apply(self._owner)
        if not result:
            return False

        if not attempt.record(cmd, label, confirmed=result is True):
            return False
        self._owner._recompose()
        return True
