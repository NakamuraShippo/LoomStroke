"""Plugin loading coordinator for :mod:`loomstroke.app`."""

from __future__ import annotations

import logging
from typing import Any

from .platform.launch_options import enabled_plugin_modules
from .plugins.sdk.base import PluginType

logger = logging.getLogger(__name__)


class PluginSystemCoordinator:
    """Own built-in and installed plugin loading for ``LoomStrokeApp``."""

    def __init__(self, app: Any) -> None:
        self._app = app
        descriptor = getattr(type(app), "_pending_tool_plugins", None)
        pending = (
            [] if isinstance(descriptor, property) else getattr(app, "_pending_tool_plugins", [])
        )
        self._pending_tool_plugins = list(pending or [])

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name == "_pending_tool_plugins":
            app = object.__getattribute__(self, "_app")
            descriptor = getattr(type(app), name, None)
            if not isinstance(descriptor, property):
                try:
                    setattr(app, name, value)
                except Exception:
                    logger.debug("Plugin pending-tool mirror failed", exc_info=True)

    async def _load_plugins(self) -> None:  # noqa: PLR0912
        """Load built-in plugins."""
        if not self.plugin_registry:
            logger.warning("Plugin registry not available")
            return

        if not self._host_api:
            logger.warning("Host API not available, skipping plugin loading")
            return

        self.plugin_registry.set_host_api(self._host_api)

        for plugin_module in enabled_plugin_modules(self.builtin_plugins):
            plugin_id: str | None = None
            try:
                plugin_id = self.plugin_registry.load_from_module(plugin_module)
                logger.info(f"Loaded plugin: {plugin_module} (ID: {plugin_id})")
            except Exception as e:
                logger.warning(f"Failed to load plugin {plugin_module}: {e}")
                continue

            if plugin_id is None:
                continue

            try:
                metadata = self.plugin_registry.get_metadata(plugin_id)
                if metadata and PluginType.TOOL in metadata.types:
                    plugin = self.plugin_registry.get(plugin_id)
                    if plugin:
                        if not hasattr(self, "_pending_tool_plugins"):
                            self._pending_tool_plugins = []
                        self._pending_tool_plugins.append(plugin)
            except Exception as e:
                logger.warning(f"Failed to inspect plugin metadata {plugin_module}: {e}")

        try:
            external_plugins = self.plugin_registry.load_installed_plugins()
            for plugin_id in external_plugins:
                logger.info(f"Loaded installed plugin: ID {plugin_id}")
                metadata = self.plugin_registry.get_metadata(plugin_id)
                if metadata and PluginType.TOOL in metadata.types:
                    plugin = self.plugin_registry.get(plugin_id)
                    if plugin:
                        if not hasattr(self, "_pending_tool_plugins"):
                            self._pending_tool_plugins = []
                        self._pending_tool_plugins.append(plugin)
        except Exception as e:  # pragma: no cover - robust startup behavior
            logger.warning(f"Failed to load installed plugins: {e}")
