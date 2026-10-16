"""Run vector trimming without blocking the input/UI thread."""

import logging

from PySide6.QtCore import QRunnable

from .vector_erase_operation import VectorEraseOperation
from .vector_erase_worker_signals import VectorEraseWorkerSignals


class VectorEraseWorker(QRunnable):
    def __init__(self, objects, points, size, mode):
        super().__init__()
        self.signals = VectorEraseWorkerSignals()
        self._args = objects, points, size, mode

    def run(self) -> None:
        try:
            result = VectorEraseOperation.calculate(*self._args)
        except Exception as error:
            logging.getLogger(__name__).exception("Vector erase preparation failed")
            self.signals.finished.emit(None, str(error))
        else:
            self.signals.finished.emit(result, None)
