"""Run detached fill coverage calculation in the Qt worker pool."""

from PySide6.QtCore import QRunnable

from loomstroke.core.fill.fill_region_calculator import FillRegionCalculator
from loomstroke.core.fill.fill_region_request import FillRegionRequest

from .fill_worker_signals import FillWorkerSignals


class FillRegionWorker(QRunnable):
    def __init__(self, request: FillRegionRequest) -> None:
        super().__init__()
        self.request = request
        self.signals = FillWorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(FillRegionCalculator.calculate(self.request), None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))
