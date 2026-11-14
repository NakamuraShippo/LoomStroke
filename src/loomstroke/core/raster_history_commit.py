"""Recover an already applied raster edit if its history write raises."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .canvas_command_commit_guard import CanvasCommandCommitGuard
from .canvas_history_clear_continuation import _HistoryClearContinuation

logger = logging.getLogger(__name__)


class RasterBackingState:
    """Keep the original backing reference and placement without copying pixels."""

    def __init__(self, layer: Any) -> None:
        self.layer = layer
        self.image = layer.image
        self.placement = (layer.offset_x, layer.offset_y, layer.transform, layer.transform_version)
        self.texture_version = layer.texture_version
        self.blank_source = getattr(layer, "_panel_blank_source", None)

    def restore(self) -> None:
        layer = self.layer
        layer.image = self.image
        layer.offset_x, layer.offset_y, layer.transform, layer.transform_version = self.placement
        layer.texture_version = self.texture_version
        if hasattr(layer, "_panel_blank_source"):
            layer._panel_blank_source = self.blank_source


def record_raster_history(
    canvas: Any,
    record: Callable[[], bool | None],
    restore: Callable[[], None],
    *,
    apply: Callable[[], bool] | None = None,
) -> bool:
    guard = CanvasCommandCommitGuard(canvas)
    owner = _HistoryClearContinuation(canvas)
    try:
        if apply is not None and (not apply() or not owner.is_current()):
            return False
        # Some recorders already handle a refusal themselves (e.g. channel edits).
        return record() is not False
    except Exception:
        logger.exception("Raster edit commit failed")
        if owner.is_current():
            try:
                restore()
            finally:
                guard._restore_history()
            canvas._recompose()
            canvas._emit_history_changed()
        return False
