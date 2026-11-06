"""Render a detached contour and feather coverage outside the GUI thread."""

from PySide6.QtCore import QRunnable
from PySide6.QtGui import QPainterPath

from loomstroke.core.selection_shapes.selection_shape_mask import SelectionShapeMask

from ..sdk.assist_geometry import combine_paths
from .selection_worker_signals import SelectionWorkerSignals


class SelectionMaskWorker(QRunnable):
    def __init__(self, path, size, radius, pixel_perfect):
        super().__init__()
        self.paths = tuple(QPainterPath(p) for p in path) if isinstance(path, tuple) else None
        self.path, self.size = (
            QPainterPath() if self.paths is not None else QPainterPath(path)
        ), size
        self.radius, self.pixel_perfect = radius, pixel_perfect
        self.signals = SelectionWorkerSignals()

    def run(self):
        try:
            if self.paths is not None:
                self.path = combine_paths(self.paths)
            result = SelectionShapeMask.render(
                self.path, self.size, feather_radius=self.radius, pixel_perfect=self.pixel_perfect
            )
            self.signals.finished.emit(result, None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))
