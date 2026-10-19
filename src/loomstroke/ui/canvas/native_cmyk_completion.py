"""Deliver worker completion to its owning Qt event loop without polling."""

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot


class NativeCmykCompletion(QObject):
    ready = Signal()

    def __init__(self, repaint: Callable[[], None] | None) -> None:
        super().__init__()
        self._repaint = repaint
        self.ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def notify(self, _future: Future[Any]) -> None:
        if self._repaint is not None:
            self.ready.emit()

    @Slot()
    def _deliver(self) -> None:
        if self._repaint is not None:
            self._repaint()

    def close(self) -> None:
        self._repaint = None
