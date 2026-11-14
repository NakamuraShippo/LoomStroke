"""Refresh a desktop plugin's panels before its update is committed."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..plugins.sdk.base import PluginType
from ..plugins.sdk.errors import PluginInitError

logger = logging.getLogger(__name__)


def _is_enabled_panel(plugin: Any) -> bool:
    types = getattr(getattr(plugin, "metadata", None), "types", None)
    return bool(getattr(plugin, "enabled", False) and PluginType.UI_PANEL in (types or ()))


class PluginUpdatePanelRefresh:
    def __init__(
        self,
        window: Any,
        plugin_id: str,
        previous: Any,
        refresh: Callable[[str, Any], bool],
    ) -> None:
        self._window = window
        self._plugin_id = plugin_id
        self._previous = previous
        self._refresh = refresh
        self._confirmed = False
        save_state = getattr(window, "saveState", None)
        self._layout = (
            save_state() if _is_enabled_panel(previous) and callable(save_state) else None
        )

    def confirm(self, candidate: Any) -> None:
        if _is_enabled_panel(self._previous) or _is_enabled_panel(candidate):
            if not self._refresh(self._plugin_id, candidate):
                # Remove any partially registered candidate panels before the
                # registry restores and reactivates the previous plugin.
                self._refresh(self._plugin_id, None)
                raise PluginInitError(f"Could not refresh panels for plugin '{self._plugin_id}'")
            if _is_enabled_panel(candidate):
                self._restore_layout()
        self._confirmed = True

    def after_failure(self, restored: Any, *, panels_removed: bool = False) -> None:
        if restored is self._previous and not panels_removed:
            return
        if self._refresh(self._plugin_id, restored) and _is_enabled_panel(restored):
            self._restore_layout()

    def after_success(self, plugin: Any) -> None:
        # Legacy/injected registries may not run the confirmation callback.
        if not self._confirmed:
            self._refresh(self._plugin_id, plugin)

    def _restore_layout(self) -> None:
        if self._layout is not None:
            try:
                if not self._window.restoreState(self._layout):
                    logger.warning("Could not restore panel layout: %s", self._plugin_id)
            except Exception:
                logger.warning("Could not restore panel layout: %s", self._plugin_id, exc_info=True)
