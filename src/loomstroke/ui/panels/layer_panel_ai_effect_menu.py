"""AI Effect add-menu builder for :mod:`layer_panel`."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QMenu

logger = logging.getLogger(__name__)


class LayerPanelAIEffectMenuBuilder:
    """Build the LayerPanel "Add AI Effect Layer" preset menu."""

    def __init__(self, panel: Any) -> None:
        self._panel = panel

    def show_add_menu(self) -> None:
        panel = self._panel
        menu = QMenu(panel)
        self.rebuild(menu)
        panel._ai_effect_menu_refs = [menu]
        menu.exec(
            panel._add_ai_effect_btn.mapToGlobal(QPoint(0, panel._add_ai_effect_btn.height()))
        )

    def rebuild(self, menu: QMenu) -> None:
        """Refresh the Add AI Effect Layer popup from the library getter."""
        panel = self._panel
        menu.clear()
        panel._ai_effect_menu_refs = [menu]
        applier = getattr(panel, "_ai_effect_preset_applier", None)
        if applier is None:
            self._add_disabled_placeholder(menu)
            return
        getter = getattr(panel, "_ai_effect_preset_library_getter", None)
        presets: list[Any]
        if getter is None:
            presets = []
        else:
            try:
                presets = list(getter())
            except Exception:  # noqa: BLE001 — never crash a menu
                logger.warning("Add AI Effect Layer: preset list failed", exc_info=True)
                presets = []
        if not presets:
            self._add_disabled_placeholder(menu)
            return
        categorized: dict[str, list[Any]] = {}
        for preset in presets:
            preset_id = getattr(preset, "preset_id", "")
            if not preset_id:
                continue
            category = str(getattr(preset, "category", "") or "").strip()
            category_label = category or panel._ts("AI Effect")
            categorized.setdefault(category_label, []).append(preset)
        if not categorized:
            self._add_disabled_placeholder(menu)
            return
        for category in sorted(categorized, key=str.casefold):
            submenu = QMenu(category, menu)
            menu.addMenu(submenu)
            panel._ai_effect_menu_refs.append(submenu)
            for preset in sorted(categorized[category], key=self.preset_sort_key):
                display = getattr(preset, "name", None) or getattr(preset, "preset_id", "(unnamed)")
                preset_id = getattr(preset, "preset_id", "")
                action = submenu.addAction(str(display))
                action.triggered.connect(
                    lambda _checked=False, pid=preset_id: self.on_triggered(pid)
                )

    @staticmethod
    def preset_sort_key(preset: Any) -> tuple[str, str]:
        display = getattr(preset, "name", None) or getattr(preset, "preset_id", "")
        preset_id = getattr(preset, "preset_id", "")
        return (str(display).casefold(), str(preset_id).casefold())

    def on_triggered(self, preset_id: str) -> None:
        """Invoke the wired applier — mirrors Filter menu path."""
        panel = self._panel
        applier = getattr(panel, "_ai_effect_preset_applier", None)
        if applier is None:
            return
        try:
            applier(preset_id)
        except Exception:
            logger.exception("Add AI Effect Layer: preset %s failed to apply", preset_id)

    def _add_disabled_placeholder(self, menu: QMenu) -> None:
        action = menu.addAction(self._panel._ts("(No presets installed)"))
        action.setEnabled(False)
