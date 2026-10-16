"""Use bounded mask reduction while retaining the existing selection history."""

from loomstroke.core.commands import ApplySelectionMaskCommand

from .selection_mask_bounds import SelectionMaskBounds


class ShapeSelectionMaskCommand(ApplySelectionMaskCommand):
    @staticmethod
    def _mask_bounds(mask):
        return SelectionMaskBounds.find(mask)
