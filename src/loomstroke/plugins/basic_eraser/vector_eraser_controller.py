"""Vector-eraser gesture adapter; document edits are delegated to a Command."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QObject, Qt, QThreadPool, Slot

from loomstroke.core.fill.fill_gesture_mask import FillGestureMask
from loomstroke.core.vector_erase_command import VectorEraseCommand

from ..basic_selection.free_selection_overlay import FreeSelectionOverlay
from .vector_erase_worker import VectorEraseWorker


class VectorEraserController(QObject):
    def __init__(self, tool: Any) -> None:
        super().__init__()
        self._tool = tool
        self._points: list[tuple[float, float]] = []
        self._layer_id = ""
        self._objects: list[Any] = []
        self._overlay = FreeSelectionOverlay()
        self._worker: VectorEraseWorker | None = None
        self._cancelled = False
        self._prepared_layer = ""
        self._prepared_objects = []

    @property
    def active(self) -> bool:
        return bool(self._points)

    def down(self, point: Any) -> bool:
        canvas = self._tool.host_api.canvas
        layer = canvas.current_layer()
        if (
            getattr(layer, "layer_type", "raster") != "vector"
            or getattr(layer, "vector_kind", "draw") != "draw"
        ):
            return False
        self.cancel()
        if self._worker is not None:
            self._notify("A vector erase is still being prepared.")
            return True
        snapshot = canvas.get_vector_state_snapshot().layer(layer.id)
        if snapshot is None:
            return True
        self._layer_id, self._objects = layer.id, list(snapshot.vector_objects)
        pos = tuple(map(float, point.pos))
        if not all(math.isfinite(value) for value in pos):
            return True
        self._points = [pos]
        self._tool.host_api.input.pointer_capture("basic_eraser")
        return True

    def move(self, points: Any) -> None:
        if not self.active:
            return
        for point in points:
            pos = tuple(map(float, point.pos))
            if (
                all(math.isfinite(value) for value in pos)
                and math.dist(pos, self._points[-1]) >= 0.5
            ):
                if len(self._points) >= 8192:
                    self._points = self._points[::2]
                self._points.append(pos)
        self._overlay.show(
            self._tool.host_api,
            FillGestureMask.path("Gap Fill", self._points, self._tool.settings.size),
        )

    def up(self, point: Any) -> None:
        if not self.active:
            return
        self.move([point])
        canvas = self._tool.host_api.canvas
        if canvas.current_layer().id != self._layer_id:
            self.cancel()
            return
        objects, points, layer_id = deepcopy(self._objects), list(self._points), self._layer_id
        self.cancel()
        self._cancelled = False
        self._prepared_layer, self._prepared_objects = layer_id, objects
        worker = VectorEraseWorker(
            objects, points, self._tool.settings.size, self._tool.settings.vector_erase_mode
        )
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, result: Any, error: Any) -> None:
        self._worker = None
        objects, layer_id = self._prepared_objects, self._prepared_layer
        self._prepared_objects = []
        host = self._tool.host_api
        if self._cancelled or host is None or host.canvas.current_layer().id != layer_id:
            return
        if error is not None:
            self._notify(
                "The vector erase could not be prepared. The original strokes were preserved."
            )
        elif result is not None and not host.canvas.submit(
            VectorEraseCommand(layer_id, objects, result)
        ):
            self._notify("The document changed. The vector erase was cancelled.")

    def cancel(self) -> None:
        self._cancelled = True
        if self.active and self._tool.host_api is not None:
            self._tool.host_api.input.release_capture("basic_eraser")
        self._points, self._objects = [], []
        self._overlay.clear()

    def _notify(self, source: str) -> None:
        from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

        show = getattr(getattr(self._tool.host_api, "ui", None), "show_message", None)
        if callable(show):
            show(translate_with_fallback("BasicEraser", source))
