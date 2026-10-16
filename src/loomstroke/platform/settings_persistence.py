"""Settings persistence layer using QSettings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

from .settings_categories import (
    AllSettings,
    ConnectionSettings,
    DrawingSettings,
    GenerationSettings,
    PerformanceSettings,
    ToolDefaults,
    UISettings,
)

logger = logging.getLogger(__name__)


class SettingsPersistence:
    """Manages persistent storage of application settings using QSettings.

    Provides save/load functionality for all setting categories with
    encryption support for sensitive data.
    """

    def __init__(self, organization: str = "LoomStroke", application: str = "LoomStroke"):
        """Initialize settings persistence.

        Args:
            organization: Organization name for QSettings
            application: Application name for QSettings
        """
        self.organization = organization
        self.application = application

        # Create QSettings instance
        self._settings = QSettings(organization, application)

        # Encryption handler (will be set externally)
        self._encryptor: Any | None = None

        logger.info(f"Settings persistence initialized: {self._settings.fileName()}")

    def set_encryptor(self, encryptor: Any) -> None:
        """Set encryption handler for sensitive data.

        Args:
            encryptor: Object with encrypt/decrypt methods
        """
        self._encryptor = encryptor

    # QSettings value cast helpers (mypy Bucket B fast-follow #1)
    #
    # QSettings.value() returns object (INI backend may return str for any type),
    # so direct int()/float() calls break mypy no-overload-variant checks.
    # These helpers narrow the type via isinstance and fall back to the default
    # on corrupt data, which is safer than the previous int()/float() which
    # raised ValueError on invalid stored values.

    def _load_str(self, key: str, default: str) -> str:
        value = self._settings.value(key, default)
        return value if isinstance(value, str) else default

    def _load_optional_str(self, key: str, default: str | None) -> str | None:
        value = self._settings.value(key, default)
        return value if isinstance(value, str) else default

    def _load_int(self, key: str, default: int) -> int:
        value = self._settings.value(key, default)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                logger.warning(f"Invalid int value for {key}: {value!r}, using default")
                return default
        return default

    def _load_float(self, key: str, default: float) -> float:
        value = self._settings.value(key, default)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                logger.warning(f"Invalid float value for {key}: {value!r}, using default")
                return default
        return default

    # Connection Settings

    def save_connection(self, settings: ConnectionSettings) -> None:
        """Save connection settings.

        Args:
            settings: Connection settings to save
        """
        self._settings.beginGroup("Connection")

        # Save non-sensitive settings
        self._settings.setValue("server_url", settings.server_url)
        self._settings.setValue("http_url", settings.http_url)
        self._settings.setValue("client_id", settings.client_id)

        # Timeouts
        self._settings.setValue("connection_timeout", settings.connection_timeout)
        self._settings.setValue("request_timeout", settings.request_timeout)
        self._settings.setValue("upload_timeout", settings.upload_timeout)

        # Retry settings
        self._settings.setValue("max_retries", settings.max_retries)
        self._settings.setValue("retry_delay", settings.retry_delay)
        self._settings.setValue("retry_backoff", settings.retry_backoff)
        self._settings.setValue("max_retry_delay", settings.max_retry_delay)

        # Security settings
        self._settings.setValue("verify_tls", settings.verify_tls)
        self._settings.setValue("tls_cert_path", settings.tls_cert_path)
        self._settings.setValue("use_auth", settings.use_auth)

        # Encrypt and save auth token if present
        if settings.auth_token and self._encryptor:
            encrypted = self._encryptor.encrypt(settings.auth_token)
            if encrypted is not None:
                self._settings.setValue("auth_token_encrypted", encrypted)
            else:
                logger.warning("Auth token encryption failed; token not saved")
        elif settings.auth_token:
            logger.warning("Auth token not encrypted (no encryptor set)")

        # Connection behavior
        self._settings.setValue("auto_reconnect", settings.auto_reconnect)
        self._settings.setValue("reconnect_interval", settings.reconnect_interval)
        self._settings.setValue("heartbeat_interval", settings.heartbeat_interval)

        self._settings.endGroup()
        self._settings.sync()

        logger.debug("Connection settings saved")

    def load_connection(self) -> ConnectionSettings:
        """Load connection settings.

        Returns:
            Loaded connection settings or defaults
        """
        settings = ConnectionSettings()

        self._settings.beginGroup("Connection")

        # Load settings with defaults
        settings.server_url = self._load_str("server_url", settings.server_url)
        settings.http_url = self._load_str("http_url", settings.http_url)
        settings.client_id = self._load_optional_str("client_id", settings.client_id)

        # Timeouts
        settings.connection_timeout = self._load_int(
            "connection_timeout", settings.connection_timeout
        )
        settings.request_timeout = self._load_int("request_timeout", settings.request_timeout)
        settings.upload_timeout = self._load_int("upload_timeout", settings.upload_timeout)

        # Retry settings
        settings.max_retries = self._load_int("max_retries", settings.max_retries)
        settings.retry_delay = self._load_float("retry_delay", settings.retry_delay)
        settings.retry_backoff = self._load_float("retry_backoff", settings.retry_backoff)
        settings.max_retry_delay = self._load_float("max_retry_delay", settings.max_retry_delay)

        # Security
        settings.verify_tls = self._settings.value("verify_tls", settings.verify_tls) == "true"
        settings.tls_cert_path = self._load_optional_str("tls_cert_path", settings.tls_cert_path)
        settings.use_auth = self._settings.value("use_auth", settings.use_auth) == "true"

        # Decrypt auth token if present
        encrypted_token = self._settings.value("auth_token_encrypted")
        if encrypted_token and self._encryptor:
            try:
                settings.auth_token = self._encryptor.decrypt(encrypted_token)
            except Exception as e:
                logger.error(f"Failed to decrypt auth token: {e}")

        # Connection behavior
        settings.auto_reconnect = (
            self._settings.value("auto_reconnect", settings.auto_reconnect) == "true"
        )
        settings.reconnect_interval = self._load_float(
            "reconnect_interval", settings.reconnect_interval
        )
        settings.heartbeat_interval = self._load_float(
            "heartbeat_interval", settings.heartbeat_interval
        )

        self._settings.endGroup()

        logger.debug("Connection settings loaded")
        return settings

    # Drawing Settings

    def save_drawing(self, settings: DrawingSettings) -> None:
        """Save drawing settings."""
        self._settings.beginGroup("Drawing")

        # Canvas dimensions
        self._settings.setValue("canvas_width", settings.canvas_width)
        self._settings.setValue("canvas_height", settings.canvas_height)
        self._settings.setValue("canvas_dpi", settings.canvas_dpi)

        # Appearance
        self._settings.setValue("background_color", settings.background_color)
        self._settings.setValue("checker_pattern", settings.checker_pattern)
        self._settings.setValue("checker_size", settings.checker_size)

        # Grid
        self._settings.setValue("grid_visible", settings.grid_visible)
        self._settings.setValue("grid_size", settings.grid_size)
        self._settings.setValue("grid_color", settings.grid_color)
        self._settings.setValue("grid_opacity", settings.grid_opacity)
        self._settings.setValue("grid_snap", settings.grid_snap)

        # Rendering
        self._settings.setValue("antialiasing", settings.antialiasing)
        self._settings.setValue("use_opengl", settings.use_opengl)
        self._settings.setValue("color_mode", settings.color_mode.value)

        # Performance
        self._settings.setValue("tile_size", settings.tile_size)
        self._settings.setValue("cache_size_mb", settings.cache_size_mb)
        self._settings.setValue("undo_levels", settings.undo_levels)

        # Viewport
        self._settings.setValue("default_zoom", settings.default_zoom)
        self._settings.setValue("min_zoom", settings.min_zoom)
        self._settings.setValue("max_zoom", settings.max_zoom)

        self._settings.endGroup()
        self._settings.sync()

    def load_drawing(self) -> DrawingSettings:
        """Load drawing settings."""
        settings = DrawingSettings()

        self._settings.beginGroup("Drawing")

        # Load with defaults
        settings.canvas_width = self._load_int("canvas_width", settings.canvas_width)
        settings.canvas_height = self._load_int("canvas_height", settings.canvas_height)
        settings.canvas_dpi = self._load_int("canvas_dpi", settings.canvas_dpi)

        settings.background_color = self._load_str("background_color", settings.background_color)
        settings.grid_visible = (
            self._settings.value("grid_visible", settings.grid_visible) == "true"
        )
        settings.grid_size = self._load_int("grid_size", settings.grid_size)

        settings.antialiasing = (
            self._settings.value("antialiasing", settings.antialiasing) == "true"
        )
        settings.use_opengl = self._settings.value("use_opengl", settings.use_opengl) == "true"

        self._settings.endGroup()

        return settings

    # Tool Settings

    def save_tools(self, settings: ToolDefaults) -> None:
        """Save tool default settings."""
        self._settings.beginGroup("Tools")

        # Pen settings
        self._settings.beginGroup("Pen")
        self._settings.setValue("size", settings.pen_size)
        self._settings.setValue("opacity", settings.pen_opacity)
        self._settings.setValue("hardness", settings.pen_hardness)
        self._settings.setValue("color", settings.pen_color)
        self._settings.setValue("blend_mode", settings.pen_blend_mode.value)
        self._settings.setValue("pressure_size", settings.pen_pressure_size)
        self._settings.setValue("pressure_opacity", settings.pen_pressure_opacity)
        self._settings.endGroup()

        # Eraser settings
        self._settings.beginGroup("Eraser")
        self._settings.setValue("size", settings.eraser_size)
        self._settings.setValue("opacity", settings.eraser_opacity)
        self._settings.setValue("hardness", settings.eraser_hardness)
        self._settings.setValue("mode", settings.eraser_mode)
        self._settings.endGroup()

        # Selection settings
        self._settings.beginGroup("Selection")
        self._settings.setValue("mode", settings.selection_mode)
        self._settings.setValue("feather", settings.selection_feather)
        self._settings.setValue("antialiasing", settings.selection_antialiasing)
        self._settings.endGroup()

        self._settings.endGroup()
        self._settings.sync()

    def load_tools(self) -> ToolDefaults:
        """Load tool default settings."""
        settings = ToolDefaults()

        self._settings.beginGroup("Tools")

        # Pen settings
        self._settings.beginGroup("Pen")
        settings.pen_size = self._load_float("size", settings.pen_size)
        settings.pen_opacity = self._load_float("opacity", settings.pen_opacity)
        settings.pen_color = self._load_str("color", settings.pen_color)
        self._settings.endGroup()

        # Eraser settings
        self._settings.beginGroup("Eraser")
        settings.eraser_size = self._load_float("size", settings.eraser_size)
        settings.eraser_opacity = self._load_float("opacity", settings.eraser_opacity)
        self._settings.endGroup()

        self._settings.endGroup()

        return settings

    # UI Settings

    def save_ui(self, settings: UISettings) -> None:
        """Save UI settings."""
        self._settings.beginGroup("UI")

        # Theme
        self._settings.setValue("theme", settings.theme)
        self._settings.setValue("accent_color", settings.accent_color)
        self._settings.setValue("font_family", settings.font_family)
        self._settings.setValue("font_size", settings.font_size)

        # Window behavior
        self._settings.setValue("save_window_geometry", settings.save_window_geometry)
        self._settings.setValue("save_window_state", settings.save_window_state)
        self._settings.setValue("restore_on_startup", settings.restore_on_startup)

        # Panels
        self._settings.setValue("show_tool_panel", settings.show_tool_panel)
        self._settings.setValue("show_comfyui_panel", settings.show_comfyui_panel)

        self._settings.endGroup()
        self._settings.sync()

    def load_ui(self) -> UISettings:
        """Load UI settings."""
        settings = UISettings()

        self._settings.beginGroup("UI")

        settings.theme = self._load_str("theme", settings.theme)
        settings.accent_color = self._load_str("accent_color", settings.accent_color)
        settings.restore_on_startup = (
            self._settings.value("restore_on_startup", settings.restore_on_startup) == "true"
        )

        self._settings.endGroup()

        return settings

    # Window State

    def save_window_state(self, geometry: bytes, state: bytes) -> None:
        """Save window geometry and state.

        Args:
            geometry: Window geometry as bytes
            state: Window state as bytes
        """
        self._settings.beginGroup("Window")
        self._settings.setValue("geometry", QByteArray(geometry))
        self._settings.setValue("state", QByteArray(state))
        self._settings.endGroup()
        self._settings.sync()

    def load_window_state(self) -> tuple[bytes | None, bytes | None]:
        """Load window geometry and state.

        Returns:
            Tuple of (geometry, state) as bytes or None
        """
        self._settings.beginGroup("Window")

        geometry = self._settings.value("geometry")
        state = self._settings.value("state")

        self._settings.endGroup()

        # Convert QByteArray to bytes
        geometry_bytes = bytes(geometry) if geometry else None
        state_bytes = bytes(state) if state else None

        return geometry_bytes, state_bytes

    # All Settings

    def save_all(self, settings: AllSettings) -> None:
        """Save all settings categories."""
        self.save_connection(settings.connection)
        self.save_drawing(settings.drawing)
        self.save_tools(settings.tools)
        # Save other categories as needed
        self.save_ui(settings.ui)

        logger.info("All settings saved")

    def load_all(self) -> AllSettings:
        """Load all settings categories."""
        return AllSettings(
            connection=self.load_connection(),
            drawing=self.load_drawing(),
            tools=self.load_tools(),
            generation=GenerationSettings(),  # Load when implemented
            ui=self.load_ui(),
            performance=PerformanceSettings(),  # Load when implemented
        )

    # Utility Methods

    def clear_all(self) -> None:
        """Clear all settings."""
        self._settings.clear()
        self._settings.sync()
        logger.warning("All settings cleared")

    def export_to_file(self, filepath: Path) -> None:
        """Export all settings to JSON file.

        Args:
            filepath: Path to export file
        """
        settings = self.load_all()
        data = settings.to_dict()

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Settings exported to {filepath}")

    def import_from_file(self, filepath: Path) -> None:
        """Import settings from JSON file.

        Args:
            filepath: Path to import file
        """
        with open(filepath) as f:
            data = json.load(f)

        settings = AllSettings.from_dict(data)
        self.save_all(settings)

        logger.info(f"Settings imported from {filepath}")
