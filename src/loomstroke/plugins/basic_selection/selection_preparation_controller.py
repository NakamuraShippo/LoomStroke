"""Publish a prepared selection only while its original document is current."""

import hashlib
import logging

from PySide6.QtCore import QObject, Qt, QThreadPool, Slot

from loomstroke.core.selection_shapes.shape_selection_mask_command import ShapeSelectionMaskCommand

from .selection_mask_worker import SelectionMaskWorker

logger = logging.getLogger(__name__)


class SelectionPreparationController(QObject):
    def __init__(self, tool):
        super().__init__()
        self._tool = tool
        self._worker = None
        self._cancelled = False
        self._context = None
        self._operation = None

    @property
    def busy(self):
        return self._worker is not None

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def context(canvas):
        mask = canvas.get_selection_mask()
        return (
            id(canvas),
            canvas.get_canvas_size(),
            canvas.current_layer().id,
            (
                hashlib.blake2b(mask.constBits(), digest_size=16).digest()
                if mask is not None
                else None
            ),
        )

    def prepare(self, path, size, radius, pixel_perfect, operation):
        self._context = self.context(self._tool.host_api.canvas)
        self._operation, self._cancelled = operation, False
        worker = SelectionMaskWorker(path, size, radius, pixel_perfect)
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    @Slot(object, object)
    def _finished(self, result, error):
        self._worker = None
        host = self._tool.host_api
        if self._cancelled or host is None or self.context(host.canvas) != self._context:
            return
        if error is not None:
            logger.error("Selection preparation failed: %s", error)
            self._notify()
        elif result is not None:
            mask, bounds = result
            if not host.canvas.submit(
                ShapeSelectionMaskCommand(mask, self._operation, bounds=bounds)
            ):
                self._notify()

    def _notify(self):
        from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

        show = getattr(getattr(self._tool.host_api, "ui", None), "show_message", None)
        if callable(show):
            show(translate_with_fallback("BasicSelectionRectTool", "Unable to apply selection."))
