"""Symmetric magic-wand snapshots and one existing selection-history commit."""

import logging

import numpy as np
from PySide6.QtCore import QObject, Qt, QThreadPool, Slot

from loomstroke.core.fill.fill_context_snapshot import FillContextSnapshot
from loomstroke.core.selection_shapes.shape_selection_mask_command import ShapeSelectionMaskCommand

from ..common.page_context import page_context
from ..sdk.assist_geometry import seed_copies
from ..sdk.drawing_assist import ToolAssistBridge
from .assist_region_worker import AssistRegionWorker

logger = logging.getLogger(__name__)


class MagicWandDrawingAssist(QObject):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.bridge = ToolAssistBridge("magic_wand")
        self.worker = None
        self.snapshot = None
        self.operation = None
        self.cancelled = False
        self.page_context = None

    def prepare(self, point, operation):
        if self.worker is not None:
            return True
        self.bridge.capture(self.owner.host_api)
        session = self.bridge.session
        if session is None or not session.has_copies:
            self.bridge.end()
            return False
        try:
            snapshot = FillContextSnapshot.capture(
                self.owner.host_api.canvas,
                "Active Layer",
                respect_selection=False,
                require_editable=False,
            )
        except (ValueError, RuntimeError) as error:
            logger.warning("Unable to capture assisted selection: %s", error)
            self._notify()
            self.bridge.end()
            return True
        seeds = seed_copies(point.pos, session, snapshot.size)
        self.bridge.end()
        coverage = snapshot.allowed
        if coverage is None:
            coverage = np.full(snapshot.reference.shape[:2], 255, dtype=np.uint8)
        self.snapshot, self.operation, self.cancelled = snapshot, operation, False
        self.page_context = page_context(self.owner.host_api)
        worker = AssistRegionWorker(
            snapshot.reference, coverage, seeds, self.owner._tolerance, self.owner._contiguous
        )
        self.worker = worker
        worker.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)
        return True

    @Slot(object, object)
    def _finished(self, result, error):
        snapshot, self.snapshot = self.snapshot, None
        self.worker = None
        host = self.owner.host_api
        if (
            self.cancelled
            or snapshot is None
            or host is None
            or page_context(host) != self.page_context
            or not snapshot.matches(host.canvas)
        ):
            return
        if error is not None:
            logger.error("Assisted selection failed: %s", error)
            self._notify()
            return
        mask, bounds = result
        if bounds is not None and not host.canvas.submit(
            ShapeSelectionMaskCommand(mask, self.operation, bounds=(0, 0, *snapshot.size))
        ):
            self._notify()

    def _notify(self):
        from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

        show = getattr(getattr(self.owner.host_api, "ui", None), "show_message", None)
        if callable(show):
            show(translate_with_fallback("BasicSelectionRectTool", "Unable to apply selection."))

    def cancel(self):
        self.cancelled = True
        self.bridge.end()
