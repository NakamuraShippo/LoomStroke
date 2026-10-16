"""Nonblocking access to the existing SAM 3.1 model unload operation."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from loomstroke.sam31.server_manager import Sam31ServerError, get_sam31_server_manager
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)
_TOOLTIP = "Unload the SAM 3.1 model. It reloads automatically on next use."
_MESSAGES = {
    "idle": "",
    "releasing": "Waiting for SAM, then releasing memory...",
    "released": "Model memory released. Reloads on next use.",
    "failed": "Could not release model memory ({error}).",
}


def _ts(text: str) -> str:
    return str(translated_literal_domain("SAM3AISelect", text, use_fallback=True))


class _ReleaseSignals(QObject):
    finished = Signal(str)


class _ReleaseTask(QRunnable):
    def __init__(self, manager: Any) -> None:
        super().__init__()
        self.manager = manager
        self.signals = _ReleaseSignals()

    def run(self) -> None:
        error = ""
        try:
            # The manager serializes this with in-progress worker requests.
            self.manager.unload_model()
        except Sam31ServerError as exc:
            if exc.code != "WORKER_NOT_RUNNING":
                error = exc.code
                logger.warning("SAM model unload failed: %s", exc.code)
        except Exception as exc:  # noqa: BLE001 - keep a retryable UI on worker failure
            error = type(exc).__name__
            logger.exception("SAM model unload failed")
        self.signals.finished.emit(error)


class Sam31MemoryReleaseWidget(QWidget):
    """Retain candidates and selection while releasing inference model memory."""

    def __init__(self, parent: QWidget | None = None, *, manager: Any = None) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else get_sam31_server_manager()
        self._task: _ReleaseTask | None = None
        self.state = "idle"
        self._error = ""
        self.button = QPushButton(self)
        self.button.setObjectName("sam31ReleaseMemory")
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(self.status)
        self.button.clicked.connect(self._release)
        self._retranslate()

    @Slot()
    def _release(self) -> None:
        if self._task is not None:
            return
        self.state = "releasing"
        self.button.setEnabled(False)
        self._retranslate()
        task = _ReleaseTask(self._manager)
        task.signals.finished.connect(self._finished)
        self._task = task
        # A pool task outlives a closed panel; Qt disconnects its destroyed receiver.
        QThreadPool.globalInstance().start(task)

    @Slot(str)
    def _finished(self, error: str) -> None:
        self._task = None
        self._error = error
        self.state = "failed" if error else "released"
        self.button.setEnabled(True)
        self._retranslate()

    def _retranslate(self) -> None:
        self.button.setText(_ts("Release SAM Memory"))
        self.button.setToolTip(_ts(_TOOLTIP))
        message = _MESSAGES[self.state]
        self.status.setText(_ts(message).format(error=self._error) if message else "")
        self.status.setVisible(bool(message))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()
