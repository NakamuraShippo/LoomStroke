"""ComfyUI Panel Plugin implementation.

This module implements the ComfyUI panel as a UIPanelPlugin,
preserving the user-friendly interface from the legacy implementation
while complying with the current plugin architecture.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QWidget

from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.translations import translated_literal_domain

from .extended_service import ExtendedComfyUIService

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    return translated_literal_domain("ComfyUIPlugin", text, use_fallback=True)


class ComfyUIPlugin(UIPanelPlugin):
    """ComfyUI integration plugin with full UI panel.

    This plugin provides the complete ComfyUI interface including:
    - Connection management
    - Workflow loading/saving
    - Parameter editing
    - Job queue visualization
    - Layer integration
    """

    # Plugin metadata
    metadata = PluginMetadata(
        id="comfyui_ui_panel",
        name=_ts("ComfyUI Panel"),
        version="1.0.0",
        description=_ts("ComfyUI integration panel for AI image generation"),
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self, host_api: HostAPI | None = None):
        """Initialize the ComfyUI plugin.

        Args:
            host_api: Host API for canvas and layer operations (optional)
        """
        super().__init__(self.metadata)
        self.host_api = host_api
        self.event_bus = getattr(host_api, "events", None) if host_api else None

        # Initialize service
        self.service = ExtendedComfyUIService(event_bus=self.event_bus, host_api=host_api)

        # Main panel widget (created on demand)
        self._panel: QWidget | None = None

        # Track initialization state
        self._initialized = False

    def descriptor(self) -> PanelDescriptor:
        """Get panel descriptor.

        Returns:
            Panel descriptor with metadata
        """
        return PanelDescriptor(
            id="comfyui_panel",
            display_name=_ts("ComfyUI"),
            category="ai_tools",
            icon_svg=self._get_icon_svg(),
            default_position="right",
            default_size=(400, 600),
            resizable=True,
            closable=True,
            floating=False,
        )

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create the panel widget.

        Args:
            parent: Optional parent widget

        Returns:
            The ComfyUI panel widget
        """
        if self._panel is None:
            # Import here to avoid circular dependencies
            from .ui.main_panel import ComfyUIMainPanel  # noqa: PLC0415

            self._panel = ComfyUIMainPanel(
                service=self.service, host_api=self.host_api, parent=parent
            )

            # Attach history store and provider if available
            store = getattr(self, "_history_store", None)
            provider = getattr(self, "_generation_provider", None)
            if store and provider:
                provider.set_panel(self._panel)
                self._panel.set_history_store(store, provider)

            # Connect panel signals to plugin
            self._connect_panel_signals()

            logger.info("ComfyUI panel created")

        return self._panel

    def activate(self, host_api: HostAPI) -> None:
        """Activate the plugin with host API.

        Args:
            host_api: Host API instance
        """
        if not self.host_api:
            self.host_api = host_api
            self.event_bus = getattr(host_api, "events", None)

            # Reinitialize service with host API
            self.service = ExtendedComfyUIService(event_bus=self.event_bus, host_api=host_api)

        # Register generation provider with history store
        get_service = getattr(host_api, "get_service", None)
        history_service = get_service("generation_history") if callable(get_service) else None
        if history_service is not None:
            from .comfyui_generation_provider import (  # noqa: PLC0415
                ComfyUIGenerationProvider,
            )

            self._generation_provider = ComfyUIGenerationProvider(service=self.service)
            history_service.register_provider(self._generation_provider)
            self._history_store = history_service

            # If panel already exists, attach provider now
            if self._panel is not None:
                self._generation_provider.set_panel(self._panel)
                self._panel.set_history_store(history_service, self._generation_provider)

    async def initialize(self) -> None:
        """Initialize the plugin and service.

        Note: This now only prepares the service for connection.
        Actual connection happens when user clicks Connect button.
        """
        if not self._initialized:
            try:
                # Initialize the service (prepares connection object only)
                await self.service.initialize()

                # Don't auto-connect or load object info
                # These will happen when user clicks Connect button
                # await self.service.get_object_info()  # Removed - manual connection

                self._initialized = True
                logger.info("ComfyUI plugin initialized (ready for manual connection)")

            except Exception as e:
                logger.error(f"Failed to initialize ComfyUI plugin: {e}")
                raise

    async def shutdown(self) -> None:
        """Shutdown the plugin and cleanup resources."""
        try:
            # Cancel any active jobs
            for job_id in list(self.service._active_jobs.keys()):
                await self.cancel(job_id)

            # Close connection
            if self.service._connection:
                await self.service._connection.close()

            # Cleanup panel
            if self._panel:
                self._panel.deleteLater()
                self._panel = None

            self._initialized = False
            logger.info("ComfyUI plugin shutdown")

        except Exception as e:
            logger.error(f"Error during ComfyUI plugin shutdown: {e}")

    def on_panel_shown(self) -> None:
        """Called when the panel is shown."""
        if self._panel and hasattr(self._panel, "on_panel_shown"):
            self._panel.on_panel_shown()

    def on_panel_hidden(self) -> None:
        """Called when the panel is hidden."""
        if self._panel and hasattr(self._panel, "on_panel_hidden"):
            self._panel.on_panel_hidden()

    def on_settings_changed(self, key: str, value: any) -> None:
        """Called when plugin settings change.

        Args:
            key: Setting key that changed
            value: New setting value
        """
        if key.startswith("comfyui."):
            # Update service configuration
            if self.service and self.service._settings:
                self.service._settings.set(key, value)

            # Notify panel of settings change
            if self._panel and hasattr(self._panel, "on_settings_changed"):
                self._panel.on_settings_changed(key, value)

            # Reconnect if server settings changed
            if (
                key in ["comfyui.host", "comfyui.port", "comfyui.use_ssl"]
                and self._panel
                and hasattr(self._panel, "reconnect")
            ):
                self._panel.reconnect()

    # --- Private Methods ---

    def _connect_panel_signals(self) -> None:
        """Connect panel signals to plugin handlers."""
        if not self._panel:
            return

        # Connect status messages to event bus
        if hasattr(self._panel, "status_message"):
            self._panel.status_message.connect(self._on_status_message)

        # Connect layer import requests
        if hasattr(self._panel, "layer_import_requested"):
            self._panel.layer_import_requested.connect(self._on_layer_import)

    def _on_status_message(self, message: str) -> None:
        """Handle status messages from panel.

        Args:
            message: Status message to display
        """
        if self.event_bus:
            self.event_bus.emit("status.message", {"source": "comfyui", "message": message})

    def _on_layer_import(self, layer_name: str, image_path: str) -> None:
        """Handle layer import requests.

        Args:
            layer_name: Name for the new layer
            image_path: Path to image file to import
        """
        if self.event_bus:
            self.event_bus.emit(
                "comfyui.layer_import", {"layer_name": layer_name, "image_path": image_path}
            )

    def _get_icon_svg(self) -> str:
        """Get the ComfyUI icon SVG.

        Returns:
            SVG string for the panel icon
        """
        return """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <circle cx="12" cy="12" r="10" fill="#8B5CF6"/>
            <path d="M8 12 L11 15 L16 10" stroke="white" stroke-width="2"
                  fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        """


def create_plugin() -> ComfyUIPlugin:
    """Factory function to create the ComfyUI plugin.

    Returns:
        ComfyUIPlugin instance

    Note:
        Host API will be provided via activate() method
    """
    return ComfyUIPlugin()


# Export public API
__all__ = ["ComfyUIPlugin", "create_plugin"]
