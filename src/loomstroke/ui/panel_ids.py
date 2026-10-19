"""Canonical panel identifiers and legacy aliases for dock panels."""

from __future__ import annotations

from collections.abc import Mapping

PANEL_ID_ALIASES: Mapping[str, str] = {
    "color_manager_panel": "ui.color",
    "text_font_panel": "ui.text_font",
    "prompt_editor": "ui.prompt_editor",
    "nano_banana_panel": "ui.nano_banana",
    "gptimage2_panel": "ui.gptimage2",
    "comfyui_panel": "ui.comfyui",
    "production_control_panel": "ui.production_control",
}

# These IDs used to describe dock panels, but the current UI routes them
# through non-dock controls. Keep title defaults for old layout/title metadata;
# do not treat them as runtime panel registrations.
TITLE_ONLY_LEGACY_PANEL_IDS: tuple[str, ...] = ("ui.tools.list",)

PANEL_TITLE_SOURCE_DEFAULTS: Mapping[str, tuple[str, str]] = {
    "ui.brush_manager": ("Brush Manager", "MainWindow"),
    "ui.layers": ("Layers", "MainWindow"),
    "ui.preview": ("Preview", "MainWindow"),
    "ui.tools.list": ("Toolbar", "MainWindow"),
    "ui.color": ("Color", "MainWindow"),
    "ui.text_font": ("Font", "MainWindow"),
    "pages_panel": ("Pages", "MainWindow"),
    "loomstroke.shape_manager.panel": ("Shape Manager", "MainWindow"),
    "ui.prompt_editor": ("Prompt Editor", "MainWindow"),
    "ui.nano_banana": ("NanoBanana", "MainWindow"),
    "ui.gptimage2": ("GPT Image 2", "MainWindow"),
    "ui.comfyui": ("ComfyUI", "MainWindow"),
    "ui.gallery": ("Gallery", "MainWindow"),
    "ui.production_control": ("Production Control(incomplete)", "MainWindow"),
    "ui.canvas_copilot": ("Canvas Assistant(incomplete)", "MainWindow"),
    "ui.ai_effect_manager": ("AI Effects Manager", "MainWindow"),
}

# Unfinished panels stay registered (layout restore, programmatic toggles) but
# are listed in the Window menu only when ``ui/show_experimental_menus`` is on.
INCOMPLETE_PANEL_IDS: tuple[str, ...] = ("ui.production_control", "ui.canvas_copilot")

# Window menu groups panels into category submenus, in this order. Panels not
# listed here (e.g. third-party plugin panels) go to ``PANEL_MENU_OTHER_CATEGORY``.
PANEL_MENU_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Layers / Pages",
        (
            "ui.layers",
            "ui.preview",
            "pages_panel",
            "loomstroke.transform_panel.panel",
            "loomstroke.effect_panel.panel",
        ),
    ),
    (
        "Drawing",
        (
            "ui.brush_manager",
            "ui.color",
            "ui.text_font",
            "loomstroke.shape_manager.panel",
            "drawing_assist_panel",
            "loomstroke.reference_viewer.panel",
        ),
    ),
    (
        "Comic",
        (
            "comic_tools_panel",
            "comic_assets_panel",
            "comic_project_panel",
            "comic_workflow_panel",
            "links_panel",
            "actions_panel",
        ),
    ),
    (
        "AI",
        (
            "ui.prompt_editor",
            "ui.nano_banana",
            "ui.gptimage2",
            "ui.comfyui",
            "ui.gallery",
            "ui.ai_effect_manager",
            "ui.sam3_ai_select_candidates",
            "ui.sam3_ai_select_manga_mask_assistant",
            "loomstroke.generated_3d_preview.panel",
            "ui.canvas_copilot",
            "ui.production_control",
        ),
    ),
)
PANEL_MENU_OTHER_CATEGORY = "Other"

SPECIAL_PANEL_HANDLING: Mapping[str, str] = {
    "ui.comfyui": "ComfyUI fallback, service binding, and workflow layout handling",
    "ui.gallery": "generation history provider restore handling",
}


def canonical_panel_id(panel_id: str) -> str:
    """Return the stable panel id used for runtime dock registration."""
    return PANEL_ID_ALIASES.get(panel_id, panel_id)


def panel_id_aliases(panel_id: str) -> tuple[str, ...]:
    """Return legacy aliases accepted for the given panel id."""
    canonical_id = canonical_panel_id(panel_id)
    return tuple(alias for alias, target in PANEL_ID_ALIASES.items() if target == canonical_id)


def panel_lookup_ids(panel_id: str) -> tuple[str, ...]:
    """Return canonical id followed by aliases for compatibility lookups."""
    canonical_id = canonical_panel_id(panel_id)
    return (canonical_id, *panel_id_aliases(canonical_id))


def panel_title_default(panel_id: str) -> tuple[str, str] | None:
    """Return the translated title source default for a panel id or alias."""
    return PANEL_TITLE_SOURCE_DEFAULTS.get(canonical_panel_id(panel_id))


def is_window_menu_panel(panel_id: str, *, show_experimental: bool) -> bool:
    """Return whether the panel's toggle action belongs in the Window menu."""
    if show_experimental:
        return True
    return canonical_panel_id(panel_id) not in INCOMPLETE_PANEL_IDS


def panel_menu_categories() -> tuple[str, ...]:
    """Return Window menu category titles in display order."""
    return (*(title for title, _ids in PANEL_MENU_CATEGORIES), PANEL_MENU_OTHER_CATEGORY)


def panel_menu_category(panel_id: str) -> str:
    """Return the Window menu category title for a panel id or alias."""
    canonical_id = canonical_panel_id(panel_id)
    for title, panel_ids in PANEL_MENU_CATEGORIES:
        if canonical_id in panel_ids:
            return title
    return PANEL_MENU_OTHER_CATEGORY


def special_panel_note(panel_id: str) -> str | None:
    """Return why a panel intentionally stays outside normal descriptor setup."""
    return SPECIAL_PANEL_HANDLING.get(canonical_panel_id(panel_id))


__all__ = [
    "PANEL_ID_ALIASES",
    "PANEL_TITLE_SOURCE_DEFAULTS",
    "SPECIAL_PANEL_HANDLING",
    "TITLE_ONLY_LEGACY_PANEL_IDS",
    "canonical_panel_id",
    "panel_id_aliases",
    "panel_lookup_ids",
    "panel_title_default",
    "special_panel_note",
]
