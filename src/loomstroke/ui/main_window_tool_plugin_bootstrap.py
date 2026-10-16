"""Tool plugin registration and tool panel host sync for MainWindow."""

from __future__ import annotations

import logging
from typing import Any

from ..plugins.sdk.base import PluginType

logger = logging.getLogger(__name__)


class MainWindowToolPluginBootstrapCoordinator:
    """Coordinate tool plugin registration and tool-related panel wiring."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def register_tools_from_registry(self, tool_manager: Any, registry: Any) -> None:
        if not tool_manager or not registry:
            return
        for plugin in registry.by_type(PluginType.TOOL):
            self.register_tools_from_plugin(tool_manager, plugin)
        if not tool_manager.get_active_tool():
            try:
                tool_manager.activate_tool("basic_pen")
                logger.info("Activated default tool: basic_pen")
            except Exception as exc:
                logger.warning("Failed to activate default tool: %s", exc)

    def register_tools_from_plugin(self, tool_manager: Any, plugin: Any) -> None:
        tools_to_register = self.resolve_plugin_tools(plugin)
        for tool in tools_to_register:
            if not hasattr(tool, "descriptor"):
                continue
            self.register_single_tool(tool_manager, tool)

    def resolve_plugin_tools(self, plugin: Any) -> list[Any]:
        get_tools = getattr(plugin, "get_tools", None)
        if not callable(get_tools):
            return [plugin]
        try:
            return list(get_tools())
        except Exception as exc:
            logger.warning("Plugin %s.get_tools() failed: %s", plugin, exc)
            return []

    def register_single_tool(self, tool_manager: Any, tool: Any) -> None:
        try:
            descriptor = tool.descriptor()
        except Exception as exc:
            logger.warning("Failed to read tool descriptor: %s", exc)
            return

        try:
            if hasattr(tool_manager, "has_tool") and tool_manager.has_tool(descriptor.id):
                logger.debug(
                    "Tool %s already registered; skipping duplicate",
                    descriptor.id,
                )
            else:
                tool_manager.register_tool(tool)
                logger.info("Registered tool: %s", descriptor.id)
        except Exception as exc:
            logger.warning(
                "Failed to register tool %s: %s",
                getattr(descriptor, "id", "unknown"),
                exc,
            )

    def sync_tool_properties_panel_tool_manager(self) -> None:
        panel = getattr(self._window, "_tool_properties_panel", None)
        if panel is None:
            return

        manager = getattr(self._window, "tool_manager", None)
        if manager is None:
            manager = getattr(getattr(self._window, "host", None), "_tool_manager", None)

        setter = getattr(panel, "set_tool_manager", None)
        if callable(setter):
            setter(manager)

    def attach_host_to_tool_panels(self, host: Any) -> None:
        for attr_name in ("_layer_panel", "_tool_list_panel"):
            panel = getattr(self._window, attr_name, None)
            setter = getattr(panel, "set_host_api", None)
            if callable(setter):
                setter(host)

        tool_properties_panel = getattr(self._window, "_tool_properties_panel", None)
        setter = getattr(tool_properties_panel, "set_host_api", None)
        if callable(setter):
            setter(host)
        if tool_properties_panel is not None:
            self.sync_tool_properties_panel_tool_manager()
