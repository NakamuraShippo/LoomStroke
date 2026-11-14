"""Stroke submit orchestration helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import os
import time
from typing import Any

from loomstroke.core.comic_panels.panel_gate import panel_stroke_reason
from loomstroke.core.comic_panels.pending_drawing import (
    consume_pending_drawing,
    pending_drawing_layer,
)
from loomstroke.core.commands import EraseCommand, PaintCommand, StampBrushCommand

_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class CanvasStrokeSubmitService:
    """Coordinate stroke submit side effects around stroke application."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def _paint_target(self, cmd: PaintCommand | StampBrushCommand) -> Any | None:
        owner = self._owner
        pending = pending_drawing_layer(owner)
        if cmd.layer_id is None or (pending is not None and cmd.layer_id == pending.id):
            if panel_stroke_reason(owner) or not consume_pending_drawing(owner, cmd):
                return None
            layer = owner._get_active_layer()
        else:
            layer = owner._get_layer_by_id(cmd.layer_id)
            if layer is None or panel_stroke_reason(owner, layer):
                return None
        is_mask = layer is not None and layer.mask_edit_mode and layer.layer_mask is not None
        return owner._resolve_paint_target(cmd.layer_id, mask_edit=is_mask)

    def submit_paint(self, cmd: PaintCommand) -> bool:
        owner = self._owner
        layer = self._paint_target(cmd)
        if layer is None:
            return False
        perf_start = time.perf_counter() if _PEN_LAG_DIAG else 0.0
        if owner._in_transaction:
            owner._stroke_painting_active = True
        is_mask_paint = layer is not None and layer.mask_edit_mode and layer.layer_mask is not None
        owner._mask_edit_flag = is_mask_paint
        with owner._mask_edit_swap(layer):
            dirty = owner._apply_paint(cmd)
        owner._mask_edit_flag = False
        if dirty is None:
            return False
        if is_mask_paint and layer is not None:
            owner._gpu_mask_dirty_layers.add(layer.id)
            owner._schedule_mask_commit(layer)
        owner._recompose_region(dirty)
        owner._log_pen_lag_diag("submit_paint", perf_start, dirty)
        return True

    def submit_stamp_brush(self, cmd: StampBrushCommand) -> bool:
        owner = self._owner
        layer = self._paint_target(cmd)
        if layer is None:
            return False
        perf_start = time.perf_counter() if _PEN_LAG_DIAG else 0.0
        if owner._in_transaction:
            owner._stroke_painting_active = True
        is_mask_paint = layer is not None and layer.mask_edit_mode and layer.layer_mask is not None
        owner._mask_edit_flag = is_mask_paint
        with owner._mask_edit_swap(layer):
            dirty = owner._apply_stamp_brush(cmd)
        owner._mask_edit_flag = False
        if dirty is None:
            return False
        if is_mask_paint and layer is not None:
            owner._gpu_mask_dirty_layers.add(layer.id)
            owner._schedule_mask_commit(layer)
        owner._recompose_region(dirty)
        owner._log_pen_lag_diag("submit_stamp_brush", perf_start, dirty)
        return True

    def submit_erase(self, cmd: EraseCommand) -> bool:
        owner = self._owner
        if panel_stroke_reason(owner) or not consume_pending_drawing(owner, cmd, allow_new=False):
            return False
        layer = owner._get_active_layer()
        is_mask_paint = layer is not None and layer.mask_edit_mode and layer.layer_mask is not None
        owner._mask_edit_flag = is_mask_paint
        with owner._mask_edit_swap(layer):
            dirty = owner._apply_erase(cmd)
        owner._mask_edit_flag = False
        if dirty is None:
            return False
        if is_mask_paint and layer is not None:
            owner._gpu_mask_dirty_layers.add(layer.id)
            owner._schedule_mask_commit()
        owner._recompose_region(dirty)
        return True
