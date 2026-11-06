"""Run detached fill coverage calculation in the Qt worker pool."""

from dataclasses import replace

from PySide6.QtCore import QRunnable
from PySide6.QtGui import QPainterPath

from loomstroke.core.fill.fill_gesture_mask import FillGestureMask
from loomstroke.core.fill.fill_region_calculator import FillRegionCalculator
from loomstroke.core.fill.fill_region_request import FillRegionRequest

from ..sdk.assist_geometry import combine_paths
from .fill_worker_signals import FillWorkerSignals


class FillRegionWorker(QRunnable):
    def __init__(self, request: FillRegionRequest, *, enclosure_paths=None, size=None) -> None:
        super().__init__()
        self.request = request
        self.enclosure_paths = (
            tuple(QPainterPath(p) for p in enclosure_paths) if enclosure_paths is not None else None
        )
        self.size = size
        self.signals = FillWorkerSignals()

    def run(self) -> None:
        try:
            request = self.request
            if self.enclosure_paths is not None:
                enclosure = FillGestureMask.render_path(
                    combine_paths(self.enclosure_paths), self.size
                )
                if enclosure is None:
                    self.signals.finished.emit(([], None), None)
                    return
                request = replace(request, enclosure=enclosure)
            self.signals.finished.emit(FillRegionCalculator.calculate(request), None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))
