"""Compatibility imports for the renamed Filter Pen plugin."""

from loomstroke.plugins.filter_pen import (
    PLUGIN_METADATA,
    FilterPenPlugin,
    FilterPenTool,
    create_plugin,
)

EffectPenPlugin = FilterPenPlugin
EffectPenTool = FilterPenTool

__all__ = ["PLUGIN_METADATA", "EffectPenPlugin", "EffectPenTool", "create_plugin"]
