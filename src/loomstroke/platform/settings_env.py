"""
Environment variable support for settings.

Per Master.md A2 specifications, allows overriding settings
via environment variables.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import cast

from .settings_schema import (
    DEFAULT_COMFYUI_HOST,
    DEFAULT_COMFYUI_PORT,
    DEFAULT_COMFYUI_URL,
    DEFAULT_COMFYUI_WS,
    DEFAULT_SETTINGS,
    SettingsValue,
)

LEGACY_COMFYUI_ENV = ("COMFYUI_URL", "COMFYUI_HOST", "COMFYUI_PORT")
KNOWN_SETTING_PATHS: tuple[str, ...] = tuple(sorted(DEFAULT_SETTINGS.keys()))

logger = logging.getLogger(__name__)


class EnvironmentSettings:
    """
    Handles environment variable overrides for settings.

    Environment variables follow the pattern:
    LOOMSTROKE_<CATEGORY>_<KEY>

    Examples:
    - LOOMSTROKE_CONNECTION_BASE_URL
    - LOOMSTROKE_CONNECTION_TIMEOUT_S
    - LOOMSTROKE_UI_THEME
    """

    PREFIX = "LOOMSTROKE"

    @classmethod
    def get_env_key(cls, setting_path: str) -> str:
        """
        Convert setting path to environment variable name.

        Args:
            setting_path: Setting path like "connection/base_url"

        Returns:
            Environment variable name like "LOOMSTROKE_CONNECTION_BASE_URL"
        """
        # Replace / with _ and convert to uppercase
        parts = setting_path.upper().replace("/", "_")
        return f"{cls.PREFIX}_{parts}"

    @classmethod
    def get_value(
        cls,
        setting_path: str,
        default: SettingsValue | None = None,
    ) -> SettingsValue | None:
        """
        Get value from environment variable.

        Args:
            setting_path: Setting path
            default: Default value if not found

        Returns:
            Environment value or default
        """
        template = cls._default_value(setting_path, default)

        env_key = cls.get_env_key(setting_path)
        value = os.environ.get(env_key)
        if value is None:
            return default

        logger.debug("Using environment override: %s=%s", env_key, value)

        converted = cls._convert_value(value, template, setting_path=setting_path)
        if converted is None:
            return template if template is not None else default
        return converted

    @classmethod
    def get_comfyui_url(cls) -> str:
        """
        Get ComfyUI URL from environment or default.

        Checks in order:
        1. LOOMSTROKE_CONNECTION_BASE_URL
        2. COMFYUI_URL (backward compatibility)
        3. Construct from COMFYUI_HOST and COMFYUI_PORT
        4. Default value

        Returns:
            ComfyUI base URL
        """
        # Check new environment variable
        url_value = cls.get_value("connection/base_url", DEFAULT_COMFYUI_URL)
        if isinstance(url_value, str) and url_value:
            return url_value

        # Check legacy environment variables
        url = os.environ.get("COMFYUI_URL")
        if url:
            logger.debug(f"Using legacy COMFYUI_URL: {url}")
            return url

        # Construct from host and port
        host = os.environ.get("COMFYUI_HOST", DEFAULT_COMFYUI_HOST)
        port_str = os.environ.get("COMFYUI_PORT", str(DEFAULT_COMFYUI_PORT))

        try:
            port = int(port_str)
        except ValueError:
            logger.warning(f"Invalid COMFYUI_PORT: {port_str}, using default")
            port = DEFAULT_COMFYUI_PORT

        if host != DEFAULT_COMFYUI_HOST or port != DEFAULT_COMFYUI_PORT:
            url = f"http://{host}:{port}"
            logger.debug(f"Constructed URL from env: {url}")
            return url

        # Return default
        return str(DEFAULT_COMFYUI_URL)

    @classmethod
    def get_comfyui_ws_url(cls) -> str:
        """
        Get ComfyUI WebSocket URL from environment or default.

        Returns:
            ComfyUI WebSocket URL
        """
        # Check new environment variable
        ws_value = cls.get_value("connection/ws_url", DEFAULT_COMFYUI_WS)
        if isinstance(ws_value, str) and ws_value:
            return ws_value

        # Derive from HTTP URL
        http_url = cls.get_comfyui_url()
        if http_url != DEFAULT_COMFYUI_URL:
            # Convert http to ws
            ws_url = http_url.replace("http://", "ws://").replace("https://", "wss://")
            logger.debug(f"Derived WebSocket URL: {ws_url}")
            return ws_url

        # Return default
        return str(DEFAULT_COMFYUI_WS)

    @classmethod
    def apply_overrides(
        cls,
        settings: Mapping[str, SettingsValue],
    ) -> dict[str, SettingsValue]:
        """
        Apply environment variable overrides to settings.

        Args:
            settings: Current settings dictionary

        Returns:
            Settings with environment overrides applied
        """
        result = dict(settings)

        # Check for environment overrides for known keys in a stable order
        for setting_path, env_key in ENV_KEY_BY_SETTING.items():
            default_value = settings.get(setting_path, cls._default_value(setting_path, None))
            env_value = cls.get_value(setting_path, default_value)
            if env_value is None:
                continue
            if default_value is None or env_value != default_value:
                result[setting_path] = env_value
                logger.info("Environment override applied: %s=%s", env_key, env_value)

        # Special handling for ComfyUI URLs
        result["connection/base_url"] = cls.get_comfyui_url()
        result["connection/ws_url"] = cls.get_comfyui_ws_url()

        return result

    @classmethod
    def log_environment(cls) -> None:
        """Log all LoomStroke-related environment variables."""
        env_vars: dict[str, str] = {}
        environment: Mapping[str, str] = os.environ

        for env_key in ENV_KEY_BY_SETTING.values():
            value = environment.get(env_key)
            if value is not None:
                env_vars[env_key] = value

        for legacy_key in LEGACY_COMFYUI_ENV:
            value = environment.get(legacy_key)
            if value is not None:
                env_vars[legacy_key] = value

        if env_vars:
            logger.info("Environment variables detected:")
            for key, value in sorted(env_vars.items()):
                # Mask sensitive values
                if "KEY" in key or "SECRET" in key or "PASSWORD" in key:
                    masked_value = "*" * min(len(value), 8)
                else:
                    masked_value = value
                logger.info("  %s=%s", key, masked_value)
        else:
            logger.debug("No LoomStroke environment variables found")

        cls._log_registry_drift()

    @staticmethod
    def _default_value(
        setting_path: str,
        fallback: SettingsValue | None,
    ) -> SettingsValue | None:
        if fallback is not None:
            return fallback
        return DEFAULT_SETTINGS.get(setting_path)

    @classmethod
    def _convert_value(
        cls,
        raw_value: str,
        template: SettingsValue | None,
        *,
        setting_path: str | None,
    ) -> SettingsValue | None:
        lowered = raw_value.strip().lower()

        if isinstance(template, bool):
            return lowered in {"true", "1", "yes", "on"}
        if isinstance(template, int) and not isinstance(template, bool):
            return cls._convert_int_value(raw_value, template, setting_path)
        if isinstance(template, float):
            return cls._convert_float_value(raw_value, template, setting_path)
        if isinstance(template, (dict, list)):
            return cls._convert_json_value(raw_value, template, setting_path)
        if isinstance(template, str):
            return raw_value

        return cls._best_effort_value(raw_value, lowered)

    @staticmethod
    def _convert_int_value(
        raw_value: str,
        template: int,
        setting_path: str | None,
    ) -> int:
        try:
            return int(raw_value)
        except ValueError:
            logger.warning(
                "Invalid int override for %s: %s",
                setting_path or "unknown",
                raw_value,
            )
            return template

    @staticmethod
    def _convert_float_value(
        raw_value: str,
        template: float,
        setting_path: str | None,
    ) -> float:
        try:
            return float(raw_value)
        except ValueError:
            logger.warning(
                "Invalid float override for %s: %s",
                setting_path or "unknown",
                raw_value,
            )
            return template

    @staticmethod
    def _convert_json_value(
        raw_value: str,
        template: SettingsValue,
        setting_path: str | None,
    ) -> SettingsValue:
        try:
            loaded = json.loads(raw_value)
        except json.JSONDecodeError:
            logger.warning(
                "Invalid JSON override for %s: %s",
                setting_path or type(template).__name__,
                raw_value,
            )
            return template
        return cast(SettingsValue, loaded)

    @staticmethod
    def _best_effort_value(raw_value: str, lowered: str) -> SettingsValue:
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        try:
            numeric = float(raw_value) if "." in raw_value else int(raw_value)
            return numeric
        except ValueError:
            return raw_value

    @classmethod
    def _log_registry_drift(cls) -> None:
        known = set(KNOWN_SETTING_PATHS)
        mapped = set(ENV_KEY_BY_SETTING.keys())
        if known == mapped:
            return
        missing = sorted(known - mapped)
        extra = sorted(mapped - known)
        if missing:
            logger.warning("Missing ENV key mapping for settings: %s", ", ".join(missing))
        if extra:
            logger.warning("Unexpected ENV key mapping entries: %s", ", ".join(extra))


ENV_KEY_BY_SETTING: Mapping[str, str] = {
    path: EnvironmentSettings.get_env_key(path) for path in KNOWN_SETTING_PATHS
}


# Convenience function
def get_url_from_env() -> tuple[str, str]:
    """
    Get ComfyUI URLs from environment.

    Returns:
        Tuple of (http_url, ws_url)
    """
    return (EnvironmentSettings.get_comfyui_url(), EnvironmentSettings.get_comfyui_ws_url())
