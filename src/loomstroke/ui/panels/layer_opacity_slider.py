"""Native slider input boundaries, including groove repeat and keyboard repeat."""

from __future__ import annotations

import weakref
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractSlider, QSlider


def _shutdown(holder: list[Any]) -> None:
    controller = holder[0]() if holder[0] is not None else None
    if controller is not None:
        controller.shutdown()


class _LayerOpacitySlider(QSlider):
    _NAVIGATION = frozenset(
        {
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_PageUp,
            Qt.Key.Key_PageDown,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        }
    )

    def __init__(self, parent: Any) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._panel = weakref.ref(parent)
        self._controller_holder: list[Any] = [None]
        self._gesture_kind: str | None = None
        self._key: int | None = None
        self._interrupted = False
        holder = self._controller_holder
        self._destroyed_handler = lambda *_: _shutdown(holder)
        self.destroyed.connect(self._destroyed_handler)

    def _controller(self) -> Any:
        from .layer_opacity_controller import _opacity_controller_for  # noqa: PLC0415

        controller = _opacity_controller_for(self._panel())
        self._controller_holder[0] = weakref.ref(controller)
        return controller

    def _begin(self, kind: str) -> None:
        if self._gesture_kind == kind:
            return
        if self._gesture_kind is not None:
            self._cancel()
        self._interrupted = False
        self._gesture_kind = kind
        self._controller().begin(kind)

    def _end(self, kind: str) -> None:
        if self._gesture_kind != kind:
            return
        self._gesture_kind = None
        self._controller().end()

    def _cancel(self) -> None:
        self._gesture_kind = None
        self._key = None
        self._interrupted = True
        self.setRepeatAction(QAbstractSlider.SliderAction.SliderNoAction)
        controller = (
            self._controller_holder[0]() if self._controller_holder[0] is not None else None
        )
        if controller is not None:
            controller.cancel()

    def mousePressEvent(self, event: Any) -> None:
        self._begin("mouse")
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._end("mouse")
            self._interrupted = False

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in self._NAVIGATION:
            self._begin("key")
            self._key = event.key()
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: Any) -> None:
        super().keyReleaseEvent(event)
        if not event.isAutoRepeat() and event.key() == self._key:
            self._key = None
            self._end("key")
            self._interrupted = False

    def wheelEvent(self, event: Any) -> None:
        self._begin("wheel")
        try:
            super().wheelEvent(event)
        finally:
            self._end("wheel")

    def focusOutEvent(self, event: Any) -> None:
        self._cancel()
        super().focusOutEvent(event)

    def hideEvent(self, event: Any) -> None:
        self._cancel()
        super().hideEvent(event)

    def closeEvent(self, event: Any) -> None:
        self._cancel()
        super().closeEvent(event)
