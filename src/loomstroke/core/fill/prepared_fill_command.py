"""Publish detached fill spans through the existing bucket Command and history."""

from __future__ import annotations

from typing import Any

from loomstroke.core.selection_shapes.selection_mask_bounds import SelectionMaskBounds

from .commands_fill import FloodFillCommand, _SelectionClip
from .fill_context_snapshot import FillContextSnapshot


class PreparedFillCommand(FloodFillCommand):
    def __init__(
        self,
        snapshot: FillContextSnapshot,
        spans: list[tuple[int, int, int]],
        bounds: tuple[int, int, int, int],
        color: tuple[float, float, float, float],
    ) -> None:
        super().__init__(snapshot.target_id, 0, 0, color, 0, 0)
        self._snapshot: FillContextSnapshot | None = snapshot
        self._spans = list(spans)
        self._bounds = bounds
        self._published = False
        if snapshot.selection is not None:
            selection_bounds = SelectionMaskBounds.find(snapshot.selection)
            if selection_bounds is not None:
                self._selection_clip = _SelectionClip(snapshot.selection, selection_bounds)

    def apply(self, canvas: Any) -> bool:
        if self._snapshot is not None and not self._snapshot.matches(canvas):
            return False
        if not super().apply(canvas):
            return False
        self._published = True
        self._snapshot = None
        return True
