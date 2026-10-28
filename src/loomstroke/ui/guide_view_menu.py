"""Guide visibility, locking and clear actions for the View menu."""

from __future__ import annotations

from typing import Any

from .dialogs.guide_templates_dialog import open_guide_templates


def populate_guide_view_menu(window: Any, menu: Any) -> None:
    submenu = window._submenu(menu, "Guides")
    for source, key, default in (
        ("Show Guides", "view/guides_visible", True),
        ("Lock Guides", "view/guides_locked", False),
        ("Snap to Canvas Edges", "view/canvas_edge_snap", True),
        ("Snap to Canvas Center", "view/canvas_center_snap", False),
    ):
        action = window._mk_action(
            source, lambda checked, key=key: window._settings.set(key, checked), checkable=True
        )
        action.setChecked(bool(window._settings.get(key, default)))
        submenu.addAction(action)

    def controller() -> Any:
        return getattr(getattr(window, "canvas", None), "_guide_controller", None)

    def clear() -> None:
        target = controller()
        if target is not None and not target.guides_locked:
            target.apply_guides([], replace=True)

    def update_enabled() -> None:
        target = controller()
        action.setEnabled(target is not None and not target.guides_locked)

    action = window._mk_action("Clear All Guides", clear)
    submenu.aboutToShow.connect(update_enabled)
    update_enabled()
    submenu.addAction(action)
    submenu.addAction(window._mk_action("Guide Templates...", lambda: open_guide_templates(window)))
