"""Selection mask command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command, SelectionOp


class CanvasSelectionMaskSubmitService:
    """Submit commands that mutate the selection mask state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit_update(self, cmd: Command) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        if getattr(cmd, "source", None) != "preview":
            self._owner._record_selection_history(cmd)
        if (
            getattr(cmd, "rect", None) is None
            and getattr(cmd, "operation", None) == SelectionOp.REPLACE
        ):
            self._owner._recompose_now()
        else:
            self._owner._recompose()
        self._owner._emit_selection_changed()
        return True

    def submit_modify(self, cmd: Command) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._record_selection_snapshot_history(
            getattr(cmd, "_before", None),
            getattr(cmd, "_after", None),
        )
        self._owner._recompose()
        self._owner._emit_selection_changed()
        return True

    def submit_apply(self, cmd: Command) -> bool:
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        if getattr(cmd, "source", None) != "preview":
            self._owner._record_selection_snapshot_history(
                getattr(cmd, "_before", None),
                getattr(cmd, "_after", None),
            )
        self._owner._recompose()
        self._owner._emit_selection_changed()
        return True
