"""Runtime language lifecycle coordination for MainWindow."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QCoreApplication

from ..core.events import Events, LanguageChangeEvent
from .i18n import (
    build_translator,
    layout_direction_for_language,
    resolve_language,
    translation_domains,
)

logger = logging.getLogger("loomstroke.ui.main_window")


class MainWindowLanguageCoordinator:
    """Coordinate MainWindow runtime language switching and event propagation."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def apply_language_setting(self) -> None:
        window = self._window
        language = window._settings.get("ui/language", "system")
        if not isinstance(language, str):
            language = "system"
        self.set_language(language)

    def set_language(self, language: str) -> None:
        window = self._window
        target = resolve_language(language)
        if target == getattr(window, "_current_language", "system"):
            return
        if window._qt_translator is not None:
            QCoreApplication.removeTranslator(window._qt_translator)
            window._qt_translator = None
        app = QCoreApplication.instance()
        translator = build_translator(target)
        if translator is None:
            window._current_language = "system"
            logger.debug("Translator not found for language: %s", target)
            if app is not None and hasattr(app, "setLayoutDirection"):
                app.setLayoutDirection(layout_direction_for_language("system"))
            translation_domains.reload(target)
            self.emit_language_changed("system")
            window._refresh_translations()
            return
        QCoreApplication.installTranslator(translator)
        window._qt_translator = translator
        window._current_language = target
        logger.info("Applied UI language: %s", target)
        if app is not None and hasattr(app, "setLayoutDirection"):
            app.setLayoutDirection(layout_direction_for_language(target))
        translation_domains.reload(target)
        self.emit_language_changed(target)
        window._refresh_translations()

    def emit_language_changed(self, language: str) -> None:
        window = self._window
        payload = LanguageChangeEvent(language=language)
        with suppress(Exception):
            window._event_bus.emit(Events.LANGUAGE_CHANGED, payload)
        host_api = getattr(window, "_host_api", None)
        host_events = getattr(host_api, "events", None) if host_api else None
        if host_events and host_events is not window._event_bus:
            emitter = getattr(host_events, "emit", None) or getattr(host_events, "publish", None)
            if callable(emitter):
                with suppress(Exception):
                    emitter(Events.LANGUAGE_CHANGED, payload)
