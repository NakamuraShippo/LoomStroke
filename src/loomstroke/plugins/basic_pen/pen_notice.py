"""Private GUI and dialog lifetime shared by the two Basic Pen failure notices."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread

logger = logging.getLogger(__name__)


def _gui_application() -> Any:
    # Resolve at the call boundary, without capturing a test replacement or
    # mistaking QCoreApplication for a GUI application.
    from PySide6 import QtWidgets  # noqa: PLC0415

    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication) or QThread.currentThread() != app.thread():
        return None
    return app


class _PenNotice(QObject):
    def __init__(
        self, translate: Callable[[str], str], title_source: str, body_source: str
    ) -> None:
        super().__init__()
        self._translate = translate
        self._title_source = title_source
        self._body_source = body_source
        self._dialog: Any = None

    def show(self, host: Any) -> None:
        from PySide6 import QtWidgets  # noqa: PLC0415

        if self._dialog is not None:
            self._retranslate()
            self._dialog.show()
            self._dialog.raise_()
            return
        ui = getattr(host, "ui", None)
        getter = getattr(ui, "get_main_window", None)
        parent = getter() if callable(getter) else None
        if not isinstance(parent, QtWidgets.QWidget):
            parent = None
        dialog = QtWidgets.QMessageBox(parent=parent)
        self._dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        dialog.installEventFilter(self)
        dialog.finished.connect(lambda *_args: self._closed(dialog))
        dialog.destroyed.connect(lambda *_args: self._closed(dialog))
        self._retranslate()
        dialog.show()

    def close(self) -> None:
        if self._dialog is not None:
            self._dialog.close()

    def _closed(self, dialog: Any) -> None:
        # A previous dialog's DeferredDelete must not release its replacement.
        if self._dialog is dialog:
            self._dialog = None

    def _retranslate(self) -> None:
        self._dialog.setWindowTitle(self._translate(self._title_source))
        self._dialog.setText(self._translate(self._body_source))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._dialog and event.type() == QEvent.Type.LanguageChange:
            try:
                self._retranslate()
            except Exception:
                logger.warning("Basic Pen notice could not be translated")
        return False
