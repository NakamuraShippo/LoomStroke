"""Read / patch the assist key inside a page ``view_state`` without touching other keys."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .drawing_assist_state import DrawingAssistState

__all__ = ["DrawingAssistViewStateCodec"]


class DrawingAssistViewStateCodec:
    KEY = DrawingAssistState.VIEW_STATE_KEY

    def read(self, view_state: Mapping[str, Any] | None) -> DrawingAssistState:
        if not isinstance(view_state, Mapping):
            return DrawingAssistState()
        return DrawingAssistState.from_dict(view_state.get(self.KEY))

    def payload(self, state: DrawingAssistState) -> dict[str, Any] | None:
        """Value to store under the key; ``None`` when nothing is left to store."""
        if state.is_empty and not state.extra:
            return None
        return state.to_dict()

    def write(
        self, view_state: Mapping[str, Any] | None, state: DrawingAssistState
    ) -> dict[str, Any]:
        merged = dict(view_state) if isinstance(view_state, Mapping) else {}
        payload = self.payload(state)
        if payload is None:
            merged.pop(self.KEY, None)
        else:
            merged[self.KEY] = payload
        return merged
