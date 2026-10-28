"""One initial library-load notice, qualified and owned by its Shape panel."""

from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from loomstroke.plugins.common.ui_notification_hint import has_log_only_ui_hint

from ..model.shape_index import ShapeIndex

logger = logging.getLogger(__name__)
_TITLE = "Shape Manager"
_BODY = "The shape library could not be loaded. Library changes cannot be saved."
_FAILURES = frozenset({"read", "json", "missing"})


@dataclass(frozen=True)
class LibraryLoadIssue:
    index: ShapeIndex
    root: Path
    path: Path
    code: str


def capture_library_load_issue(panel: Any) -> LibraryLoadIssue | None:
    """Observe the initial load only; never read, reload or reset its model."""
    index, root = panel._index, panel._data_root
    if not isinstance(index, ShapeIndex) or index._load_failure not in _FAILURES:
        return None
    return LibraryLoadIssue(index, root, index.path, index._load_failure)


def _gui_application() -> Any:
    try:
        app = QApplication.instance()
        if not isinstance(app, QApplication) or QThread.currentThread() != app.thread():
            return None
        return app
    except Exception:
        _warning()
        return None


def _warning() -> None:
    logger.warning("Shape library load notice could not be displayed")


class LibraryLoadFeedback(QObject):
    ready = Signal(int)

    def __init__(self, panel: Any, issue: LibraryLoadIssue) -> None:
        super().__init__(panel)
        self._panel = weakref.ref(panel)
        self._issue = issue
        self._host: Any = None
        self._ui: Any = None
        self._dialog: Any = None
        self._generation = 0
        self._queued = False
        self._attempted = False
        self._disposed = False
        self.ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        panel.installEventFilter(self)

    def _owns_issue(self) -> bool:
        panel = self._panel()
        issue = self._issue
        return (
            not self._disposed
            and panel is not None
            and isValid(panel)
            and panel._index is issue.index
            and panel._data_root == issue.root
            and issue.index.path == issue.path == issue.root / "index.json"
            and issue.index._load_failure in _FAILURES
        )

    def _current(self, generation: int | None = None) -> bool:
        panel = self._panel()
        return (
            (generation is None or generation == self._generation)
            and panel is not None
            and _gui_application() is not None
            and self._owns_issue()
            and self._host is not None
            and panel._host is self._host
            and getattr(self._host, "ui", None) is self._ui
            and (generation is None or generation == self._generation)
            and self._owns_issue()
            and panel._host is self._host
        )

    def request(self) -> None:
        if not self._owns_issue():
            self.invalidate()
            return
        panel = self._panel()
        if panel is None:
            return
        host = panel._host
        if host is None:
            self.invalidate()
            return
        ui = getattr(host, "ui", None)
        if host is not self._host or ui is not self._ui:
            self.invalidate()
            self._host, self._ui = host, ui
        if self._attempted:
            return
        if not self._queued and self._current() and panel.isVisible():
            self._queued = True
            self._enqueue()

    def _enqueue(self) -> None:
        self.ready.emit(self._generation)

    @Slot(int)
    def _deliver(self, generation: int) -> None:
        if generation != self._generation or self._disposed:
            return
        try:
            if not self._current(generation):
                return
            panel = self._panel()
            if panel is None or self._attempted or not panel.isVisible():
                return
            marked = has_log_only_ui_hint(self._ui)
            body = panel._ts(_BODY)
            if not self._current(generation) or marked != has_log_only_ui_hint(self._ui):
                return
            if marked:
                self._show_box(body, generation)
            else:
                callback = getattr(self._ui, "show_message", None)
                if not self._current(generation) or has_log_only_ui_hint(self._ui):
                    return
                self._attempted = True
                if callable(callback):
                    callback(body)
                else:
                    _warning()
        except Exception:
            self._attempted |= generation == self._generation
            _warning()
        finally:
            if generation == self._generation:
                self._queued = False

    def _show_box(self, body: str, generation: int) -> None:
        panel = self._panel()
        if panel is None:
            return
        title = panel._ts(_TITLE)
        if not self._current(generation):
            return
        dialog = QMessageBox(parent=panel)
        try:
            if (
                not self._current(generation)
                or not panel.isVisible()
                or not has_log_only_ui_hint(self._ui)
            ):
                self._close_dialog(dialog)
                return
            self._dialog = dialog
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
            dialog.installEventFilter(self)
            dialog.finished.connect(lambda *_args: self._closed(dialog))
            dialog.destroyed.connect(lambda *_args: self._closed(dialog))
            dialog.setWindowTitle(title)
            dialog.setText(body)
            if (
                not self._current(generation)
                or not panel.isVisible()
                or not has_log_only_ui_hint(self._ui)
            ):
                self._close_dialog(dialog)
                return
            self._attempted = True
            dialog.show()
            if not self._current(generation) or not panel.isVisible():
                self._close_dialog(dialog)
        except Exception:
            self._close_dialog(dialog)
            raise

    def _closed(self, dialog: Any) -> None:
        if self._dialog is dialog:
            self._dialog = None

    def _close_dialog(self, dialog: Any = None) -> None:
        if dialog is None:
            dialog = self._dialog
        if dialog is None or _gui_application() is None:
            return
        if self._dialog is dialog:
            self._dialog = None
        try:
            if isValid(dialog):
                try:
                    dialog.close()
                finally:
                    if _gui_application() is not None and isValid(dialog):
                        dialog.deleteLater()
        except Exception:
            _warning()

    def invalidate(self, *, final: bool = False) -> None:
        self._generation += 1
        self._queued = False
        self._host = self._ui = None
        self._disposed |= final
        self._close_dialog()

    def refresh(self) -> None:
        dialog = self._dialog
        generation = self._generation
        if dialog is None:
            return
        try:
            if not self._current(generation):
                self._close_dialog(dialog)
                return
            panel = self._panel()
            if panel is None:
                return
            title, body = panel._ts(_TITLE), panel._ts(_BODY)
            if not self._current(generation):
                self._close_dialog(dialog)
                return
            dialog.setWindowTitle(title)
            dialog.setText(body)
            if not self._current(generation):
                self._close_dialog(dialog)
        except Exception:
            self._close_dialog(dialog)
            _warning()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        try:
            if watched is self._panel():
                if event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
                    self.invalidate()
                elif event.type() == QEvent.Type.Show:
                    self.request()
            elif watched is self._dialog and event.type() == QEvent.Type.LanguageChange:
                self.refresh()
        except Exception:
            _warning()
        return False


def request_library_load_feedback(panel: Any) -> None:
    issue = getattr(panel, "_initial_library_load_issue", None)
    if issue is None:
        return
    notice = None
    try:
        if _gui_application() is None or not isValid(panel):
            _warning()
            return
        notice = getattr(panel, "_library_load_feedback", None)
        if notice is None:
            notice = LibraryLoadFeedback(panel, issue)
            panel._library_load_feedback = notice
        notice.request()
    except Exception:
        if isinstance(notice, LibraryLoadFeedback):
            notice.invalidate()
        _warning()


def retire_library_load_feedback(panel: Any, *, final: bool = False) -> None:
    notice = getattr(panel, "_library_load_feedback", None)
    if notice is not None:
        try:
            notice.invalidate(final=final)
        except Exception:
            _warning()


def refresh_library_load_feedback(panel: Any) -> None:
    notice = getattr(panel, "_library_load_feedback", None)
    if notice is not None:
        notice.refresh()
