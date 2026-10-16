"""Compatibility entry point for the canonical filter panel plugin."""

from __future__ import annotations

from loomstroke.plugins.filter_panel import FilterPanelPlugin, create_plugin

EffectPanelPlugin = FilterPanelPlugin

__all__ = ["EffectPanelPlugin", "FilterPanelPlugin", "create_plugin"]
