"""Observe Space navigation across the canvas window's dock focus targets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDockWidget,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

if TYPE_CHECKING:
    from .input_handler import InputHandler


class SpaceKeyTracker(QObject):
    """Track navigation without consuming a panel's own keyboard actions.

    The filter is owned by the original canvas widget, so Qt removes it on
    destruction. Resolve ``handler.canvas`` per event: the native host replaces
    the legacy widget as the input target after InputHandler construction.
    """

    def __init__(self, handler: InputHandler, parent: QWidget) -> None:
        super().__init__(parent)
        self._handler = handler
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type == QEvent.Type.ApplicationDeactivate and self._handler.state.space:
            self._handler.handle_key_release(
                QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
            )
        elif (
            event_type in {QEvent.Type.KeyPress, QEvent.Type.KeyRelease}
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Space
        ):
            if event_type == QEvent.Type.KeyRelease and self._handler.state.space:
                # A held navigation key can be released after focus moves to an
                # editor or another window. Always clear it (except auto-repeat).
                self._handler.handle_key_release(QKeyEvent(event))
            elif (
                event_type == QEvent.Type.KeyPress
                and not event.isAutoRepeat()
                and self._accepts_press(watched)
            ):
                self._handler.handle_key_press(QKeyEvent(event))
        return False

    def _accepts_press(self, watched: QObject) -> bool:
        if not isinstance(watched, QWidget):
            return False
        canvas = self._handler.canvas
        if not isinstance(canvas, QWidget) or not canvas.isVisible():
            return False
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return False

        owner = canvas.window()
        target_window = watched.window()
        if target_window is not owner:
            # Floating docks are separate Qt windows but retain the MainWindow
            # as parent. Dialogs and unrelated windows are deliberately excluded.
            if not isinstance(target_window, QDockWidget):
                return False
            parent = target_window.parentWidget()
            while parent is not None and parent is not owner:
                parent = parent.parentWidget()
            if parent is None:
                return False

        # Qt can propagate an ignored editor key to its parents. Check the
        # focus widget as well as the receiver so a typed space never arms pan.
        return not self._is_editor(watched) and not self._is_editor(QApplication.focusWidget())

    @staticmethod
    def _is_editor(widget: QWidget | None) -> bool:
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return True
            if isinstance(widget, QComboBox) and widget.isEditable():
                return True
            widget = widget.parentWidget()
        return False
