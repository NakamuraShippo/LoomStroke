"""Settings and translation bootstrap ordering for MainWindow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..core.event_bus_extensions import get_extended_event_bus
from ..platform import get_settings_manager
from ..platform.settings_manager import SettingsManager
from .main_window_settings_applier import MainWindowSettingsApplier
from .translations import TranslationBinder


class MainWindowSettingsTranslationBootstrapCoordinator:
    """Initialize settings, translation state, and startup setting effects."""

    def __init__(
        self,
        window: Any,
        *,
        event_bus_factory: Callable[[], Any] = get_extended_event_bus,
        settings_manager_factory: Callable[[], SettingsManager] = get_settings_manager,
        settings_applier_type: Callable[[Any, SettingsManager], Any] = MainWindowSettingsApplier,
        translation_binder_type: Callable[[Callable[[str], str]], Any] = TranslationBinder,
    ) -> None:
        self._window = window
        self._event_bus_factory = event_bus_factory
        self._settings_manager_factory = settings_manager_factory
        self._settings_applier_type = settings_applier_type
        self._translation_binder_type = translation_binder_type

    def initialize_state(self) -> None:
        """Prepare settings and translation state used during early MainWindow setup."""
        window = self._window
        window._event_bus = self._event_bus_factory()
        window._settings = self._settings_manager_factory()
        window._settings_applier = self._settings_applier_type(window, window._settings)
        window._qt_translator = None
        window._current_language = "system"
        window._translation_binder = self._translation_binder_type(window.tr)
        window._panel_title_sources = {}
        window._panel_title_contexts = {}
        window._panel_title_source_defaults = dict(window._PANEL_TITLE_SOURCE_DEFAULTS)
        window._panel_titles = {}
        window._panel_actions = {}
        window._panel_docks = {}

    def apply_startup_settings(self) -> None:
        """Apply constructor-time settings in the established startup order."""
        window = self._window
        window._show_experimental_menus = window._settings.get_bool(
            "ui/show_experimental_menus",
            False,
        )
        window._apply_language_setting()
        window._apply_recompose_fps(window._settings.get("performance/recompose_fps", 60))


__all__ = ["MainWindowSettingsTranslationBootstrapCoordinator"]
