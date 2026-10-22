"""Let a pending comic operation receive history keys before numeric editors."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QDockWidget, QWidget

from .inline_text_history_target import inline_text_history_target


class PendingComicShortcutFilter(QObject):
    """Route only this window's active pending-history shortcuts to its actions."""

    def __init__(self, window: QWidget, pending: Any) -> None:
        super().__init__(window)
        self._window = window
        self._pending = pending
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            not self._pending.active
            or event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
            or not isinstance(event, QKeyEvent)
            or not isinstance(watched, QWidget)
            or (watched is not self._window and not self._window.isAncestorOf(watched))
            or inline_text_history_target(self._window) is not None
        ):
            return False
        owner = watched.window()
        if owner is not self._window and not isinstance(owner, QDockWidget):
            return False
        sequence = QKeySequence(event.keyCombination())
        for name in ("_undo_action", "_redo_action"):
            action = getattr(self._window, name, None)
            if action is None or not action.isEnabled():
                continue
            if any(
                shortcut.matches(sequence) == QKeySequence.SequenceMatch.ExactMatch
                for shortcut in action.shortcuts()
            ):
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    # Keep the following Redo on document history after the
                    # pending task completes and the numeric field re-enables.
                    canvas = getattr(self._window, "canvas", None)
                    if isinstance(canvas, QWidget):
                        canvas.setFocus(Qt.FocusReason.ShortcutFocusReason)
                    action.trigger()
                return True
        return False
