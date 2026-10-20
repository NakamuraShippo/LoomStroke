"""Share the layer-filter catalog with the main menu using stable identifiers."""

from typing import Any

from PySide6.QtWidgets import QMenu

from loomstroke.core.filters import FilterRegistry
from loomstroke.ui.translations import translated_literal_domain


def populate_direct_filters(window: Any, menu: QMenu) -> None:
    filters = FilterRegistry.list_filters()
    for adjustment in (True, False):
        entries = [item for item in filters if (item[2] == "adjustment") == adjustment]
        if not entries:
            continue
        for type_id, name, _ in entries:
            action = window._mk_action(
                translated_literal_domain("FilterPanel", name, use_fallback=True),
                lambda _checked=False, tid=type_id: window._apply_filter(tid),
                action_id=f"filter.apply.{type_id}",
                shortcut_category="Filter",
            )
            action.setData(type_id)
            menu.addAction(action)
        menu.addSeparator()
