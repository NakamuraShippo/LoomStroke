"""Plugin lifecycle activation coordination for MainWindow."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MainWindowPluginLifecycleCoordinator:
    """Coordinate plugin activation checks shared by MainWindow helpers."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def ensure_plugin_activated(self, plugin: Any) -> bool:
        """Ensure plugin lifecycle is ready before plugin-owned UI is used."""
        if not plugin:
            return False

        metadata = getattr(plugin, "metadata", None)
        plugin_id = getattr(metadata, "id", plugin.__class__.__name__)

        try:
            enabled = plugin.enabled
        except AttributeError:
            enabled = None

        if isinstance(enabled, bool) and enabled:
            return True

        if enabled is None and getattr(plugin, "host_api", None):
            return True

        host = getattr(self._window, "host", None) or self._window._host_api
        if host is None:
            logger.warning("Host API unavailable; cannot activate plugin %s", plugin_id)
            return False

        activate = getattr(plugin, "activate", None)
        if callable(activate):
            try:
                activate(host)
            except Exception as exc:
                logger.error("Failed to activate plugin %s: %s", plugin_id, exc, exc_info=True)
                return False

        return True
