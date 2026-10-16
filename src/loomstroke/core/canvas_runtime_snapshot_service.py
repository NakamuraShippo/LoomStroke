"""Runtime snapshot helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor

from loomstroke.core.canvas_models import CanvasStateSnapshot
from loomstroke.core.selection_state import build_selection_state_snapshot
from loomstroke.core.vector_state import build_vector_state_snapshot


class CanvasRuntimeSnapshotService:
    """Build renderer/save/page snapshots without mutating canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def get_canvas_state_snapshot(self) -> CanvasStateSnapshot:
        active_layer = self._owner._get_active_layer()
        return CanvasStateSnapshot(
            width=int(self._owner._width),
            height=int(self._owner._height),
            dpi=int(self._owner._dpi),
            background=QColor(self._owner._background),
            active_layer_id=active_layer.id if active_layer is not None else None,
            active_index=int(self._owner._active_index),
            layer_cache_version=int(self._owner._layer_cache_version),
        )

    def get_canvas_size(self) -> tuple[int, int]:
        return int(self._owner._width), int(self._owner._height)

    def get_selection_state_snapshot(self) -> Any:
        return build_selection_state_snapshot(self._owner)

    def get_vector_state_snapshot(self) -> Any:
        return build_vector_state_snapshot(self._owner)

    def get_document_layer_snapshots(self) -> Any:
        from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
            DocumentSnapshotBuilder,
        )

        return DocumentSnapshotBuilder.capture_simple_canvas_layers(self._owner)
