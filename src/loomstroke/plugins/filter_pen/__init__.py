"""Independent tool plugin applying the Add Filter Layer catalog."""

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

from .plugin import PLUGIN_METADATA, FilterPenPlugin, create_plugin
from .tool import FilterPenTool

__all__ = ["PLUGIN_METADATA", "FilterPenPlugin", "FilterPenTool", "create_plugin"]

EffectPenPlugin = FilterPenPlugin
EffectPenTool = FilterPenTool

_bind_legacy_names(
    __name__, {"EffectPenPlugin": "FilterPenPlugin", "EffectPenTool": "FilterPenTool"}
)
