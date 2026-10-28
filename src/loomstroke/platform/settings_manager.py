"""
Settings manager using QSettings.

Per Master.md PR-D specifications, provides centralized configuration
with validation, type checking, and EventBus integration.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from PySide6.QtCore import QObject, Signal

from .settings_env import EnvironmentSettings
from .settings_log_redaction import redact_setting_value
from .settings_schema import (
    DEFAULT_COMFYUI_URL,
    DEFAULT_COMFYUI_WS,
    DEFAULT_SETTINGS,
    CanvasSettings,
    ConnectionSettings,
    MutableSettingsScope,
    SettingsScope,
    SettingsValue,
)
from .settings_validators import validate_setting
from .user_dirs import default_layout_directory, default_user_root

logger = logging.getLogger(__name__)

PROTECTED_SETTINGS = frozenset({"plugins/trusted_keys"})
_MENU_DEFAULT_PROFILE_KEY = "ui/menus/default_profile"
_MENU_DEFAULT_PROFILE_VERSION = "view_layer_filter_v1"
_DIRECT_VECTOR_KEY = "performance/direct_vector_rendering_enabled"
_DIRECT_VECTOR_DEFAULT_PROFILE_KEY = "performance/direct_vector_default_profile"
_DIRECT_VECTOR_DEFAULT_PROFILE_VERSION = "v8_enabled_v1"
_VIEW_COMPOSITING_KEY = "performance/view_resolution_compositing_enabled"
_VIEW_COMPOSITING_DEFAULT_PROFILE_KEY = "performance/view_compositing_default_profile"
_VIEW_COMPOSITING_DEFAULT_PROFILE_VERSION = "c3_enabled_v1"
_LEGACY_OPTIONAL_MENU_DEFAULTS: Mapping[str, bool] = MappingProxyType(
    {
        "ui/menus/view/enabled": False,
        "ui/menus/layer/enabled": False,
        "ui/menus/filter/enabled": False,
        "ui/menus/tools/enabled": False,
        "ui/menus/plugins/enabled": False,
    }
)
_END_USER_MENU_DEFAULTS: Mapping[str, bool] = MappingProxyType(
    {
        "ui/menus/view/enabled": True,
        "ui/menus/layer/enabled": True,
        "ui/menus/filter/enabled": True,
    }
)


class _FileSettingsBackend:
    """Simple JSON-backed settings store to avoid QSettings lock issues."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, SettingsValue] = {}
        self._dirty = False
        self._batch_depth = 0
        self._load()

    # Core IO -----------------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            loaded = json.loads(raw) if raw.strip() else {}
            if isinstance(loaded, dict):
                self._data = loaded
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load settings file %s: %s", self._path, exc)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        try:
            self._path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to persist settings file %s: %s", self._path, exc)

    # Batch API ----------------------------------------------------------------
    def begin_batch(self) -> None:
        """Suppress disk writes until the matching ``end_batch()``."""
        with self._lock:
            self._batch_depth += 1

    def end_batch(self) -> None:
        """End a batch; flush to disk if dirty and no outer batch remains."""
        with self._lock:
            self._batch_depth = max(0, self._batch_depth - 1)
            if self._batch_depth == 0 and self._dirty:
                self._save()
                self._dirty = False

    # QSettings-like API -----------------------------------------------------
    def value(self, key: str, default: Any = None) -> Any:
        return self._data.get(str(key), default)

    def setValue(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[str(key)] = value
            if self._batch_depth > 0:
                self._dirty = True
            else:
                self._save()

    def contains(self, key: str) -> bool:
        return str(key) in self._data

    def remove(self, key: str) -> None:
        with self._lock:
            self._data.pop(str(key), None)
            if self._batch_depth > 0:
                self._dirty = True
            else:
                self._save()

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            if self._batch_depth > 0:
                self._dirty = True
            else:
                self._save()

    def allKeys(self) -> list[str]:
        return list(self._data.keys())

    def sync(self) -> None:
        """Flush pending changes to disk."""
        with self._lock:
            if self._dirty:
                self._save()
                self._dirty = False

    @property
    def fileName(self) -> str:
        return str(self._path)


class SettingsManager(QObject):
    """
    Centralized settings management using QSettings.

    Replaces all hardcoded configuration values.
    """

    # Signals for settings changes
    settings_changed = Signal(str, object)  # key, value

    def __init__(self, organization: str = "LoomStroke", application: str = "LoomStroke"):
        """
        Initialize settings manager.

        Args:
            organization: Organization name for QSettings
            application: Application name for QSettings
        """
        super().__init__()
        settings_root = default_user_root(create=True) / "settings"
        settings_root.mkdir(parents=True, exist_ok=True)
        settings_path = settings_root / f"{application}.json"

        # Use a JSON file under repo-local user_data to keep distributions self-contained
        self.settings = _FileSettingsBackend(settings_path)

        # Initialize default values if not present
        self._initialize_defaults()

        # Log environment variables
        EnvironmentSettings.log_environment()

        logger.info("SettingsManager initialized: %s", self.settings.fileName)

    def _initialize_defaults(self) -> None:
        """Initialize default settings if not present."""
        self.settings.begin_batch()
        try:
            for key, value in DEFAULT_SETTINGS.items():
                if not self.settings.contains(key):
                    self.set(key, value)
            self._migrate_optional_menu_defaults()
            self._migrate_direct_vector_default()
            self._migrate_view_compositing_default()
            self._ensure_layout_directory_default()
        finally:
            self.settings.end_batch()

    def _migrate_optional_menu_defaults(self) -> None:
        """Promote the old all-hidden optional menu default to the end-user default."""

        if self.settings.value(_MENU_DEFAULT_PROFILE_KEY) == _MENU_DEFAULT_PROFILE_VERSION:
            return

        legacy_default_state = all(
            self.get_bool(key, default=expected) is expected
            for key, expected in _LEGACY_OPTIONAL_MENU_DEFAULTS.items()
        )
        if legacy_default_state:
            for key, value in _END_USER_MENU_DEFAULTS.items():
                self.set(key, value, emit_event=False)
            logger.info("Migrated optional menu defaults: View/Layer/Filter enabled")

        self.set(
            _MENU_DEFAULT_PROFILE_KEY,
            _MENU_DEFAULT_PROFILE_VERSION,
            emit_event=False,
        )

    def _migrate_direct_vector_default(self) -> None:
        """Promote direct vector rendering to on for stores frozen on the old default.

        ``_initialize_defaults`` persists ``DEFAULT_SETTINGS`` into the store
        on first run, so flipping a schema default later never reaches stores
        created earlier.  The V8 default flip (False -> True) hit exactly
        that: stores created during the opt-in phase keep ``false`` forever.
        The key has no preferences UI yet, so a stored ``false`` can only be
        the frozen old default — promote it once, guarded by a profile
        marker so a user who later disables it stays disabled.
        """

        if (
            self.settings.value(_DIRECT_VECTOR_DEFAULT_PROFILE_KEY)
            == _DIRECT_VECTOR_DEFAULT_PROFILE_VERSION
        ):
            return
        if not self.get_bool(_DIRECT_VECTOR_KEY, default=True):
            self.set(_DIRECT_VECTOR_KEY, True, emit_event=False)
            logger.info("Migrated direct vector rendering default to enabled (V8)")
        self.set(
            _DIRECT_VECTOR_DEFAULT_PROFILE_KEY,
            _DIRECT_VECTOR_DEFAULT_PROFILE_VERSION,
            emit_event=False,
        )

    def _migrate_view_compositing_default(self) -> None:
        """Promote view-resolution compositing to on for pre-C3 stores.

        The key shipped default-off during C1/C2 field testing, so existing
        stores carry a frozen ``false``.  Unlike the direct-vector key this
        one HAS a preferences UI, so a stored ``false`` may be a deliberate
        user choice — the one-shot promotion flips it once anyway (C3 makes
        sharp rendering the product default), and a user who turns it off
        after the migration stays off thanks to the profile marker.
        """

        if (
            self.settings.value(_VIEW_COMPOSITING_DEFAULT_PROFILE_KEY)
            == _VIEW_COMPOSITING_DEFAULT_PROFILE_VERSION
        ):
            return
        if not self.get_bool(_VIEW_COMPOSITING_KEY, default=True):
            self.set(_VIEW_COMPOSITING_KEY, True, emit_event=False)
            logger.info("Migrated view-resolution compositing default to enabled (C3)")
        self.set(
            _VIEW_COMPOSITING_DEFAULT_PROFILE_KEY,
            _VIEW_COMPOSITING_DEFAULT_PROFILE_VERSION,
            emit_event=False,
        )

    def _ensure_layout_directory_default(self) -> None:
        """Ensure the layout directory setting points to a writable path."""
        layout_key = "io/layout_directory"
        current = self.settings.value(layout_key)
        if isinstance(current, str) and current.strip():
            try:
                layout_path = Path(current).expanduser()
                layout_path.mkdir(parents=True, exist_ok=True)
                return
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Configured layout directory is unavailable; using default: %s (%s)",
                    current,
                    exc,
                )

        layout_path = default_layout_directory()
        try:
            layout_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - defensive
            logger.warning("Failed to create default layout directory %s: %s", layout_path, exc)
        self.set(layout_key, str(layout_path), emit_event=False)

    def get(
        self,
        key: str,
        default: SettingsValue | None = None,
    ) -> SettingsValue | None:
        """
        Get setting value with environment override support.

        Args:
            key: Setting key (use / for groups, e.g., "connection/base_url")
            default: Default value if key not found

        Returns:
            Setting value or default (environment overrides take precedence)
        """
        default_value = default if default is not None else DEFAULT_SETTINGS.get(key)
        env_key = EnvironmentSettings.get_env_key(key)
        if env_key in os.environ:
            env_value = EnvironmentSettings.get_value(key, default_value)
            return self._coerce_value(env_value, default, setting_path=key)

        if not self.settings.contains(key):
            return default

        raw_value = cast(SettingsValue | None, self.settings.value(key))
        if isinstance(default, dict) and isinstance(raw_value, str):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                logger.warning("Failed to decode JSON for setting %s", key)
                return default
            return cast(SettingsValue, parsed) if isinstance(parsed, dict) else default

        coerced = self._coerce_value(raw_value, default, setting_path=key)
        if isinstance(default, dict) and not isinstance(coerced, dict):
            return default
        return coerced

    def _coerce_value(  # noqa: PLR0911, PLR0912
        self,
        raw: SettingsValue | None,
        default: SettingsValue | None,
        *,
        setting_path: str = "",
    ) -> SettingsValue | None:
        if raw is None:
            return default

        if isinstance(default, bool):
            if isinstance(raw, str):
                lowered = raw.lower()
                if lowered in {"true", "1", "yes", "on"}:
                    return True
                if lowered in {"false", "0", "no", "off"}:
                    return False
            return bool(raw)

        if isinstance(default, int) and not isinstance(default, bool):
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return int(raw)
            if isinstance(raw, str):
                try:
                    return int(raw)
                except ValueError:
                    logger.warning(
                        "Failed to coerce %s to int", _redact_setting_value(setting_path, raw)
                    )
            return default

        if isinstance(default, float):
            if isinstance(raw, (int, float)):
                return float(raw)
            if isinstance(raw, str):
                try:
                    return float(raw)
                except ValueError:
                    logger.warning(
                        "Failed to coerce %s to float", _redact_setting_value(setting_path, raw)
                    )
            return default

        if isinstance(default, dict):
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    return default
                if isinstance(parsed, dict):
                    return cast(SettingsValue, parsed)
                return default
            return raw if isinstance(raw, dict) else default

        if isinstance(default, list):
            if isinstance(raw, str):
                try:
                    loaded = json.loads(raw)
                except json.JSONDecodeError:
                    return default
                if isinstance(loaded, list):
                    return cast(SettingsValue, loaded)
                return default
            return list(raw) if isinstance(raw, list) else default

        if isinstance(raw, str):
            normalized = raw.strip()
            lowered = normalized.lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            try:
                if normalized.replace("-", "", 1).isdigit():
                    return int(normalized)
                return float(normalized)
            except ValueError:
                return normalized

        return raw

    def set(
        self,
        key: str,
        value: SettingsValue,
        emit_event: bool = True,
        *,
        protected_write: bool = False,
    ) -> None:
        """
        Set setting value.

        Args:
            key: Setting key
            value: Value to set
            emit_event: Whether to emit change event via EventBus
            protected_write: Internal app/installer path for protected settings.
        """
        if key in PROTECTED_SETTINGS and not protected_write:
            raise PermissionError(f"{key} is a protected setting")

        old_value = self.settings.value(key)

        stored_value: SettingsValue | str = value

        # Convert complex types to JSON strings for storage
        if isinstance(value, (dict, list)):
            stored_value = json.dumps(value)

        self.settings.setValue(key, stored_value)
        self.settings.sync()
        if emit_event:
            self.settings_changed.emit(key, value)

        logger.debug("Setting updated: %s = %s", key, _redact_setting_value(key, value))

        # Emit via EventBus for non-Qt components (per PR-D)
        if emit_event and old_value != stored_value:
            try:
                event_bus_module = importlib.import_module("loomstroke.core.event_bus_extensions")
                events_module = importlib.import_module("loomstroke.core.events")
            except (ImportError, AttributeError):
                return

            event_bus = event_bus_module.get_extended_event_bus()
            SettingsChangeEvent = events_module.SettingsChangeEvent
            Events = events_module.Events
            event = SettingsChangeEvent(
                source="settings_manager",
                key=key,
                old_value=old_value,
                new_value=value,
            )
            event_bus.emit(Events.SETTINGS_CHANGED, event)

    def set_protected(
        self,
        key: str,
        value: SettingsValue,
        *,
        emit_event: bool = True,
    ) -> None:
        """Set a protected setting through an explicit trusted application path."""

        if key not in PROTECTED_SETTINGS:
            raise ValueError(f"{key} is not a protected setting")
        self.set(key, value, emit_event=emit_event, protected_write=True)

    def get_group(self, group: str) -> MutableSettingsScope:
        """
        Get all settings in a group.

        Args:
            group: Group name (e.g., "connection")

        Returns:
            Dictionary of settings in the group
        """
        result: MutableSettingsScope = {}
        normalized = group.strip().strip("/")
        prefix = f"{normalized}/" if normalized else ""
        prefix_len = len(prefix)

        for full_key in self.settings.allKeys():
            if prefix and not full_key.startswith(prefix):
                continue
            scoped_key = full_key[prefix_len:] if prefix else full_key
            raw_value = self.settings.value(full_key)
            if isinstance(raw_value, str):
                trimmed = raw_value.strip()
                if trimmed and trimmed[0] in "{[":
                    try:
                        parsed = json.loads(raw_value)
                    except json.JSONDecodeError:
                        parsed = raw_value
                    else:
                        result[scoped_key] = cast(SettingsValue, parsed)
                        continue
                if raw_value.lower() in {"true", "false"}:
                    result[scoped_key] = raw_value.lower() == "true"
                    continue
                try:
                    if "." not in raw_value:
                        result[scoped_key] = int(raw_value)
                    else:
                        result[scoped_key] = float(raw_value)
                except ValueError:
                    result[scoped_key] = raw_value
                continue

            result[scoped_key] = cast(SettingsValue, raw_value)

        return result

    def get_section(self, group: str, *, include_defaults: bool = True) -> SettingsScope:
        """Return an immutable snapshot of a settings group for UI consumption."""

        section_values: MutableSettingsScope = {}
        if include_defaults:
            section_values.update(self._default_section_values(group))
        section_values.update(self.get_group(group))
        return MappingProxyType(section_values)

    def set_group(self, group: str, values: Mapping[str, SettingsValue]) -> None:
        """
        Set multiple settings in a group.

        Args:
            group: Group name
            values: Dictionary of key-value pairs
        """
        normalized = group.strip().strip("/")
        prefix = f"{normalized}/" if normalized else ""
        self.settings.begin_batch()
        try:
            for key, value in values.items():
                scoped = f"{prefix}{key}" if prefix else key
                self.set(scoped, value, emit_event=True)
        finally:
            self.settings.end_batch()

    def set_section(
        self,
        group: str,
        values: Mapping[str, SettingsValue],
        *,
        emit_event: bool = True,
    ) -> None:
        """Set multiple keys within ``group`` using subsection-style keys."""

        normalized = group.strip().strip("/")
        prefix = f"{normalized}/" if normalized else ""
        self.settings.begin_batch()
        try:
            for key, value in values.items():
                scoped_key = f"{prefix}{key}" if prefix else key
                self.set(scoped_key, value, emit_event=emit_event)
        finally:
            self.settings.end_batch()

    def has(self, key: str) -> bool:
        """
        Check if a setting exists.

        Args:
            key: Setting key

        Returns:
            True if setting exists
        """
        return self.settings.contains(key)

    def remove(self, key: str) -> None:
        """
        Remove a setting.

        Args:
            key: Setting key to remove
        """
        if key in PROTECTED_SETTINGS:
            raise PermissionError(f"{key} is a protected setting")
        self.settings.remove(key)
        self.settings.sync()

    def get_int(self, key: str, default: int = 0) -> int:
        """
        Get an integer setting with type checking.

        Args:
            key: Setting key
            default: Default value if not found or invalid type

        Returns:
            Integer value
        """
        value = self.get(key, default)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                logger.warning(
                    "Invalid int value for %s: %s, using default: %s",
                    key,
                    _redact_setting_value(key, value),
                    _redact_setting_value(key, default),
                )
                return default
        logger.warning(
            "Invalid int value for %s: %s, using default: %s",
            key,
            _redact_setting_value(key, value),
            _redact_setting_value(key, default),
        )
        return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        """
        Get a boolean setting with type checking.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Boolean value
        """
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def get_float(self, key: str, default: float = 0.0) -> float:
        """
        Get a float setting with type checking.

        Args:
            key: Setting key
            default: Default value if not found or invalid type

        Returns:
            Float value
        """
        value = self.get(key, default)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                logger.warning(
                    "Invalid float value for %s: %s, using default: %s",
                    key,
                    _redact_setting_value(key, value),
                    _redact_setting_value(key, default),
                )
                return default
        logger.warning(
            "Invalid float value for %s: %s, using default: %s",
            key,
            _redact_setting_value(key, value),
            _redact_setting_value(key, default),
        )
        return default

    def get_str(self, key: str, default: str = "") -> str:
        """
        Get a string setting.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            String value
        """
        value = self.get(key, default)
        return str(value) if value is not None else default

    def validate(self, key: str, value: Any) -> tuple[bool, str | None]:
        """
        Validate a setting value based on key constraints.

        Args:
            key: Setting key
            value: Value to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        result = validate_setting(key, value)
        return result

    def set_validated(self, key: str, value: Any) -> bool:
        """
        Set a value with validation.

        Args:
            key: Setting key
            value: Value to set

        Returns:
            True if set successfully, False if validation failed
        """
        is_valid, error_msg = self.validate(key, value)
        if not is_valid:
            logger.error(
                "Validation failed for %s=%s: %s",
                key,
                _redact_setting_value(key, value),
                _redact_setting_value(key, error_msg),
            )
            return False

        self.set(key, value)
        return True

    def clear(self) -> None:
        """Clear all settings (use with caution)."""
        self.settings.clear()
        self.settings.sync()
        self._initialize_defaults()

    async def initialize(self) -> None:
        """
        Initialize the settings manager.

        Provides unified manager interface compatibility.
        This is a no-op for SettingsManager as initialization
        happens in __init__, but the application bootstrap expects
        all managers to have an async initialize() method.
        """
        # Settings are already initialized in __init__
        # This method exists for interface consistency with other managers
        logger.debug("SettingsManager.initialize() called (initialization already complete)")

    def get_connection_settings(self) -> ConnectionSettings:
        """
        Get connection settings for ComfyUI.

        Returns:
            Dictionary with connection parameters
        """
        return ConnectionSettings(
            base_url=self.get_str("connection/base_url", DEFAULT_COMFYUI_URL),
            ws_url=self.get_str("connection/ws_url", DEFAULT_COMFYUI_WS),
            verify_tls=self.get_bool("connection/verify_tls", True),
            timeout_s=self.get_int("connection/timeout_s", 15),
            max_retries=self.get_int("connection/max_retries", 3),
            backoff_factor=self.get_float("connection/backoff_factor", 2.0),
        )

    def get_canvas_config(self) -> CanvasSettings:
        """
        Get canvas configuration.

        Returns:
            Dictionary with canvas parameters
        """
        return CanvasSettings(
            width=self.get_int("canvas/width", 832),
            height=self.get_int("canvas/height", 1216),
            dpi=self.get_int("canvas/dpi", 96),
            tile_size=self.get_int("canvas/tile_size", 256),
            max_layers=self.get_int("canvas/max_layers", 100),
            use_memmap_threshold=self.get_int("canvas/use_memmap_threshold", 10_000_000),
        )

    def get_tool_settings(self, tool_id: str) -> SettingsScope:
        """
        Get settings for a specific tool.

        Args:
            tool_id: Tool identifier

        Returns:
            Dictionary with tool settings
        """
        return self.get_group(f"tools/{tool_id}")

    def set_tool_settings(
        self,
        tool_id: str,
        settings: Mapping[str, SettingsValue],
    ) -> None:
        """
        Save settings for a specific tool.

        Args:
            tool_id: Tool identifier
            settings: Tool settings dictionary
        """
        self.set_group(f"tools/{tool_id}", settings)

    def _default_section_values(self, group: str) -> MutableSettingsScope:
        """Return defaults for ``group`` derived from DEFAULT_SETTINGS."""

        normalized = group.strip().strip("/")
        if not normalized:
            return {}
        prefix = f"{normalized}/"
        defaults: MutableSettingsScope = {}
        for key, value in DEFAULT_SETTINGS.items():
            if key.startswith(prefix):
                section_key = key[len(prefix) :]
                defaults[section_key] = deepcopy(value)
        return defaults

    def export_settings(self, file_path: str) -> None:
        """
        Export all settings to a JSON file.

        Args:
            file_path: Path to export file
        """
        all_settings: MutableSettingsScope = {}

        for key in self.settings.allKeys():
            all_settings[key] = cast(SettingsValue, self.settings.value(key))

        with open(file_path, "w") as f:
            json.dump(all_settings, f, indent=2)

        logger.info(f"Settings exported to {file_path}")

    def import_settings(self, file_path: str) -> None:
        """
        Import settings from a JSON file.

        Args:
            file_path: Path to import file
        """
        with open(file_path) as f:
            loaded = json.load(f)

        all_settings = cast(Mapping[str, SettingsValue], loaded)

        self.settings.begin_batch()
        try:
            for key, value in all_settings.items():
                self.set(key, value)
        finally:
            self.settings.end_batch()

        logger.info(f"Settings imported from {file_path}")


# Global instance
_settings_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    """
    Get global SettingsManager instance.

    Returns:
        SettingsManager instance
    """
    global _settings_manager  # noqa: PLW0603
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager


def _redact_setting_value(key: str, value: Any) -> Any:
    return redact_setting_value(key, value)
