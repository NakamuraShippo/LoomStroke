"""Queued worker completion signals; receiver lives on the GUI thread."""

from PySide6.QtCore import QObject, Signal


class FillWorkerSignals(QObject):
    finished = Signal(object, object)
