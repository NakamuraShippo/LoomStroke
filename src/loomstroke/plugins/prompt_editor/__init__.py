"""Prompt Editor plugin package."""

from __future__ import annotations

from .panel_plugin import PromptEditorPanelPlugin


def create_plugin() -> PromptEditorPanelPlugin:
    """Factory used by the plugin loader."""
    return PromptEditorPanelPlugin()


__all__ = ["PromptEditorPanelPlugin", "create_plugin"]
