"""Prepared symmetry coverage with the existing RGB/native pattern history contract."""

from loomstroke.core.fill.commands_fill import PatternFillCommand, _SelectionClip
from loomstroke.core.selection_shapes.selection_mask_bounds import SelectionMaskBounds


class PreparedPatternFillCommand(PatternFillCommand):
    def __init__(self, snapshot, spans, bounds, pattern, scale):
        super().__init__(snapshot.target_id, 0, 0, pattern, 0, 0, pattern_scale=scale)
        self._spans = list(spans)
        self._bounds = bounds
        self._snapshot = snapshot
        if snapshot.selection is not None:
            selection_bounds = SelectionMaskBounds.find(snapshot.selection)
            if selection_bounds is not None:
                self._selection_clip = _SelectionClip(snapshot.selection, selection_bounds)

    def apply(self, canvas):
        if self._snapshot is not None and not self._snapshot.matches(canvas):
            return False
        if not super().apply(canvas):
            return False
        self._snapshot = None
        return True
