"""Plugin menu contribution coordination for MainWindow."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMenu


class MainWindowPluginMenuContributionCoordinator:
    """Materialize plugin-contributed submenu actions."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def populate_plugin_extra_menus(self, menu: QMenu) -> None:
        window = self._window
        registry = window._plugin_registry
        if not registry:
            return

        for plugin in registry.all():
            contrib = getattr(plugin, "menu_contributions", None)
            if callable(contrib):
                submenu_title, actions = contrib()
                sub = window._submenu(menu, submenu_title)
                for action_def in actions:
                    sub.addAction(
                        window._mk_action(
                            action_def["title"],
                            action_def["handler"],
                            action_def.get("shortcut"),
                            action_id=action_def.get("id"),
                            shortcut_category="Plugins",
                        )
                    )
