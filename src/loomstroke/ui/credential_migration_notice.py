"""Deliver credential migration notices on the owning Qt main thread."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from shiboken6 import isValid

from ..config.credential_migration_notice import (
    MigrationNotice,
    MigrationNoticeQueue,
    migration_notices,
)
from .credential_migration_translations import CLEANUP, CONFLICT, PRESERVED, TITLE
from .translations import translate_with_fallback

logger = logging.getLogger(__name__)


class CredentialMigrationNoticeBridge(QObject):
    ready = Signal()

    def __init__(self, window: QWidget, queue: MigrationNoticeQueue) -> None:
        super().__init__(window)
        self._window = window
        self._queue = queue
        self._dialogs: dict[QMessageBox, MigrationNotice] = {}
        self.ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._handler = self.notify
        self._unsubscribe = queue.subscribe(self._handler)
        self.destroyed.connect(self._unsubscribe)
        self.notify()

    def notify(self) -> None:
        if isValid(self):
            self.ready.emit()

    @Slot()
    def _deliver(self) -> None:
        while isValid(self._window) and (notice := self._queue.claim()) is not None:
            dialog = None
            try:
                dialog = QMessageBox(self._window)
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
                self._dialogs[dialog] = notice
                dialog.installEventFilter(self)
                self._translate_dialog(dialog, notice)
                dialog.show()
                displayed = isValid(dialog) and dialog.isVisible()
            except Exception:
                logger.warning("Credential migration notice could not be displayed")
                displayed = False
            self._queue.finish(notice, delivered=displayed)
            if not displayed:
                if dialog is not None and isValid(dialog):
                    dialog.deleteLater()
                return

    def _translate_dialog(self, dialog: QMessageBox, notice: MigrationNotice) -> None:
        source = {"keyring_conflict": CONFLICT, "cleanup_failed": CLEANUP}.get(
            notice.reason, PRESERVED
        )
        dialog.setWindowTitle(translate_with_fallback("CredentialMigrationNotice", TITLE))
        dialog.setText(translate_with_fallback("CredentialMigrationNotice", source))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.LanguageChange
            and isinstance(watched, QMessageBox)
            and watched in self._dialogs
        ):
            self._translate_dialog(watched, self._dialogs[watched])
        return super().eventFilter(watched, event)


def install_credential_migration_notice(
    window: Any, *, queue: MigrationNoticeQueue | None = None
) -> CredentialMigrationNoticeBridge | None:
    app = QApplication.instance()
    if not isinstance(window, QWidget) or not isinstance(app, QApplication):
        return None
    if not isValid(window) or QThread.currentThread() is not app.thread():
        return None
    existing = getattr(window, "_credential_migration_notice_bridge", None)
    if isinstance(existing, CredentialMigrationNoticeBridge) and isValid(existing):
        existing.notify()
        return existing
    bridge = CredentialMigrationNoticeBridge(window, queue or migration_notices)
    window._credential_migration_notice_bridge = bridge
    return bridge
