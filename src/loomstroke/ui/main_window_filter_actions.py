"""Filter action coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class MainWindowFilterActionsCoordinator:
    """Coordinate Filter menu action dispatch and repeat state."""

    def __init__(self, window: Any, *, message_box: Callable[[], Any]) -> None:
        self._window = window
        self._message_box = message_box

    def initialize_state(self) -> None:
        self._window._last_filter_plugin = None

    def on_last_filter(self) -> None:
        plugin = self._window._last_filter_plugin
        if plugin:
            self.apply_filter(plugin)

    def apply_filter(self, plugin: Any) -> None:
        try:
            self._window._last_filter_plugin = plugin
            self._window._event_bus.emit("filter.apply", {"plugin": plugin})
        except Exception as exc:
            logger.debug("_apply_filter: %s", exc)
            self._message_box().warning(self._window, "Filter Error", str(exc))
