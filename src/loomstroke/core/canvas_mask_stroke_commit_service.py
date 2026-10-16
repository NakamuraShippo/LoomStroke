"""Deferred mask stroke commit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any


class CanvasMaskStrokeCommitService:
    """Coordinate mask stroke debounce and final commit side effects."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def schedule_mask_commit(self) -> None:
        """Restart the owner mask commit debounce timer."""

        owner = self._owner
        if not owner._mask_paint_dirty:
            layer = owner._get_active_layer()
            if layer is not None and layer.layer_mask is not None:
                owner._mask_paint_layer_id = layer.id
                if layer.layer_type == "effect":
                    owner._mask_paint_mask_snapshot = layer.layer_mask.copy()
        owner._mask_paint_dirty = True
        owner._mask_commit_timer.start()

    def commit_mask_paint(self) -> None:
        """Apply the final mask stroke recompose after debounce."""

        owner = self._owner
        if owner._mask_paint_dirty:
            owner._mask_paint_dirty = False
            layer_id = owner._mask_paint_layer_id
            if layer_id is not None:
                layer = owner._find_layer_by_id_tree(layer_id)
                if layer is not None:
                    layer.filter_mask_version += 1
            owner._mask_paint_layer_id = None
            owner._mask_paint_mask_snapshot = None
            owner._recompose()
