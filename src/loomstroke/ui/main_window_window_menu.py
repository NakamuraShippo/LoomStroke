"""Window menu construction and toolbar visibility coordination."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolBar

from ._dock_safe import is_valid
from .panel_ids import is_window_menu_panel, panel_menu_categories, panel_menu_category

if TYPE_CHECKING:
    from .menu_catalog import MenuDefinition

logger = logging.getLogger(__name__)


class MainWindowWindowMenuCoordinator:
    """Build Window menu actions and keep toolbar action state synchronized."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def build_window_menu(self) -> None:
        """Build Window menu with panel management."""
        window = self._window
        for panel_id, dock in list(window._panel_docks.items()):
            if panel_id not in window._panel_actions:
                title = window._panel_titles.get(panel_id, panel_id)
                act = window._mk_action(
                    title,
                    checkable=True,
                    checked=dock.isVisible(),
                )
                act.toggled.connect(lambda on, pid=panel_id: window._toggle_dock_from_menu(pid, on))
                dock.visibilityChanged.connect(
                    lambda vis, pid=panel_id: window._sync_action_from_dock(pid, vis)
                )
                window._panel_actions[panel_id] = act

        for toolbar_id, toolbar in list(window._toolbars.items()):
            if toolbar_id not in window._toolbar_actions:
                title = window._TOOLBAR_TITLE_SOURCES.get(toolbar_id, toolbar.windowTitle())
                act = window._mk_action(
                    title,
                    checkable=True,
                    checked=self.is_toolbar_visible_for_menu(toolbar_id, toolbar),
                )
                act.toggled.connect(
                    lambda on, tid=toolbar_id: window._toggle_toolbar_from_menu(tid, on)
                )
                toolbar.visibilityChanged.connect(
                    lambda vis, tid=toolbar_id: window._sync_action_from_toolbar(tid, vis)
                )
                window._toolbar_actions[toolbar_id] = act

        if not is_valid(window._window_menu):
            return

        window._window_menu.clear()
        window._window_menu.addAction(
            window._mk_action(
                "&Full Screen",
                window._toggle_fullscreen,
                "F11",
                checkable=True,
            )
        )
        screen_mode_action = window._mk_action(
            "Screen &Mode",
            window._toggle_screen_mode,
            "Tab",
            checkable=True,
            checked=window._screen_mode_active,
        )
        window._screen_mode_action = screen_mode_action
        window._window_menu.addAction(screen_mode_action)
        window._window_menu.addAction(window._mk_action("&Reset Layout", window._reset_layout))
        window._window_menu.addAction(
            window._mk_action(
                "Show/Hide Guides",
                window._on_toggle_rulers,
                "R",
                checkable=True,
                checked=bool(window._settings.get_bool("view/rulers_visible", False)),
                action_id="view.toggle_rulers",
                shortcut_category="Window",
            )
        )
        window._window_menu.addSeparator()

        if window._toolbar_actions:
            toolbar_menu = window._submenu(window._window_menu, "Toolbar")
            for toolbar_id in window._TOOLBAR_TITLE_SOURCES:
                action = window._toolbar_actions.get(toolbar_id)
                if action is not None:
                    toolbar_menu.addAction(action)
            window._window_menu.addSeparator()

        window._panel_category_menus = {}
        for category in panel_menu_categories():
            category_menu = window._submenu(window._window_menu, category)
            category_menu.menuAction().setVisible(False)
            window._panel_category_menus[category] = category_menu

        show_experimental = bool(getattr(window, "_show_experimental_menus", False))
        menu_panel_ids = [
            panel_id
            for panel_id in window._panel_actions
            if is_window_menu_panel(panel_id, show_experimental=show_experimental)
        ]
        logger.info("Window menu panels: %s", menu_panel_ids)
        for panel_id in menu_panel_ids:
            self.add_panel_action(panel_id, window._panel_actions[panel_id])

    def add_panel_action(self, panel_id: str, action: QAction) -> None:
        """Place a panel toggle in its Window menu category submenu."""
        window = self._window
        category_menus = getattr(window, "_panel_category_menus", {})
        category_menu = category_menus.get(panel_menu_category(panel_id))
        if category_menu is None or not is_valid(category_menu):
            if is_valid(window._window_menu):
                window._window_menu.addAction(action)
            return
        category_menu.addAction(action)
        category_menu.menuAction().setVisible(True)

    def create_menu_toggle_action(self, definition: MenuDefinition) -> QAction:
        window = self._window
        action = QAction(window)
        label = definition.checkbox_label()
        window._translation_binder.register(action.setText, label)
        action.setCheckable(True)
        action.setChecked(window._menu_enabled(definition.menu_id))
        action.toggled.connect(
            lambda checked, menu_def=definition: window._on_optional_menu_toggle(menu_def, checked)
        )
        return action

    def on_optional_menu_toggle(self, definition: MenuDefinition, enabled: bool) -> None:
        window = self._window
        try:
            window._settings.set(definition.setting_key, bool(enabled))
        except Exception as exc:
            logger.error("Failed to update menu setting %s: %s", definition.setting_key, exc)
            action = window._menu_toggle_actions.get(definition.menu_id)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(not bool(enabled))
                action.blockSignals(False)
            return
        window._handle_setting_side_effects(
            definition.setting_key,
            bool(enabled),
            source="window",
        )

    def toggle_toolbar_from_menu(self, toolbar_id: str, on: bool) -> None:
        """Toggle toolbar visibility from the Window menu."""
        window = self._window
        floating_toolbar = getattr(window, "_floating_tool_toolbar", None)
        if toolbar_id == "tools" and floating_toolbar is not None:
            if on:
                window._show_floating_tool_toolbar_at_default()
            else:
                floating_toolbar.hide()
                window._request_native_canvas_refresh_deferred()
            self.sync_action_from_toolbar(toolbar_id, on)
            return
        toolbar = window._toolbars.get(toolbar_id)
        if toolbar is None:
            return
        toolbar.setVisible(on)

    def is_toolbar_visible_for_menu(self, toolbar_id: str, toolbar: QToolBar) -> bool:
        floating_toolbar = getattr(self._window, "_floating_tool_toolbar", None)
        if toolbar_id == "tools" and floating_toolbar is not None:
            return not floating_toolbar.isHidden()
        return not toolbar.isHidden()

    def sync_action_from_toolbar(self, toolbar_id: str, visible: bool) -> None:
        """Sync Window menu toolbar action state from toolbar visibility."""
        act = self._window._toolbar_actions.get(toolbar_id)
        if act and act.isChecked() != visible:
            old = act.blockSignals(True)
            act.setChecked(visible)
            act.blockSignals(old)
