"""Session defaults and command-backed whole-object Japanese typography edits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loomstroke.core.japanese_text.typography import normalize_typography

from .character_style_controller import apply_selection_style


def _session_owner(tool: Any) -> Any:
    host = tool.host_api
    return getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None) or tool


def session_typography(tool: Any) -> dict[str, Any]:
    """Read transient creation defaults independently of selection-driven UI state."""
    return normalize_typography(getattr(_session_owner(tool), "_text_typography_defaults", None))


def apply_typography(tool: Any, changes: Any) -> None:
    """Merge a UI patch once, preserving unrelated formatting and one undo group."""
    if not isinstance(changes, Mapping) or not changes:
        return
    if tool._inline_state is not None and not tool._edit_lifecycle.same_page():
        return
    current = tool._properties.current_values()["typography"]
    value = normalize_typography({**current, **changes})
    if apply_selection_style(tool, "paragraph_typography", value):
        return
    owner = _session_owner(tool)
    owner._text_typography_defaults = normalize_typography({**session_typography(tool), **changes})
    if tool._inline_editor is None:
        tool._ensure_prop_transaction()
    tool._apply_settings_to_selected_text("typography", value)
    tool._refresh_inline_editor_after_setting_change()
    tool._properties.sync(force=True)
