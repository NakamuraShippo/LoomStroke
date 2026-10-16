"""Lazily mounted native frontend shared by authoring and layer panels."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget
from shiboken6 import delete, isValid

from loomstroke.platform.async_task_owner import AsyncTaskOwner
from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.comfyui.server_registry import get_registry
from loomstroke.plugins.comfyui.session_target import confirm
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)


def tr(text: str) -> str:
    return translate_with_fallback("NativeComfyUI", text)


class NativePanel(QWidget, AsyncTaskOwner):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        AsyncTaskOwner.__init__(self)
        self.view: Any = None
        self.body = QVBoxLayout(self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self._busy = False

    def button(self, source: str) -> QPushButton:
        button = QPushButton(tr(source), self)
        button.setProperty("native_source_text", source)
        return button

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            for button in self.findChildren(QPushButton):
                source = button.property("native_source_text")
                if source:
                    button.setText(tr(source))

    def run(self, operation: Any) -> Any:
        if self._busy:
            operation.close()
            return
        self._busy = True

        async def execute() -> None:
            try:
                await operation
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = tr(str(exc)) if str(exc) else type(exc).__name__
                logger.warning("Native ComfyUI operation failed: %s", detail)
                self.status.setText(tr("ComfyUI error: %s") % detail)
            finally:
                self._busy = False

        try:
            task = self.create_owned_task(execute(), consume_exceptions=True)
            task.add_done_callback(lambda done: operation.close() if done.cancelled() else None)
            return task
        except RuntimeError:
            self._busy = False
            operation.close()
            raise

    async def connect_view(self, url: str, *, generation: bool = False) -> Any:
        settings = get_settings_manager()
        if not is_external_services_allowed(settings):
            raise ValueError(tr("Enable external services in Preferences to use ComfyUI."))
        if self.view is not None:
            if self.view.url.rstrip("/") != url.rstrip("/"):
                raise ValueError(tr("The server changed. Close and reopen the node editor."))
            return self.view
        from .view import NativeComfyView  # noqa: PLC0415 - load Chromium only on demand

        self.status.setText(tr("Connecting to ComfyUI…"))
        self.view = NativeComfyView(url, settings, self, persistent=generation)
        self.view.interceptor.allow_generation = generation
        self.view.setMinimumHeight(380)
        self.body.addWidget(self.view, 1)
        try:
            await self.view.open_frontend()
        except TimeoutError as exc:
            self.dispose_view()
            raise ValueError(
                tr("ComfyUI did not finish loading. Check the server connection and reload nodes.")
            ) from exc
        except BaseException:
            self.dispose_view()
            raise
        self.status.setText(tr("Connected"))
        confirm(get_registry(), url)
        return self.view

    def dispose_view(self) -> None:
        if self.view is not None:
            self.view.probe_page.closing_probe = True
            self.view.setHtml("")
            from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: PLC0415

            # setPage detaches the old page but does not necessarily destroy it.
            old_page = self.view.page()
            self.view.setPage(QWebEnginePage(self.view))
            if isValid(old_page):
                delete(old_page)
            self.body.removeWidget(self.view)
            self.view.deleteLater()
            self.view = None

    def dispose(self) -> None:
        self.cancel_owned_tasks()
        self._busy = False
        self.dispose_view()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        self.dispose()
        super().closeEvent(event)
