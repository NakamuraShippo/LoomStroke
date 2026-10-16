"""Main-thread delivery channel for detached vector eraser work."""

from PySide6.QtCore import QObject, Signal


class VectorEraseWorkerSignals(QObject):
    finished = Signal(object, object)
