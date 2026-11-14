"""Keep Shape write errors inside Qt callbacks and retain their editable input."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid

from loomstroke.plugins.common.ui_notification_hint import has_log_only_ui_hint

logger = logging.getLogger(__name__)
_BODY = "Saving shape failed"


class _LibraryWriteFeedback(QObject):
    def __init__(self, panel: Any) -> None:
        super().__init__(panel)
        self._panel = panel
        self._dialog: QMessageBox | None = None
        self._source_text = _BODY
        self._format_args: tuple[Any, ...] = ()
        panel.installEventFilter(self)

    def show(self, source_text: str, format_args: tuple[Any, ...]) -> None:
        panel = self._panel
        self._source_text, self._format_args = source_text, format_args
        ui = getattr(getattr(panel, "_host", None), "ui", None)
        callback = getattr(ui, "show_message", None)
        if callable(callback) and not has_log_only_ui_hint(ui):
            callback(panel._ts(source_text).format(*format_args))
            return
        dialog = self._dialog
        if dialog is None or not isValid(dialog):
            dialog = QMessageBox(parent=panel)
            self._dialog = dialog
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
            dialog.installEventFilter(self)
            dialog.finished.connect(lambda *_args: self._closed(dialog))
            dialog.destroyed.connect(lambda *_args: self._closed(dialog))
        self.refresh()
        dialog.show()

    def _closed(self, dialog: QMessageBox) -> None:
        if self._dialog is dialog:
            self._dialog = None

    def close(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None and isValid(dialog):
            dialog.close()

    def refresh(self) -> None:
        if self._dialog is not None and isValid(self._dialog):
            self._dialog.setWindowTitle(self._panel._ts("Shape Manager"))
            self._dialog.setText(self._panel._ts(self._source_text).format(*self._format_args))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._panel and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.close()
        elif watched is self._dialog and event.type() == QEvent.Type.LanguageChange:
            self.refresh()
        return False


def run_library_write(panel: Any, callback: Callable[..., Any], *args: Any) -> Any:
    try:
        return callback(*args)
    except (OSError, ValueError) as exc:
        logger.warning("Shape library write failed (%s)", type(exc).__name__)
        notify_library_write_failure(panel)
        return False


def notify_library_write_failure(panel: Any, source_text: str = _BODY, *format_args: Any) -> None:
    try:
        notice = getattr(panel, "_library_write_feedback", None)
        if notice is None or not isValid(notice):
            notice = _LibraryWriteFeedback(panel)
            panel._library_write_feedback = notice
        notice.show(source_text, format_args)
    except Exception:
        logger.warning("Shape library write notice could not be displayed")


def refresh_library_write_feedback(panel: Any) -> None:
    notice = getattr(panel, "_library_write_feedback", None)
    if notice is not None and isValid(notice):
        notice.refresh()


def retire_library_write_feedback(panel: Any) -> None:
    notice = getattr(panel, "_library_write_feedback", None)
    if notice is not None and isValid(notice):
        notice.close()
