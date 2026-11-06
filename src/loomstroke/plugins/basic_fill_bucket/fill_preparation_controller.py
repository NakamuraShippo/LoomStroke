"""Prepare reference fills asynchronously and reject stale document results."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QObject, Qt, QThreadPool, Slot

from loomstroke.core.fill.fill_context_snapshot import FillContextSnapshot
from loomstroke.core.fill.fill_region_request import FillRegionRequest
from loomstroke.core.fill.prepared_fill_command import PreparedFillCommand
from loomstroke.core.fill.prepared_fill_to_new_layer_command import PreparedFillToNewLayerCommand

from ..common.page_context import page_context
from ..sdk.assist_geometry import seed_copies
from .fill_region_worker import FillRegionWorker


class FillPreparationController(QObject):
    def __init__(self, tool: Any, *, command_factory=None) -> None:
        super().__init__()
        self._tool = tool
        self._command_factory = command_factory
        self._worker: FillRegionWorker | None = None
        self._snapshot: FillContextSnapshot | None = None
        self._color: Any = None
        self._cancelled = False
        self._new_layer = False
        self._page_context = None

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
        session = getattr(getattr(tool, "_assist", None), "session", None)
        seeds = seed_copies(point.pos, session, snapshot.size)
        if tool.settings.fill_mode == "Bucket" and snapshot.selection is not None:
            seeds = tuple((x, y) for x, y in seeds if snapshot.selection[y, x])
            if not seeds:
                return
        if (
            tool.settings.fill_mode == "Bucket"
            and session is not None
            and session.has_copies
            and not seeds
        ):
            return
        self._snapshot, self._color, self._cancelled = snapshot, color, False
        self._page_context = page_context(tool.host_api)
        self._new_layer = tool.settings.fill_target == "new_layer"
        enclosure_paths = enclosure if isinstance(enclosure, tuple) else None
        request = FillRegionRequest(
            snapshot.reference,
            seed,
            tool.settings.color_tolerance,
            tool.settings.gap_close_px,
            tool.settings.area_expansion,
            tool.settings.fill_mode,
            None if enclosure_paths is not None else enclosure,
            snapshot.allowed,
            (
                seeds
                if tool.settings.fill_mode == "Bucket"
                and session is not None
                and session.has_copies
                else ()
            ),
        )
        worker = (
            FillRegionWorker(request, enclosure_paths=enclosure_paths, size=snapshot.size)
            if enclosure_paths is not None
            else FillRegionWorker(request)
        )
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
        if page_context(host) != self._page_context or not snapshot.matches(host.canvas):
            self._notify("The document changed. The prepared fill was cancelled.")
            return
        spans, bounds = result
        if not spans or bounds is None:
            return
        command_type = PreparedFillToNewLayerCommand if self._new_layer else PreparedFillCommand
        factory = self._command_factory or command_type
        command = factory(snapshot, spans, bounds, color)
        if not host.canvas.submit(command):
            self._notify("The fill could not be applied. The original pixels were preserved.")

    def _notify(self, source: str) -> None:
        host = self._tool.host_api
        ui = getattr(host, "ui", None)
        show = getattr(ui, "show_message", None)
        if callable(show):
            from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

            show(translate_with_fallback("BasicFillBucket", source))
