"""Routes wheel notches over a size slider to the host brush size stepper.

Without this a wheel notch moves the slider by its single step (1 px). Each
full notch (120 angle units) becomes one ``[`` / ``]``-style step, so rapid
scrolling accelerates the same way repeated bracket presses do.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent

_NOTCH = 120


class SizeWheelFilter(QObject):
    """Event filter turning wheel notches into ``handler(direction)`` calls."""

    def __init__(self, handler: Callable[[int], bool], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._handler = handler
        self._pending = 0

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel or not isinstance(event, QWheelEvent):
            return False
        angle = event.angleDelta()
        delta = angle.y() or angle.x()
        if delta == 0:
            return False
        if (delta > 0) != (self._pending > 0):
            self._pending = 0
        self._pending += delta
        notches = int(self._pending / _NOTCH)
        if notches == 0:
            # Partial notch from a high-resolution wheel: wait for the rest.
            event.accept()
            return True
        self._pending -= notches * _NOTCH
        direction = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            if not self._handler(direction):
                self._pending = 0
                return False
        event.accept()
        return True
