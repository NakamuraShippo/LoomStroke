"""Guide visibility, locking and clear actions for the View menu."""

from __future__ import annotations

from typing import Any

from .canvas.composition_interaction import clear_composition_guides
from .dialogs.binding_margin_dialog import open_binding_margin
from .dialogs.bleed_extension_dialog import open_bleed_extension
from .dialogs.cover_template_dialog import open_cover_template
from .dialogs.format_guides_dialog import open_format_guides
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
            clear_composition_guides(window.canvas, window._settings)

    def update_enabled() -> None:
        target = controller()
        action.setEnabled(target is not None and not target.guides_locked)

    action = window._mk_action("Clear All Guides", clear)
    submenu.aboutToShow.connect(update_enabled)
    update_enabled()
    submenu.addAction(action)
    submenu.addAction(window._mk_action("Guide Templates...", lambda: open_guide_templates(window)))
    binding_action = window._mk_action("Binding Margin...", lambda: open_binding_margin(window))

    def update_binding_enabled() -> None:
        target = controller()
        binding_action.setEnabled(target is not None and not target.guides_locked)

    submenu.aboutToShow.connect(update_binding_enabled)
    update_binding_enabled()
    submenu.addAction(binding_action)
    format_action = window._mk_action("Page Format Guides...", lambda: open_format_guides(window))

    def update_format_enabled() -> None:
        target = controller()
        format_action.setEnabled(target is not None and not target.guides_locked)

    submenu.aboutToShow.connect(update_format_enabled)
    update_format_enabled()
    submenu.addAction(format_action)

    def show_trim(checked: bool) -> None:
        window._settings.set("view/trim_preview", checked)
        widget = getattr(window, "canvas", None)
        update = getattr(widget, "update", None)
        if callable(update):
            update()

    trim_action = window._mk_action("Preview Trimmed Page", show_trim, checkable=True)
    trim_action.setChecked(bool(window._settings.get("view/trim_preview", False)))
    submenu.addAction(trim_action)
    submenu.addAction(window._mk_action("Extend Bleed...", lambda: open_bleed_extension(window)))
    submenu.addAction(window._mk_action("Cover Template...", lambda: open_cover_template(window)))
