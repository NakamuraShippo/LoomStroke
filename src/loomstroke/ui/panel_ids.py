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
