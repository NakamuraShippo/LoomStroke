"""Prepare reference fills asynchronously and reject stale document results."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QObject, Qt, QThreadPool, Slot

from loomstroke.core.fill.fill_context_snapshot import FillContextSnapshot
from loomstroke.core.fill.fill_region_request import FillRegionRequest
from loomstroke.core.fill.prepared_fill_command import PreparedFillCommand
from loomstroke.core.fill.prepared_fill_to_new_layer_command import PreparedFillToNewLayerCommand

from .fill_region_worker import FillRegionWorker


class FillPreparationController(QObject):
    def __init__(self, tool: Any) -> None:
        super().__init__()
        self._tool = tool
        self._worker: FillRegionWorker | None = None
        self._snapshot: FillContextSnapshot | None = None
        self._color: Any = None
        self._cancelled = False
        self._new_layer = False

    def cancel(self) -> None:
        self._cancelled = True

    def prepare(self, point: Any, enclosure: Any = None) -> None:
        if self._worker is not None:
            self._notify("A fill is still being prepared.")
            return
        tool = self._tool
        if not all(math.isfinite(value) for value in point.pos):
            return
        color = tool._active_color_rgba()
        if color is None or tool.host_api is None:
            return
        try:
            snapshot = FillContextSnapshot.capture(
                tool.host_api.canvas, tool.settings.reference_mode
            )
        except (ValueError, RuntimeError) as error:
            self._notify(str(error))
            return
        seed = tuple(map(round, point.pos))
        if tool.settings.fill_mode == "Bucket" and snapshot.selection is not None:
            x, y = seed
            width, height = snapshot.size
            if not 0 <= x < width or not 0 <= y < height or not snapshot.selection[y, x]:
                return
        self._snapshot, self._color, self._cancelled = snapshot, color, False
        self._new_layer = tool.settings.fill_target == "new_layer"
        request = FillRegionRequest(
            snapshot.reference,
            seed,
            tool.settings.color_tolerance,
            tool.settings.gap_close_px,
            tool.settings.area_expansion,
            tool.settings.fill_mode,
            enclosure,
            snapshot.allowed,
        )
        worker = FillRegionWorker(request)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, result: Any, error: Any) -> None:
        snapshot, color = self._snapshot, self._color
        self._worker, self._snapshot, self._color = None, None, None
        host = self._tool.host_api
        if self._cancelled or host is None or snapshot is None:
            return
        if error is not None:
            self._notify("The fill could not be prepared. The original pixels were preserved.")
            return
        if not snapshot.matches(host.canvas):
            self._notify("The document changed. The prepared fill was cancelled.")
            return
        spans, bounds = result
        if not spans or bounds is None:
            return
        command_type = PreparedFillToNewLayerCommand if self._new_layer else PreparedFillCommand
        command = command_type(snapshot, spans, bounds, color)
        if not host.canvas.submit(command):
            self._notify("The fill could not be applied. The original pixels were preserved.")

    def _notify(self, source: str) -> None:
        host = self._tool.host_api
        ui = getattr(host, "ui", None)
        show = getattr(ui, "show_message", None)
        if callable(show):
            from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

            show(translate_with_fallback("BasicFillBucket", source))
