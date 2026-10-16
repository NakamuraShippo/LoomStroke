"""Atomic pixel/selection history for existing clipboard and transform commands."""

from __future__ import annotations

from copy import deepcopy

from loomstroke.core.commands_selection_mask import (
    UpdateSelectionMaskCommand,
    _get_selection_full_bounds,
    _get_selection_full_mask,
    _set_selection_full_bounds,
    _set_selection_full_mask,
)

from .editing_common import invalid
from .layer_edit_command import LayerEditCommand


def _selection(canvas):
    mask, bounds = UpdateSelectionMaskCommand._read_selection(canvas)
    return (
        UpdateSelectionMaskCommand._clone_mask(mask),
        deepcopy(bounds),
        deepcopy(_get_selection_full_bounds(canvas)),
        UpdateSelectionMaskCommand._clone_mask(_get_selection_full_mask(canvas)),
    )


def _restore(canvas, state):
    mask, bounds, full_bounds, full_mask = state
    canvas.set_selection_mask(UpdateSelectionMaskCommand._clone_mask(mask), bounds)
    _set_selection_full_bounds(canvas, deepcopy(full_bounds))
    _set_selection_full_mask(canvas, UpdateSelectionMaskCommand._clone_mask(full_mask))
    canvas._emit_selection_changed()


class SelectionPixelEditCommand(LayerEditCommand):
    """Keep core command rendering, restore complete COW pixels and selection.

    Selection clipboard commands retain rectangular patches internally. A
    full COW layer snapshot also preserves pixels covered by a soft mask and
    placement changes caused by backing-image expansion, without duplicating
    the source image until a mutation actually detaches it.
    """

    def __init__(self, layer_id, command):
        super().__init__([layer_id], lambda canvas: self._perform(canvas, command))
        self.selection_before = None
        self.selection_after = None

    @staticmethod
    def _perform(canvas, command):
        if not command.apply(canvas):
            raise invalid("The selection pixel edit was rejected and rolled back.")
        return {}

    def apply(self, canvas):
        if self.selection_after is not None:
            _restore(canvas, self.selection_after)
        else:
            self.selection_before = _selection(canvas)
        try:
            changed = super().apply(canvas)
        except Exception:
            _restore(canvas, self.selection_before)
            raise
        self.selection_after = _selection(canvas)
        canvas._emit_selection_changed()
        return changed

    def revert(self, canvas):
        if self.selection_before is not None:
            _restore(canvas, self.selection_before)
        return super().revert(canvas)
