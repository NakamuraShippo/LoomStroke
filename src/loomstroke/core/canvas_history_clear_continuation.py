"""Keep a pending history clear on its original runtime owner after finalize."""

from __future__ import annotations

from typing import Any

_MISSING = object()
_CANVAS_REFERENCES = ("_doc", "_undo_stack", "_redo_stack", "_history_service")


class _HistoryClearSuperseded(RuntimeError):
    """Stop the old replacement before it resets a newly active owner's state."""


def _stored_state(owner: Any) -> dict[str, Any]:
    """Read only stored fields, without asking a custom canvas to create any."""
    try:
        return vars(owner)
    except TypeError:
        return {}


class _HistoryClearContinuation:
    """A call-local guard; missing lightweight-canvas metadata stays optional.

    Finalization may legitimately add a group to this owner's existing stacks.
    Only an ownership/document boundary invalidates the pending clear. The
    generation also detects a move away and back to the same stored objects.
    """

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas
        state = _stored_state(canvas)
        self._references = {name: state[name] for name in _CANVAS_REFERENCES if name in state}
        self._history = state.get("_history_service")
        if self._history is None:
            # Lightweight canvases can construct their first service in finalize.
            self._references.pop("_history_service", None)
        history_state = _stored_state(self._history)
        self._owner = history_state.get("_restore_owner", _MISSING)
        self._generation = history_state.get("_restore_generation", _MISSING)

    def is_current(self) -> bool:
        state = _stored_state(self._canvas)
        if any(state.get(name, _MISSING) is not value for name, value in self._references.items()):
            return False
        history_state = _stored_state(self._history)
        return (
            self._owner is _MISSING or history_state.get("_restore_owner", _MISSING) is self._owner
        ) and (
            self._generation is _MISSING
            or history_state.get("_restore_generation", _MISSING) == self._generation
        )
