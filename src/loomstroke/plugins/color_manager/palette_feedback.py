"""Observe palette operations and present fixed feedback for their live panel."""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from shiboken6 import isValid

from loomstroke.ui.palette_feedback_strings import LOAD_FAILED, SAVE_FAILED, SETTINGS_FAILED, TITLE
from loomstroke.ui.translations import translate_with_fallback

from .errors import PaletteImportError, PaletteSettingsUpdateError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaletteTaskTarget:
    """The repository and panel owned by an operation before its first await."""

    owner: weakref.ReferenceType[Any]
    repository: Any
    panel: Any

    @classmethod
    def capture(cls, plugin: Any) -> PaletteTaskTarget:
        return cls(weakref.ref(plugin), plugin._repository, plugin._panel)

    def is_current(self) -> bool:
        plugin = self.owner()
        app = QApplication.instance()
        return (
            plugin is not None
            and plugin._repository is self.repository
            and plugin._panel is self.panel
            and isinstance(self.panel, QWidget)
            and isValid(self.panel)
            and isinstance(app, QApplication)
            and QThread.currentThread() is app.thread()
        )


class PaletteFeedbackPresenter(QObject):
    ready = Signal(object, object, object)

    def __init__(self, panel: QWidget) -> None:
        super().__init__(panel)
        self._dialog: QMessageBox | None = None
        self._target: PaletteTaskTarget | None = None
        self._kind: str | None = None
        self.ready.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def request(self, target: PaletteTaskTarget, kind: str | None, palettes: Any) -> None:
        if isValid(self):
            self.ready.emit(target, kind, palettes)

    @Slot(object, object, object)
    def _show(self, target: PaletteTaskTarget, kind: str | None, palettes: Any) -> None:
        if not target.is_current():
            return
        pending = _has_settings_warning(target.repository)
        kind = "settings" if pending or kind == "settings" else kind
        if kind is None:
            return
        try:
            if kind == "settings" and palettes is not None:
                target.panel.set_palettes(palettes)
                if not target.is_current():
                    return
            dialog = self._dialog
            if dialog is None or not isValid(dialog) or not dialog.isVisible():
                dialog = QMessageBox(target.panel)
                dialog.setWindowModality(Qt.WindowModality.NonModal)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
                dialog.installEventFilter(self)
                self._dialog = dialog
            self._target, self._kind = target, kind
            self._translate(dialog)
            dialog.show()
            if not target.is_current():
                if isValid(dialog):
                    dialog.close()
                return
            if isValid(dialog) and dialog.isVisible() and pending:
                target.repository.take_settings_warning()
        except Exception:
            dialog = self._dialog
            if dialog is not None and isValid(dialog) and not dialog.isVisible():
                dialog.deleteLater()
                self._dialog = None
            logger.warning("Palette feedback could not be displayed")

    def _translate(self, dialog: QMessageBox) -> None:
        source = {"load": LOAD_FAILED, "settings": SETTINGS_FAILED}.get(
            self._kind or "save", SAVE_FAILED
        )
        dialog.setWindowTitle(translate_with_fallback("PalettePersistenceNotice", TITLE))
        dialog.setText(translate_with_fallback("PalettePersistenceNotice", source))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        dialog = self._dialog
        if (
            event.type() == QEvent.Type.LanguageChange
            and dialog is not None
            and watched is dialog
            and self._target is not None
            and self._target.is_current()
        ):
            self._translate(dialog)
        return super().eventFilter(watched, event)

    def dismiss(self) -> None:
        if self._dialog is not None and isValid(self._dialog):
            self._dialog.close()


def install_palette_feedback(panel: Any) -> PaletteFeedbackPresenter | None:
    app = QApplication.instance()
    if not isinstance(panel, QWidget) or not isValid(panel) or not isinstance(app, QApplication):
        return None
    if QThread.currentThread() is not app.thread():
        return None
    presenter = getattr(panel, "_palette_feedback_presenter", None)
    if not isinstance(presenter, PaletteFeedbackPresenter) or not isValid(presenter):
        presenter = PaletteFeedbackPresenter(panel)
        panel._palette_feedback_presenter = presenter
    return presenter


def dismiss_palette_feedback(panel: Any) -> None:
    presenter = getattr(panel, "_palette_feedback_presenter", None)
    if isinstance(presenter, PaletteFeedbackPresenter) and isValid(presenter):
        presenter.dismiss()


def _has_settings_warning(repository: Any) -> bool:
    return bool(getattr(repository, "has_settings_warning", lambda: False)())


async def _finish_feedback(target: PaletteTaskTarget, kind: str | None) -> None:
    if not target.is_current():
        return
    palettes = None
    if _has_settings_warning(target.repository) or kind == "settings":
        try:
            palettes = await target.repository.list_palettes()
        except Exception:
            logger.warning("Palette feedback could not refresh the confirmed palette")
        if not target.is_current():
            return
    presenter = install_palette_feedback(target.panel)
    if presenter is not None:
        presenter.request(target, kind, palettes)


async def run_palette_task(target: PaletteTaskTarget, operation: Any) -> None:
    """Finish feedback even after cancellation, without hiding the cancellation."""
    if not target.is_current():
        operation.close()
        return
    kind: str | None = None
    cancelled: asyncio.CancelledError | None = None
    try:
        await operation
    except asyncio.CancelledError as error:
        cancelled = error
    except PaletteSettingsUpdateError:
        kind = "settings"
        logger.warning("Palette file committed but its location setting was not updated")
    except PaletteImportError:
        kind = "load"
        logger.warning("Palette file could not be loaded")
    except Exception:
        code = getattr(operation, "cr_code", None)
        kind = "load" if getattr(code, "co_name", None) == "_load_palettes" else "save"
        logger.warning("Palette operation did not complete")
    finally:
        feedback = asyncio.create_task(_finish_feedback(target, kind))
        while not feedback.done():
            try:
                await asyncio.shield(feedback)
            except asyncio.CancelledError as error:
                cancelled = cancelled or error
            except Exception:
                logger.warning("Palette feedback could not be completed")
                break
        if not feedback.cancelled():
            try:
                feedback.result()
            except Exception:
                logger.warning("Palette feedback could not be completed")
    if cancelled is not None:
        raise cancelled
