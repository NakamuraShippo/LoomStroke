from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QT_TRANSLATE_NOOP


@dataclass(frozen=True)
class MenuDefinition:
    """Metadata describing an optional main menu entry."""

    menu_id: str
    title: str
    setting_key: str
    builder: str
    enabled_by_default: bool = False
    preference_label: str | None = None

    def checkbox_label(self) -> str:
        """Return a mnemonic-free label for checkbox surfaces."""

        label = self.preference_label or self.title
        return label.replace("&&", "&").replace("&", "")


MENU_DEFINITIONS: Mapping[str, MenuDefinition] = {
    "view": MenuDefinition(
        "view",
        cast(str, QT_TRANSLATE_NOOP("MainWindow", "&View")),
        "ui/menus/view/enabled",
        "_build_view_menu",
        enabled_by_default=True,
        preference_label=cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "View menu")),
    ),
    "layer": MenuDefinition(
        "layer",
        cast(str, QT_TRANSLATE_NOOP("MainWindow", "&Layer")),
        "ui/menus/layer/enabled",
        "_build_layer_menu",
        enabled_by_default=True,
        preference_label=cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Layer menu")),
    ),
    "filter": MenuDefinition(
        "filter",
        cast(str, QT_TRANSLATE_NOOP("MainWindow", "&Filter")),
        "ui/menus/filter/enabled",
        "_build_filter_menu",
        enabled_by_default=True,
        preference_label=cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Filter menu")),
    ),
    "tools": MenuDefinition(
        "tools",
        cast(str, QT_TRANSLATE_NOOP("MainWindow", "&Tools")),
        "ui/menus/tools/enabled",
        "_build_tools_menu",
        preference_label=cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Tools menu")),
    ),
    "plugins": MenuDefinition(
        "plugins",
        cast(str, QT_TRANSLATE_NOOP("MainWindow", "&Plugins")),
        "ui/menus/plugins/enabled",
        "_build_plugins_menu",
        preference_label=cast(str, QT_TRANSLATE_NOOP("PreferencesDialog", "Plugins menu")),
    ),
}


MENU_IDS: tuple[str, ...] = tuple(MENU_DEFINITIONS.keys())
