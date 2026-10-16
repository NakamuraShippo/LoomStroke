"""AI Effect preset menu coordination for MainWindow."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QMenu

logger = logging.getLogger(__name__)


class MainWindowAIPresetMenuCoordinator:
    """Bridge AI Effect preset wiring into the Filter menu."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_state(self) -> None:
        """Initialize AI preset menu injection points on the MainWindow surface."""
        self._window._ai_effect_preset_library = None
        self._window._ai_effect_preset_applier = None
        self._window._ai_effect_preset_manager_opener = None

    def set_library(self, library: Any) -> None:
        self._window._ai_effect_preset_library = library
        self._rebuild_menubar()

    def set_applier(self, applier: Any) -> None:
        self._window._ai_effect_preset_applier = applier
        self._rebuild_menubar()

    def set_manager_opener(self, opener: Any) -> None:
        self._window._ai_effect_preset_manager_opener = opener
        self._rebuild_menubar()

    def populate_manager_entry(self, menu: QMenu) -> None:
        """Append an "AI Effect Presets..." action if the opener is wired."""

        window = self._window
        opener = window._ai_effect_preset_manager_opener
        if opener is None:
            return
        menu.addAction(
            window._mk_action(
                "AI Effect &Presets...",
                opener,
                action_id="filter.ai_preset_manager",
                shortcut_category="Filter",
            )
        )

    def populate_presets_submenu(self, menu: QMenu) -> None:
        """Attach the "Installed AI Presets" submenu to the Filter menu."""

        window = self._window
        library = window._ai_effect_preset_library
        applier = window._ai_effect_preset_applier
        if library is None or applier is None:
            return
        menu.addSeparator()
        submenu = menu.addMenu(window._ts("Installed AI Presets"))
        if submenu is None:
            return
        submenu.aboutToShow.connect(lambda sub=submenu: self.rebuild_presets_submenu(sub))

    def rebuild_presets_submenu(self, submenu: QMenu) -> None:
        """Refresh the submenu from the latest preset library contents."""

        window = self._window
        submenu.clear()
        library = window._ai_effect_preset_library
        if library is None:
            return
        try:
            presets = list(library.list_all())
        except Exception as exc:  # noqa: BLE001 - library failure must not crash menus
            logger.warning("Filter menu: failed to list AI presets: %s", exc)
            presets = []
        if not presets:
            empty_action = submenu.addAction(window._ts("(No presets installed)"))
            empty_action.setEnabled(False)
            return
        for preset in presets:
            display = getattr(preset, "name", None) or getattr(preset, "preset_id", "(unnamed)")
            preset_id = getattr(preset, "preset_id", "")
            if not preset_id:
                continue
            submenu.addAction(
                window._mk_action(
                    display,
                    lambda _checked=False, pid=preset_id: window._on_ai_preset_triggered(pid),
                )
            )

    def on_preset_triggered(self, preset_id: str) -> None:
        """Route a preset menu click to the injected applier callback."""

        window = self._window
        applier = window._ai_effect_preset_applier
        if applier is None:
            return
        try:
            layer_id = applier(preset_id)
        except Exception:
            logger.exception("Failed to apply AI effect preset %s", preset_id)
            layer_id = None
        if layer_id is None:
            status = getattr(window, "statusBar", None)
            status_bar = status() if callable(status) else None
            if status_bar is not None:
                status_bar.showMessage(window._ts("Failed to add AI effect layer"), 4000)

    def _rebuild_menubar(self) -> None:
        window = self._window
        menu_bar = getattr(window, "menuBar", None)
        if callable(menu_bar) and menu_bar() is not None:
            window._build_menubar()
