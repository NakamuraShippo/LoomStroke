"""Opt-in custom tool settings beside the existing metadata property forms."""

from __future__ import annotations

from typing import Any


def custom_property_keys(panel: Any, metadata: Any) -> set[str]:
    """Avoid repeating fields provided by an opted-in docked options widget."""
    tool = panel._tool_plugin(metadata.tool_id)
    if not getattr(tool, "options_widget_in_properties", False):
        return set()
    return set(getattr(tool, "options_widget_property_keys", ()))


def add_custom_tool_options(panel: Any, metadata: Any) -> bool:
    if panel._bar_mode:
        return False
    tool = panel._tool_plugin(metadata.tool_id)
    if not getattr(tool, "options_widget_in_properties", False):
        return False
    widget = tool.create_options_widget()
    if widget is None:
        return False
    form = panel.specific_form
    form.addWidget(widget, form.rowCount(), 0, 1, max(1, form.columnCount()))
    widget.show()
    return True
