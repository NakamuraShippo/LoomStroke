"""One nonblocking failure notice owned by the current pages panel."""

from __future__ import annotations

import logging
import weakref
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from shiboken6 import isValid

from loomstroke.ui.translations import TranslatedLiteral, translate_runtime_text

logger = logging.getLogger(__name__)


class PageOperationFailureNotice(QObject):
    """Queue UI delivery; keep only the latest error in one modeless box."""

    ready = Signal(int, object, object)

    def __init__(self, owner: Any, panel: QWidget, translate: Callable[[str], str]) -> None:
        super().__init__(panel)
        self._owner = weakref.ref(owner) if owner is not None else None
        self._panel = panel
        self._translate = translate
        self._dialog: QMessageBox | None = None
        self._body = ""
        self._title: str | None = None
        self._request_serial = 0
        self._generation = 0
        self._disposed = False
        self._suppressed = False
        panel.installEventFilter(self)
        self.ready.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def owns(self, panel: Any) -> bool:
        return self._panel is panel and not self._disposed

    def request(self, body: str, *, title: str | None = None) -> None:
        if isValid(self) and not self._disposed and not self._suppressed:
            self._request_serial += 1
            self.ready.emit(self._generation, title, body)

    def _is_current(self) -> bool:
        owner = self._owner() if self._owner is not None else None
        return (
            not self._disposed
            and not self._suppressed
            and (self._owner is None or (owner is not None and owner._panel is self._panel))
            and isValid(self._panel)
        )

    @Slot(int, object, object)
    def _show(self, generation: int, title: str | None, body: str) -> None:
        if generation != self._generation or not self._is_current():
            return
        try:
            dialog = self._dialog
            if dialog is None or not isValid(dialog) or not dialog.isVisible():
                dialog = QMessageBox(self._panel)
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
                dialog.installEventFilter(self)
                self._dialog = dialog
            self._body = body
            self._title = title
            self._retranslate(dialog)
            if generation != self._generation or not self._is_current():
                self._close_dialog()
                return
            dialog.show()
            if (generation != self._generation or not self._is_current()) and isValid(dialog):
                dialog.close()
        except Exception:
            dialog, self._dialog = self._dialog, None
            if dialog is not None and isValid(dialog):
                dialog.deleteLater()
            logger.warning("Page operation failure notice could not be displayed")

    def _retranslate(self, dialog: QMessageBox) -> None:
        dialog.setWindowTitle(
            self._translate("Page Operation Failed")
            if self._title is None
            else translate_runtime_text(self._title)
        )
        dialog.setText(
            translate_runtime_text(self._body)
            if isinstance(self._body, TranslatedLiteral)
            else self._body
        )

    def _close_dialog(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None and isValid(dialog):
            dialog.close()

    def dismiss(self) -> None:
        self._disposed = True
        self._generation += 1
        self._close_dialog()
        if isValid(self._panel):
            self._panel.removeEventFilter(self)
        self.deleteLater()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._panel:
            if event.type() in (QEvent.Type.Close, QEvent.Type.Hide):
                self._generation += 1
                self._suppressed = True
                self._close_dialog()
            elif event.type() == QEvent.Type.Show:
                self._suppressed = False
        elif (
            event.type() == QEvent.Type.LanguageChange
            and watched is self._dialog
            and self._is_current()
        ):
            self._retranslate(self._dialog)
        return super().eventFilter(watched, event)


def install_page_failure_notice(
    owner: Any, translate: Callable[[str], str]
) -> PageOperationFailureNotice | None:
    panel = owner._panel
    notice = getattr(owner, "_failure_notice", None)
    if isinstance(notice, PageOperationFailureNotice) and isValid(notice) and notice.owns(panel):
        return notice
    app = QApplication.instance()
    if (
        not isinstance(panel, QWidget)
        or not isValid(panel)
        or not isinstance(app, QApplication)
        or QThread.currentThread() is not app.thread()
    ):
        return None
    if isinstance(notice, PageOperationFailureNotice) and isValid(notice):
        notice.dismiss()
    standalone = getattr(panel, "_page_failure_notice", None)
    if (
        isinstance(standalone, PageOperationFailureNotice)
        and isValid(standalone)
        and standalone.owns(panel)
        and standalone._owner is None
    ):
        standalone._generation += 1
        standalone._close_dialog()
        standalone._owner = weakref.ref(owner)
        standalone._translate = translate
        owner._failure_notice = standalone
    else:
        owner._failure_notice = PageOperationFailureNotice(owner, panel, translate)
    panel._page_failure_notice = owner._failure_notice
    return owner._failure_notice


def dismiss_page_failure_notice(owner: Any) -> None:
    notice = getattr(owner, "_failure_notice", None)
    if isinstance(notice, PageOperationFailureNotice) and isValid(notice):
        notice.dismiss()
    owner._failure_notice = None


def _widget_notice(
    panel: QWidget, translate: Callable[[str], str]
) -> PageOperationFailureNotice | None:
    app = QApplication.instance()
    if (
        not isValid(panel)
        or not isinstance(app, QApplication)
        or QThread.currentThread() is not app.thread()
    ):
        return None
    # An old reference remains intentionally: a retired plugin panel must not
    # silently become a new standalone notification owner.
    if hasattr(panel, "_page_failure_notice"):
        notice = panel._page_failure_notice
        return (
            notice
            if isinstance(notice, PageOperationFailureNotice)
            and isValid(notice)
            and notice.owns(panel)
            and notice._is_current()
            else None
        )
    notice = PageOperationFailureNotice(None, panel, translate)
    panel._page_failure_notice = notice
    return notice


def page_failure_checkpoint(
    panel: QWidget, translate: Callable[[str], str]
) -> tuple[PageOperationFailureNotice, int, Any, int] | None:
    """Observe request acceptance, including a bus notice still queued for UI delivery."""
    notice = _widget_notice(panel, translate)
    return (
        (notice, notice._request_serial, notice._owner, notice._generation)
        if notice is not None
        else None
    )


def request_widget_page_failure(
    panel: QWidget,
    title: str,
    body: str,
    translate: Callable[[str], str],
    *,
    checkpoint: tuple[PageOperationFailureNotice, int, Any, int] | None = None,
) -> None:
    notice = _widget_notice(panel, translate)
    if notice is None:
        return
    if checkpoint is not None:
        previous, serial, owner, generation = checkpoint
        if (
            notice is not previous
            or notice._owner is not owner
            or notice._request_serial != serial
            or notice._generation != generation
        ):
            return
    notice.request(body, title=title)
