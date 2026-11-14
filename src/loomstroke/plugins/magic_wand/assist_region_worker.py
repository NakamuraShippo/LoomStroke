"""Offload symmetric magic-wand matching without accessing live document objects."""

from PySide6.QtCore import QRunnable

from loomstroke.core.selection_shapes.color_region_union import color_region_union
from loomstroke.core.selection_shapes.selection_mask_bounds import SelectionMaskBounds


class AssistRegionWorker(QRunnable):
    def __init__(self, reference, coverage, seeds, tolerance, contiguous):
        from ..basic_fill_bucket.fill_worker_signals import FillWorkerSignals  # noqa: PLC0415

        super().__init__()
        self.inputs = (reference, coverage, seeds, tolerance, contiguous)
        self.signals = FillWorkerSignals()

    def run(self):
        try:
            mask = color_region_union(*self.inputs)
            self.signals.finished.emit((mask, SelectionMaskBounds.find(mask)), None)
        except Exception as error:
            self.signals.finished.emit(None, str(error))
