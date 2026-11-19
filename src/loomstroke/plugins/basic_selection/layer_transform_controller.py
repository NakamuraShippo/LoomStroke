"""Route a whole RGB raster layer through the selection transform session."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, QRect, QRunnable, Qt, QThreadPool, Slot
from PySide6.QtGui import QImage

from loomstroke.core.canvas_history_entries import _HistoryPatch
from loomstroke.core.commands import (
    TransformSelectionCommand,
    UpdateSelectionMaskCommand,
    _resolve_target_layer,
    _set_selection_full_bounds,
    _set_selection_full_mask,
)
from loomstroke.core.selection_projective_composite import composite_projective_qimage

from ..common.page_context import page_context
from .selection_worker_signals import SelectionWorkerSignals

if TYPE_CHECKING:
    from .rect_tool import BasicSelectionRectTool

logger = logging.getLogger(__name__)


class _PrepareLayerWorker(QRunnable):
    def __init__(
        self,
        command: TransformSelectionCommand,
        source: QImage,
        offset: tuple[int, int],
    ) -> None:
        super().__init__()
        self.command, self.source, self.offset = command, source, offset
        self.signals = SelectionWorkerSignals()

    def run(self) -> None:
        try:
            command = self.command
            result = command.liquify_raster or command.prepared_mesh or command.prepared_projective
            if result is None:
                raise ValueError("Layer transform has no prepared raster")
            image, mask, bounds = result
            after = self.source.copy()
            if not composite_projective_qimage(
                after,
                command.payload,
                image,
                mask,
                bounds,
                offset=self.offset,
                binary=command.mask_mode == "binary",
                duplicate=command.duplicate,
                additive=command.liquify_raster is not None,
            ):
                raise RuntimeError("Layer transform composition failed")
            rect = QRect(self.offset[0], self.offset[1], self.source.width(), self.source.height())
            patch = _HistoryPatch.from_images(
                command.layer_id,
                rect,
                self.source,
                after,
                {"command": "transform_selection"},
            )
            self.signals.finished.emit((self.source, after, patch), None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))


class LayerTransformController(QObject):
    def __init__(self, tool: BasicSelectionRectTool) -> None:
        super().__init__()
        self._tool = tool
        self.active = False
        self._context: tuple[int, object] | None = None
        self._generation = 0
        self._running: tuple[int, tuple[Any, ...], TransformSelectionCommand] | None = None
        self._worker: _PrepareLayerWorker | None = None

    def start(self, mode: str) -> bool:  # noqa: PLR0911
        tool = self._tool
        host = tool.host_api
        if host is None or tool._transform_active or self._running is not None:
            return False
        if tool._selection_exists():
            self._notify("Deselect before transforming the whole layer.")
            return False
        canvas = host.canvas
        layer = _resolve_target_layer(canvas, None)
        if (
            tool._is_vector_mode()
            or layer is None
            or getattr(layer, "layer_type", "raster") != "raster"
            or getattr(layer, "native_cmyk", None) is not None
        ):
            self._notify("This transform requires an RGB raster layer.")
            return False
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False
        mask = np.full((height, width), 255, dtype=np.uint8)
        canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
            canvas, mask, width, height
        )
        if canvas_mask is None:
            return False
        UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, (0, 0, width, height))
        _set_selection_full_bounds(canvas, None)
        _set_selection_full_mask(canvas, None)
        self.active = True
        self._context = (id(canvas), page_context(host))
        if mode == "liquify":
            tool._liquify.start("push")
        else:
            tool._begin_transform_mode(mode)
        if not tool._transform_active:
            self.finish()
            return False
        return True

    def finish(self) -> None:
        if not self.active:
            return
        self.active = False
        host = self._tool.host_api
        context, self._context = self._context, None
        if host is None or (id(host.canvas), page_context(host)) != context:
            return
        canvas = host.canvas
        UpdateSelectionMaskCommand._set_selection(canvas, None, None)
        _set_selection_full_bounds(canvas, None)
        _set_selection_full_mask(canvas, None)
        self._tool._recompose_canvas()

    def _notify(self, source: str) -> None:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        message = translated_literal_domain("BasicSelectionRectTool", source, use_fallback=True)
        self._tool._notify_message(message)

    def submit_prepared(self, command: TransformSelectionCommand) -> None:
        host = self._tool.host_api
        if host is None:
            return
        layer = _resolve_target_layer(host.canvas, command.layer_id)
        if layer is None or not isinstance(getattr(layer, "image", None), QImage):
            return
        source = QImage(layer.image)
        context = (
            id(host.canvas),
            page_context(host),
            getattr(layer, "id", None),
            getattr(layer, "texture_version", None),
            source.cacheKey(),
        )
        offset = (int(getattr(layer, "offset_x", 0)), int(getattr(layer, "offset_y", 0)))
        worker = _PrepareLayerWorker(command, source, offset)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        self._running = (self._generation, context, command)
        QThreadPool.globalInstance().start(worker)

    def cancel_pending(self) -> bool:
        if self._running is None:
            return False
        self._generation += 1
        return True

    @Slot(object, object)
    def _finished(self, result: Any, error: Any) -> None:
        running = self._running
        self._running, self._worker = None, None
        if running is None:
            return
        generation, context, command = running
        if generation != self._generation:
            return
        host = self._tool.host_api
        if host is None:
            return
        layer = _resolve_target_layer(host.canvas, command.layer_id)
        current = (
            id(host.canvas),
            page_context(host),
            getattr(layer, "id", None),
            getattr(layer, "texture_version", None),
            layer.image.cacheKey() if layer is not None else None,
        )
        if current != context:
            return
        if error is not None or result is None:
            logger.error("Layer transform preparation failed: %s", error)
            self._notify("Unable to apply selection.")
            return
        (
            command.prepared_layer_before,
            command.prepared_layer_image,
            command._prepared_history_patch,
        ) = result
        try:
            success = bool(host.canvas.submit(command))
        except Exception:
            logger.exception("Prepared layer transform could not be submitted")
            success = False
        if not success:
            self._notify("Unable to apply selection.")
