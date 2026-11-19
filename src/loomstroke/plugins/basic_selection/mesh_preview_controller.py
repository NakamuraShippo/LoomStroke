"""Calculate mesh previews away from Qt's paint and pointer event thread."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Slot

from loomstroke.core.commands import (
    UpdateSelectionMaskCommand,
    _is_qimage_layer,
    _resolve_target_layer,
)
from loomstroke.core.selection_mesh import Mesh, warp_mesh_payload

from ..common.page_context import page_context
from .projective_preview import _apply_preview
from .selection_worker_signals import SelectionWorkerSignals

if TYPE_CHECKING:
    from .rect_tool import BasicSelectionRectTool

logger = logging.getLogger(__name__)


class _MeshWorker(QRunnable):
    def __init__(
        self,
        payload: Any,
        mesh: Mesh,
        mask_mode: str,
        premultiplied: bool,
        canvas_size: tuple[int, int] | None,
    ) -> None:
        super().__init__()
        self.payload = payload
        self.mesh = mesh
        self.mask_mode = mask_mode
        self.premultiplied = premultiplied
        self.canvas_size = canvas_size
        self.signals = SelectionWorkerSignals()

    def run(self) -> None:
        try:
            result = warp_mesh_payload(
                self.payload,
                self.mesh,
                premultiplied=self.premultiplied,
                mask_mode=self.mask_mode,
                canvas_size=self.canvas_size,
            )
            self.signals.finished.emit(result, None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))


class MeshPreviewController(QObject):
    def __init__(self, tool: BasicSelectionRectTool) -> None:
        super().__init__()
        self._tool = tool
        self._generation = 0
        self._running: tuple[int, Mesh, tuple[Any, ...]] | None = None
        self._worker: _MeshWorker | None = None
        self._pending: Mesh | None = None
        self._cached: tuple[Mesh, Any] | None = None
        self._commit_requested = False

    def cancel(self) -> None:
        self._generation += 1
        self._pending = None
        self._cached = None
        self._commit_requested = False

    def result(self, mesh: Mesh) -> Any | None:
        return self._cached[1] if self._cached is not None and self._cached[0] == mesh else None

    def request(self, mesh: Mesh) -> None:
        if self.result(mesh) is not None:
            return
        if self._running is not None:
            if self._running[0] == self._generation and self._running[1] == mesh:
                return
            self._pending = mesh
            return
        self._start(mesh)

    def commit_when_ready(self, mesh: Mesh) -> None:
        self._commit_requested = True
        self.request(mesh)
        if self.result(mesh) is not None:
            self._submit()

    def _context(self) -> tuple[Any, ...] | None:
        host = self._tool.host_api
        payload = self._tool._transform_payload
        if host is None or payload is None:
            return None
        layer = _resolve_target_layer(host.canvas, None)
        return (
            id(host.canvas),
            page_context(host),
            id(payload),
            getattr(layer, "id", None),
            self._tool._transform_mask_mode,
        )

    def _start(self, mesh: Mesh) -> None:
        context = self._context()
        payload = self._tool._transform_payload
        host = self._tool.host_api
        if context is None or payload is None or host is None:
            return
        layer = _resolve_target_layer(host.canvas, None)
        premultiplied = _is_qimage_layer(layer)
        size = (
            None
            if premultiplied and not self._tool._layer_transform.active
            else UpdateSelectionMaskCommand._canvas_size(host.canvas)
        )
        worker = _MeshWorker(payload, mesh, self._tool._transform_mask_mode, premultiplied, size)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        self._running = (self._generation, mesh, context)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, result: Any, error: Any) -> None:  # noqa: PLR0911
        running = self._running
        self._running, self._worker = None, None
        if running is None:
            return
        generation, mesh, context = running
        if generation != self._generation:
            if self._pending is not None and self._pending == self._tool._transform_mesh:
                pending, self._pending = self._pending, None
                self._start(pending)
            return
        if context != self._context():
            host = self._tool.host_api
            if host is not None and (id(host.canvas), page_context(host)) == context[:2]:
                self._tool._cancel_transform_session()
            else:
                self.cancel()
            return
        current = self._tool._transform_mesh
        if current is None:
            return
        if self._pending is not None or current != mesh:
            pending, self._pending = self._pending or current, None
            self._start(pending)
            return
        if error is not None or result is None:
            logger.error("Mesh transform failed: %s", error)
            self._commit_requested = False
            self._tool._notify_message("Unable to apply selection.")
            return
        self._cached = (mesh, result)
        if self._commit_requested:
            self._submit()
            return
        host = self._tool.host_api
        if host is None:
            return
        canvas = host.canvas
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        layer = _resolve_target_layer(canvas, None)
        if (
            width > 0
            and height > 0
            and _apply_preview(canvas, result, width, height, premultiplied=_is_qimage_layer(layer))
        ):
            self._tool._recompose_canvas()

    def _submit(self) -> None:
        self._commit_requested = False
        command = self._tool._build_transform_commit_command()
        if command is None:
            return
        self._tool._submit_ready_transform_command(command)
