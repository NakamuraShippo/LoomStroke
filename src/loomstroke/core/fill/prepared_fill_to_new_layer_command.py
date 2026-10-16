"""Create a fill layer from worker-prepared spans as one undoable operation."""

from __future__ import annotations

from typing import Any

from loomstroke.core.selection_shapes.selection_mask_bounds import SelectionMaskBounds

from .commands_fill import FloodFillToNewLayerCommand, _SelectionClip
from .fill_context_snapshot import FillContextSnapshot


class PreparedFillToNewLayerCommand(FloodFillToNewLayerCommand):
    def __init__(
        self,
        snapshot: FillContextSnapshot,
        spans: list[tuple[int, int, int]],
        bounds: tuple[int, int, int, int],
        color: tuple[float, float, float, float],
    ) -> None:
        super().__init__(snapshot.target_id, 0, 0, color, 0, 0, layer_name="Fill")
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
