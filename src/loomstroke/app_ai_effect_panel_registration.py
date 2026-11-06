"""Connect standalone image panels to the application's installed preset library."""

from __future__ import annotations

import logging
from typing import Any

from .plugins.ai_effect.panel_presets import (
    REGISTER_PANEL_PRESET,
    PanelPresetRequest,
    register_panel_preset,
)

logger = logging.getLogger(__name__)


class PanelPresetRegistrationBinding:
    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator
        # EventBus stores weak references; retain the bound handler.
        self._handler = self._register
        coordinator._host_api.events.on(REGISTER_PANEL_PRESET, self._handler)

    def _register(self, request: Any) -> None:
        if not isinstance(request, PanelPresetRequest) or request.preset is not None:
            return
        coordinator = self._coordinator
        try:
            request.preset = register_panel_preset(
                request,
                coordinator._ai_effect_gateway_registry,
                coordinator._ai_effect_preset_library,
            )
        except Exception:
            # Do not log panel content, exception payloads or credentials.
            logger.warning("Could not register image panel preset")
            return
        manager = getattr(coordinator, "_ai_effect_preset_manager_window", None)
        if manager is not None:
            try:
                manager.refresh()
            except Exception:
                logger.warning("Could not refresh AI Effect Manager after panel registration")
