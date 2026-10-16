from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]

_TOOL_ICON_MAP: dict[str, str] = {
    "basic_pen": "basic_pen/icons/basic_pen.svg",
    "command_pen": "basic_pen/icons/command_pen.svg",
    "basic_eraser": "basic_eraser/icons/basic_eraser.svg",
    "basic_fill_bucket": "basic_fill_bucket/icons/basic_fill_bucket.svg",
    "basic_gradient": "basic_gradient/icons/basic_gradient.svg",
    "magic_wand": "magic_wand/icons/magic_wand.svg",
    "pattern_fill": "pattern_fill/icons/pattern_fill.svg",
    "loomstroke.basic.selection.rect": "basic_selection/icons/selection_rect.svg",
    "vector_path_tool": "vector_tools/icons/vector_path.svg",
    "vector_pen_precise": "vector_tools/icons/vector_pen_precise.svg",
    "vector_path_edit_tool": "vector_tools/icons/vector_path_edit.svg",
    "basic_move": "basic_move/icons/basic_move.svg",
    "color_tool": "color_manager/icons/color_tool.svg",
    "text_tool": "text_tool/icons/text_tool.svg",
    "text_tool_vertical": "text_tool/icons/text_tool_vertical.svg",
}


def resolve_tool_icon_path(tool_id: str) -> Path | None:
    rel = _TOOL_ICON_MAP.get(tool_id)
    if not rel:
        return None
    return _PLUGIN_ROOT / rel


def iter_tool_icon_paths() -> dict[str, Path]:
    return {tool_id: _PLUGIN_ROOT / rel for tool_id, rel in _TOOL_ICON_MAP.items()}
