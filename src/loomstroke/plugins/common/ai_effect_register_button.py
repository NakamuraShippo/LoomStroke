"""Shared registration control for standalone image generation panels."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QInputDialog, QPushButton

from loomstroke.plugins.ai_effect.panel_presets import REGISTER_PANEL_PRESET, PanelPresetRequest
from loomstroke.ui.translations import translate_with_fallback


class AIEffectRegisterButton(QPushButton):
    def __init__(
        self,
        panel: Any,
        gateway_id: str,
        settings: Callable[[], dict[str, Any]],
    ) -> None:
        super().__init__(panel)
        self._panel = panel
        self._gateway_id = gateway_id
        self._settings = settings
        self.clicked.connect(self._register)
        self._retranslate()

    @staticmethod
    def _tr(source: str) -> str:
        return translate_with_fallback("AIEffectRegisterButton", source)

    def _retranslate(self) -> None:
        self.setText(self._tr("Register in AI Effect Manager"))
        self.setToolTip(
            self._tr(
                "Save the current prompt and options as a preset. "
                "Choose input layers again when applying the preset."
            )
        )

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()

    def _register(self) -> None:
        # Capture before opening a modal dialog; never advance LivePrompt here.
        settings = deepcopy(self._settings())
        name, accepted = QInputDialog.getText(
            self, self._tr("Register in AI Effect Manager"), self._tr("Preset name:")
        )
        if not accepted:
            return
        if not name.strip():
            self._panel.append_status_translated(
                "Preset name is required", domain="AIEffectRegisterButton"
            )
            return
        request = PanelPresetRequest(self._gateway_id, name.strip(), settings)
        self.setEnabled(False)
        try:
            self._panel._host.events.emit(REGISTER_PANEL_PRESET, request)
        finally:
            self.setEnabled(True)
        source = (
            "Registered preset: {name}"
            if request.preset is not None
            else "Could not register preset. AI Effect Manager may be unavailable or saving failed."
        )
        self._panel.append_status_translated(
            source, domain="AIEffectRegisterButton", name=name.strip()
        )
