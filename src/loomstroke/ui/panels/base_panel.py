"""
Base panel class for UI panels.

Per Master.md PR#4 specifications, provides panel abstraction
with async support and EventBus integration.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, cast

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ...platform import get_settings_manager
from ..translations import TranslationBinder

logger = logging.getLogger(__name__)

P = ParamSpec("P")


try:
    from qasync import asyncSlot as _asyncSlot
except ImportError:
    # asyncSlot may not be needed for all panels
    def asyncSlot(
        *_args: object, **_kwargs: object
    ) -> Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[Any]]]:
        def decorator(func: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[Any]]:
            return func

        return decorator

else:

    def asyncSlot(
        *args: object, **kwargs: object
    ) -> Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[Any]]]:
        return cast(
            Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[Any]]],
            _asyncSlot(*args, **kwargs),
        )


if TYPE_CHECKING:
    from ...platform.settings_manager import SettingsManager

    class EventBusProtocol(Protocol):
        def emit(self, event_name: str, payload: Any = ...) -> None: ...
        def on(self, event: str, handler: Any) -> Any: ...
        def subscribe(self, event: str, handler: Any) -> Any: ...


class BasePanel(QWidget):
    """
    Base class for all UI panels.

    Provides async operations, EventBus integration, and settings management.
    """

    # Panel lifecycle signals
    panel_activated = Signal()
    panel_deactivated = Signal()
    panel_shown = Signal()
    panel_hidden = Signal()

    # Data signals
    data_changed = Signal(dict)
    selection_changed = Signal(object)

    def __init__(self, panel_id: str, title: str, parent: QWidget | None = None):
        """
        Initialize base panel.

        Args:
            panel_id: Unique panel identifier
            title: Panel title
            parent: Parent widget
        """
        # Use super() for proper MRO initialization
        super().__init__(parent)

        self.panel_id = panel_id
        self.title = title

        # Core components
        self._event_bus: EventBusProtocol = get_extended_event_bus()
        self._settings: SettingsManager = get_settings_manager()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[Callable[[Any], Any]] = []
        self._event_tokens: list[Any] = []

        # Panel state
        self._is_active = False
        self._data: dict[str, Any] = {}

        # Setup
        self._setup_ui()
        self._connect_signals()
        self._load_settings()

        logger.info(f"Panel {panel_id} initialized")

    def _setup_ui(self) -> None:
        """Set up base UI structure."""
        # Create main layout
        self.main_layout: QVBoxLayout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(5, 5, 5, 5)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Subclasses will add their UI
        self.setup_panel_ui()

    @abstractmethod
    def setup_panel_ui(self) -> None:
        """
        Set up panel-specific UI.

        Must be implemented by subclasses.
        """
        pass

    def _connect_signals(self) -> None:
        """Connect to EventBus and internal signals."""
        # Subscribe to relevant events
        self.subscribe_events()

    def subscribe_events(self) -> None:
        """
        Subscribe to EventBus events.

        Override in subclasses to subscribe to specific events.
        """
        self._subscribe_language_events()

    def _subscribe_language_events(self) -> None:
        handler = self._handle_language_changed
        self._event_handlers.append(handler)
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus, "subscribe", None
        )
        if callable(subscribe):
            token = subscribe(Events.LANGUAGE_CHANGED, handler)
            self._event_tokens.append(token)

    def _handle_language_changed(self, payload: Any) -> None:
        language = getattr(payload, "language", None)
        self.refresh_translations()
        if isinstance(language, str):
            self.on_language_changed(language)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

    def on_language_changed(self, _language: str) -> None:
        """Hook for panel-specific language change handling."""
        pass

    def _ts(self, text: str) -> str:
        """Mark a string for translation while keeping the source literal."""
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)

    def _load_settings(self) -> None:
        """Load panel-specific settings."""
        # Load visibility
        visible = self._settings.get_bool(f"ui/panels/{self.panel_id}/visible", True)
        self.setVisible(visible)

        # Load panel-specific settings
        self.load_panel_settings()

    def load_panel_settings(self) -> None:
        """
        Load panel-specific settings.

        Override in subclasses for custom settings.
        """
        pass

    def _save_settings(self) -> None:
        """Save panel-specific settings."""
        # Save visibility
        self._settings.set(f"ui/panels/{self.panel_id}/visible", self.isVisible())

        # Save panel-specific settings
        self.save_panel_settings()

    def save_panel_settings(self) -> None:
        """
        Save panel-specific settings.

        Override in subclasses for custom settings.
        """
        pass

    def activate(self) -> None:
        """Activate panel."""
        if not self._is_active:
            self._is_active = True
            self.panel_activated.emit()
            self.on_activated()
            logger.debug(f"Panel {self.panel_id} activated")

    def deactivate(self) -> None:
        """Deactivate panel."""
        if self._is_active:
            self._is_active = False
            self.panel_deactivated.emit()
            self.on_deactivated()
            logger.debug(f"Panel {self.panel_id} deactivated")

    def on_activated(self) -> None:
        """
        Called when panel is activated.

        Override in subclasses for activation logic.
        """
        pass

    def on_deactivated(self) -> None:
        """
        Called when panel is deactivated.

        Override in subclasses for deactivation logic.
        """
        pass

    def showEvent(self, event: QShowEvent) -> None:
        """Handle show event."""
        super().showEvent(event)
        self.panel_shown.emit()
        self.on_panel_shown()

    def hideEvent(self, event: QHideEvent) -> None:
        """Handle hide event."""
        super().hideEvent(event)
        self.panel_hidden.emit()
        self.on_panel_hidden()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def on_panel_shown(self) -> None:
        """
        Called when panel is shown.

        Override in subclasses for show logic.
        """
        pass

    def on_panel_hidden(self) -> None:
        """
        Called when panel is hidden.

        Override in subclasses for hide logic.
        """
        pass

    @asyncSlot()
    async def refresh_async(self) -> None:
        """
        Async refresh of panel data.

        Override in subclasses for async refresh logic.
        """
        logger.debug(f"Refreshing panel {self.panel_id}")

    def set_data(self, data: dict[str, Any]) -> None:
        """
        Set panel data.

        Args:
            data: Data dictionary
        """
        old_data = self._data.copy()
        self._data = data

        if old_data != data:
            self.data_changed.emit(data)
            self.on_data_changed(data)

    def on_data_changed(self, data: dict[str, Any]) -> None:
        """
        Called when data changes.

        Args:
            data: New data

        Override in subclasses to handle data changes.
        """
        pass

    def get_data(self) -> dict[str, Any]:
        """
        Get panel data.

        Returns:
            Current data dictionary
        """
        return self._data.copy()

    def emit_event(self, event_name: str, payload: Any = None) -> None:
        """
        Emit event through EventBus.

        Args:
            event_name: Event name
            payload: Event payload
        """
        self._event_bus.emit(event_name, payload)

    @asyncSlot()
    async def async_initialize(self) -> None:
        """
        Async initialization.

        Override in subclasses for async setup.
        """
        # QWidget doesn't have async_initialize, so don't call super()
        logger.debug(f"Panel {self.panel_id} async initialized")

    @asyncSlot()
    async def async_cleanup(self) -> None:
        """Async cleanup."""
        self._save_settings()
        # QWidget doesn't have async_cleanup, so don't call super()
        logger.debug(f"Panel {self.panel_id} cleaned up")

    @property
    def is_active(self) -> bool:
        """Check if panel is active."""
        return self._is_active
