"""Render a detached contour and feather coverage outside the GUI thread."""

from PySide6.QtCore import QRunnable
from PySide6.QtGui import QPainterPath

from loomstroke.core.selection_shapes.selection_shape_mask import SelectionShapeMask

from .selection_worker_signals import SelectionWorkerSignals


class SelectionMaskWorker(QRunnable):
    def __init__(self, path, size, radius, pixel_perfect):
        super().__init__()
        self.path, self.size = QPainterPath(path), size
        self.radius, self.pixel_perfect = radius, pixel_perfect
        self.signals = SelectionWorkerSignals()

    def run(self):
        try:
            result = SelectionShapeMask.render(
                self.path, self.size, feather_radius=self.radius, pixel_perfect=self.pixel_perfect
            )
            self.signals.finished.emit(result, None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))
