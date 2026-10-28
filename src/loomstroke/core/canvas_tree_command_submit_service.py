"""Tree-structure command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from .layer_mask_submit import _submit_mask_command


class CanvasTreeCommandSubmitService:
    """Submit tree commands while preserving deferred recompose ordering."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Any) -> bool:
        mask_result = _submit_mask_command(self._owner, cmd)
        if mask_result is not None:
            return mask_result
        self._owner.defer_recompose()
        try:
            success = bool(cmd.apply(self._owner))
        finally:
            self._owner.resume_recompose()
        if not success:
            return False

        label = type(cmd).__name__.replace("Command", "")
        self._owner._record_history_command(label, cmd)
        return True
