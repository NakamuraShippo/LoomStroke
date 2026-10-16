"""Register the independent Filter Pen without changing Basic Pen state."""

from __future__ import annotations

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.plugins.sdk.base import PluginBase, PluginMetadata, PluginType

from .tool import FilterPenTool

PLUGIN_METADATA = PluginMetadata(
    id="effect_pen",
    name="Filter Pen",
    version="1.0.0",
    description="Paint registered filters onto the active raster layer.",
    author="LoomStroke Team",
    types=[PluginType.TOOL],
)


class FilterPenPlugin(PluginBase):
    def __init__(self) -> None:
        super().__init__(PLUGIN_METADATA)
        self.filter_pen_tool = FilterPenTool()

    @property
    def effect_pen_tool(self):
        return self.filter_pen_tool

    @effect_pen_tool.setter
    def effect_pen_tool(self, tool):
        self.filter_pen_tool = tool

    def activate(self, host_api) -> None:
        super().activate(host_api)
        self.filter_pen_tool.activate(host_api)
        host_api.register_tool(self.filter_pen_tool)

    def deactivate(self) -> None:
        self.filter_pen_tool.deactivate()
        super().deactivate()

    def get_tools(self) -> list[FilterPenTool]:
        return [self.filter_pen_tool]


def create_plugin() -> FilterPenPlugin:
    return FilterPenPlugin()


# Compatibility aliases for existing imports.
EffectPenPlugin = FilterPenPlugin


_bind_legacy_names(
    __name__, {"EffectPenTool": "FilterPenTool", "EffectPenPlugin": "FilterPenPlugin"}
)
