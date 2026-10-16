"""Translation-aware menu shell coordination for MainWindow."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMenuBar

from .translations import (
    translate_runtime_text,
    translated_literal_domain,
)


class MainWindowTranslationMenuShellCoordinator:
    """Coordinate MainWindow source tokens, menu title binding, and action slots."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def ts(self, text: str) -> str:
        """Mark a string for MainWindow-domain translation."""

        return translated_literal_domain("MainWindow", text, use_fallback=True)

    def translation_token(self, text: str) -> str:
        source_text = getattr(text, "source_text", None)
        if isinstance(source_text, str) and source_text:
            return text
        return self.ts(text)

    def apply_window_title_translation(self, text: str) -> None:
        self._window._base_window_title = text
        self.update_window_title()

    def update_window_title(self) -> None:
        window = self._window
        title = window._base_window_title
        if window._document_path:
            title = f"{window._document_path.name} - {title}"
        if window._document_dirty:
            title = f"* {title}"
        window.setWindowTitle(title)

    def menu(self, bar: QMenuBar, title: str) -> QMenu:
        """Add a runtime-translatable menu to the menu bar."""

        window = self._window
        title_source = self.translation_token(title)
        translated = translate_runtime_text(title_source, window.tr)
        menu: QMenu = bar.addMenu(translated)
        window._translation_binder.register(menu.setTitle, title_source)
        return menu

    def submenu(self, parent: QMenu, title: str) -> QMenu:
        """Add a runtime-translatable submenu."""

        window = self._window
        title_source = self.translation_token(title)
        translated = translate_runtime_text(title_source, window.tr)
        submenu = parent.addMenu(translated)
        window._translation_binder.register(submenu.setTitle, title_source)
        return submenu

    def refresh_translations(self) -> None:
        window = self._window
        window._translation_binder.set_translator(window.tr)
        window._translation_binder.retranslate()
        window._refresh_panel_titles()
        if (
            window.menuBar() is not None
            and hasattr(window, "_menu_handles")
            and hasattr(window, "_shortcut_labels")
        ):
            window._build_menubar()

    def connect_action_slot(self, action: QAction, slot: Callable[..., Any]) -> None:
        """Connect QAction.triggered while adapting optional checked arguments."""

        if self.slot_accepts_checked(slot):
            action.triggered.connect(cast(Callable[[bool], None], slot))
        else:
            noarg_slot = cast(Callable[[], None], slot)

            def _invoke(_checked: bool = False) -> None:
                noarg_slot()

            action.triggered.connect(_invoke)

    @staticmethod
    def slot_accepts_checked(slot: Callable[..., Any]) -> bool:
        """Return True if slot accepts QAction's checked flag."""

        try:
            return len(inspect.signature(slot).parameters) >= 1
        except (TypeError, ValueError):
            return False
