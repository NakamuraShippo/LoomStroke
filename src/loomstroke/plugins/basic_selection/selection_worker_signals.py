"""Queued completion for detached selection coverage calculation."""

from PySide6.QtCore import QObject, Signal


class SelectionWorkerSignals(QObject):
    finished = Signal(object, object)
