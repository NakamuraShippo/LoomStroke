"""
Plugin registry for managing loaded plugins.

The registry handles plugin discovery, loading, and lifecycle management.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import os
import shutil
import uuid
from collections.abc import Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from ...platform.launch_options import ai_features_enabled, is_ai_plugin_module
from ...platform.settings_manager import get_settings_manager
from ...platform.user_dirs import plugin_data_root
from .base import PluginLifecycle, PluginMetadata, PluginType
from .errors import PluginInitError, PluginLifecycleError
from .loader import PluginLoader, PluginLoadError
from .validator import PluginManifest, ValidationResult

logger = logging.getLogger(__name__)


class PluginRegistry:
    """
    Central registry for all plugins.

    Manages plugin loading, activation, and lifecycle.
    """

    def __init__(self) -> None:
        """Initialize the plugin registry."""
        self._plugins: dict[str, PluginLifecycle] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._by_type: dict[PluginType, list[str]] = {plugin_type: [] for plugin_type in PluginType}
        self._host_api: Any | None = None
        self._settings_manager = get_settings_manager()
        trusted_keys = self._normalize_trusted_keys(
            self._settings_manager.get("plugins/trusted_keys", [])
        )
        self._loader = PluginLoader(
            plugins_dir=plugin_data_root(),
            trusted_keys=trusted_keys,
        )

    def set_host_api(self, host_api: Any) -> None:
        """
        Set the host API for plugins.

        Args:
            host_api: The host API instance
        """
        self._host_api = host_api

    def register(self, plugin: PluginLifecycle, metadata: PluginMetadata) -> None:
        """
        Register a plugin instance.

        Args:
            plugin: Plugin instance
            metadata: Plugin metadata

        Raises:
            PluginInitError: If plugin ID already exists
        """
        if not ai_features_enabled() and PluginType.AI_SERVICE in metadata.types:
            raise PluginInitError("AI plugins are disabled by --no-ai")
        if metadata.id in self._plugins:
            raise PluginInitError(f"Plugin with ID '{metadata.id}' already registered")

        self._plugins[metadata.id] = plugin
        self._metadata[metadata.id] = metadata

        # Index by type
        for plugin_type in metadata.types:
            self._by_type[plugin_type].append(metadata.id)

    def load_from_module(
        self,
        module_path: str,
        factory_name: str = "create_plugin",
        *,
        granted_permissions: dict[str, bool | list[str]] | None = None,
    ) -> str:
        """
        Load a plugin from a Python module.

        Args:
            module_path: Path to the module or module name
            factory_name: Name of the factory function in the module

        Returns:
            Plugin ID

        Raises:
            PluginInitError: If loading fails
        """
        if not ai_features_enabled() and is_ai_plugin_module(module_path):
            raise PluginInitError("AI plugins are disabled by --no-ai")
        try:
            source_path = Path(module_path)
            allow_legacy_module = os.getenv("LOOMSTROKE_ALLOW_LEGACY_PLUGIN_MODULE", "0") == "1"
            plugin: PluginLifecycle | None = None
            if source_path.exists():
                if source_path.is_dir():
                    plugin, metadata = self._load_packaged_plugin(
                        source_path,
                        module_path,
                        granted_permissions=granted_permissions,
                    )
                elif source_path.is_file():
                    if source_path.suffix.lower() == ".zip":
                        plugin, metadata = self._load_packaged_plugin(
                            source_path,
                            module_path,
                            granted_permissions=granted_permissions,
                        )
                    elif source_path.suffix.lower() == ".py":
                        if not allow_legacy_module:
                            raise PluginInitError(
                                "Direct .py plugin loading is disabled for security hardening; "
                                "use packaged .zip plugin installation instead."
                            )
                        # Legacy direct module file loading (single-file plugin)
                        spec = importlib.util.spec_from_file_location("plugin", source_path)
                        if spec is None or spec.loader is None:
                            raise PluginInitError(f"Cannot load module from {module_path}")
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        plugin = None
                    else:
                        raise PluginInitError(f"Unsupported plugin file type: {source_path.suffix}")
                else:
                    raise PluginInitError(f"Unsupported plugin source path: {module_path}")
            else:
                if not module_path.startswith("loomstroke.plugins.") and not allow_legacy_module:
                    raise PluginInitError(
                        "Direct module-name plugin loading is restricted; use plugin packages only."
                    )
                # Load by module name
                module = importlib.import_module(module_path)

            if plugin is not None:
                self.register(plugin, metadata)
                if self._host_api:
                    self.activate(metadata.id)
                return metadata.id

            # Get the factory function
            if not hasattr(module, factory_name):
                raise PluginInitError(
                    f"Module {module_path} does not have factory function '{factory_name}'"
                )

            factory = getattr(module, factory_name)

            # Create the plugin
            plugin = factory()

            # Get metadata
            if hasattr(plugin, "metadata"):
                metadata = plugin.metadata
            elif hasattr(module, "PLUGIN_METADATA"):
                metadata = module.PLUGIN_METADATA
            else:
                raise PluginInitError(f"Plugin from {module_path} has no metadata")

            # Register the plugin
            self.register(plugin, metadata)

            # Activate if host API is available
            if self._host_api:
                self.activate(metadata.id)

            return metadata.id

        except Exception as e:
            raise PluginInitError(f"Failed to load plugin from {module_path}: {e}") from e

    def update_from_module(
        self,
        module_path: str,
        *,
        plugin_id: str | None = None,
        granted_permissions: Mapping[str, bool | list[str]] | None = None,
    ) -> str:
        """
        Update an installed plugin package with rollback support.

        Args:
            module_path: Path to the plugin package path
            plugin_id: Expected plugin ID (optional)
            granted_permissions: Optional permission overrides for updated plugin
        """
        manifest, validation_result, validation_msg = self.inspect_plugin_source(module_path)

        if validation_result is not ValidationResult.VALID:
            raise PluginInitError(f"Plugin update package validation failed: {validation_msg}")

        target_id = manifest.id
        if plugin_id is not None and plugin_id != target_id:
            raise PluginInitError(f"Plugin ID mismatch: selected {plugin_id}, package {target_id}")

        plugin_root = self._loader.plugins_dir / target_id
        if not plugin_root.exists():
            raise PluginInitError(
                f"Cannot update plugin '{target_id}': plugin not installed in {plugin_root}"
            )

        old_permissions = self._load_granted_permissions(target_id)
        effective_permissions = (
            self._normalize_permissions(granted_permissions)
            if granted_permissions is not None
            else old_permissions
        )

        old_plugin = self._plugins.get(target_id)
        old_was_enabled = bool(getattr(old_plugin, "enabled", False))
        old_was_registered = target_id in self._plugins
        backup_path: Path | None = None
        logger.info(
            "plugin registry update requested plugin_id=%s path=%s",
            target_id,
            module_path,
        )

        if old_was_registered:
            with suppress(Exception):
                self.deactivate(target_id)
            self._remove_registered_plugin(target_id, clear_permissions=False)

        with suppress(Exception):
            self._loader.unload_plugin(target_id)

        try:
            installed_path, installed_manifest, backup = self._loader.install_from_source(
                module_path,
                keep_backup=True,
            )
            backup_path = backup

            if installed_manifest.id != target_id:
                raise PluginInitError(
                    f"Plugin package ID mismatch during update: {installed_manifest.id}"
                )

            plugin_obj = self._loader.load_plugin(
                installed_path,
                installed_manifest,
                granted_permissions=effective_permissions,
            )
            if not hasattr(plugin_obj, "metadata"):
                raise PluginInitError(f"Plugin from {module_path} has no metadata")

            new_metadata = plugin_obj.metadata
            if not isinstance(new_metadata, PluginMetadata):
                raise PluginInitError(f"Plugin metadata from {module_path} is invalid")

            self.register(plugin_obj, new_metadata)
            if old_was_enabled and self._host_api:
                with suppress(Exception):
                    self.activate(target_id)

            self.set_granted_permissions(target_id, effective_permissions)
            self._cleanup_plugin_backups(target_id, keep_backup=backup_path)
            logger.info(
                "plugin registry update succeeded plugin_id=%s backup=%s",
                target_id,
                str(backup_path) if backup_path else None,
            )
            return target_id

        except Exception as exc:
            logger.warning(
                "plugin registry update failed plugin_id=%s backup=%s error=%s",
                target_id,
                backup_path,
                exc,
            )
            self._restore_plugin_from_backup(target_id, backup_path)
            if old_was_registered:
                try:
                    restored_plugin, restored_metadata = self._load_packaged_plugin(
                        plugin_root,
                        str(plugin_root),
                        granted_permissions=old_permissions,
                    )
                    self.register(restored_plugin, restored_metadata)
                    if old_was_enabled and self._host_api:
                        with suppress(Exception):
                            self.activate(target_id)
                except Exception as restore_exc:
                    logger.warning(
                        "plugin registry update rollback load failed plugin_id=%s error=%s",
                        target_id,
                        restore_exc,
                    )
            raise PluginInitError(f"Failed to update plugin '{target_id}': {exc}") from exc

    def rollback_plugin(self, plugin_id: str) -> None:
        """
        Roll back an installed plugin using the latest preserved backup.

        Args:
            plugin_id: Target plugin ID
        """
        backup_path = self._loader.latest_plugin_backup(plugin_id)
        if backup_path is None:
            raise PluginInitError(f"No rollback backup available for plugin '{plugin_id}'")

        plugin_path = self._loader.plugins_dir / plugin_id
        old_plugin = self._plugins.get(plugin_id)
        was_registered = plugin_id in self._plugins
        was_enabled = bool(getattr(old_plugin, "enabled", False)) if was_registered else False
        old_permissions = self._load_granted_permissions(plugin_id)
        staging_path = plugin_path.parent / f".{plugin_id}.rollback_staging.{uuid.uuid4().hex}"
        logger.info("plugin registry rollback requested plugin_id=%s", plugin_id)

        try:
            if was_registered:
                with suppress(Exception):
                    self.deactivate(plugin_id)
                self._remove_registered_plugin(plugin_id, clear_permissions=False)
            with suppress(Exception):
                self._loader.unload_plugin(plugin_id)

            if plugin_path.exists():
                if staging_path.exists():
                    shutil.rmtree(staging_path, ignore_errors=True)
                shutil.move(str(plugin_path), str(staging_path))

            with suppress(Exception):
                if plugin_path.exists():
                    shutil.rmtree(plugin_path, ignore_errors=True)
            shutil.copytree(backup_path, plugin_path)

            plugin_obj, metadata = self._load_packaged_plugin(
                plugin_path,
                str(plugin_path),
                granted_permissions=old_permissions,
            )
            self.register(plugin_obj, metadata)

            if was_enabled and self._host_api:
                with suppress(Exception):
                    self.activate(plugin_id)

            # Keep rollback only as the explicitly requested restoration target.
            self._cleanup_plugin_backups(plugin_id, keep_backup=None)
            logger.info("plugin registry rollback succeeded plugin_id=%s", plugin_id)

        except Exception as exc:
            logger.warning("plugin rollback failed, restoring pre-rollback state: %s", exc)
            with suppress(Exception):
                if plugin_path.exists():
                    shutil.rmtree(plugin_path, ignore_errors=True)
            with suppress(Exception):
                if staging_path.exists():
                    shutil.move(str(staging_path), str(plugin_path))

            if was_registered:
                with suppress(Exception):
                    restored_plugin, restored_metadata = self._load_packaged_plugin(
                        plugin_path,
                        str(plugin_path),
                        granted_permissions=old_permissions,
                    )
                    self.register(restored_plugin, restored_metadata)
                    if was_enabled and self._host_api:
                        with suppress(Exception):
                            self.activate(plugin_id)

            raise PluginInitError(f"Failed to rollback plugin '{plugin_id}': {exc}") from exc
        finally:
            with suppress(Exception):
                if staging_path.exists():
                    shutil.rmtree(staging_path, ignore_errors=True)

    def has_rollback_backup(self, plugin_id: str) -> bool:
        """Return whether a rollback backup is currently available."""
        return self._latest_plugin_backup(plugin_id) is not None

    def load_installed_plugins(self) -> list[str]:
        """
        Load installed plugin packages from `plugin_data` (user scope).

        Returns:
            Loaded plugin IDs.
        """
        loaded: list[str] = []
        discovered = self._loader.discover_plugins()
        for plugin_path, manifest in discovered:
            if not ai_features_enabled() and "ai" in manifest.types:
                continue
            plugin_id = manifest.id

            if plugin_id in self._plugins:
                logger.warning(
                    "Skipping installed plugin already registered: id=%s path=%s",
                    plugin_id,
                    plugin_path,
                )
                continue

            try:
                plugin, metadata = self._load_packaged_plugin(
                    plugin_path,
                    str(plugin_path),
                    granted_permissions=self._load_granted_permissions(manifest.id),
                )
            except PluginInitError as exc:
                logger.warning(
                    "Failed to load installed plugin %s from %s: %s",
                    plugin_id,
                    plugin_path,
                    exc,
                )
                continue

            if metadata.id in self._plugins:
                logger.warning(
                    "Skipping installed plugin registration; duplicate ID: id=%s path=%s",
                    metadata.id,
                    plugin_path,
                )
                continue

            try:
                self.register(plugin, metadata)
            except PluginInitError as exc:
                logger.warning(
                    "Failed to register installed plugin %s from %s: %s",
                    plugin_id,
                    plugin_path,
                    exc,
                )
                continue

            if self._host_api:
                try:
                    self.activate(metadata.id)
                except PluginLifecycleError as exc:
                    logger.warning(
                        "Failed to activate installed plugin %s: %s",
                        metadata.id,
                        exc,
                    )
                    continue

            loaded.append(metadata.id)

        return loaded

    def _load_packaged_plugin(
        self,
        source_path: Path,
        module_path: str,
        *,
        granted_permissions: (
            dict[str, bool | list[str]] | Mapping[str, bool | list[str]] | None
        ) = None,
    ) -> tuple[PluginLifecycle, PluginMetadata]:
        try:
            plugin_path, manifest = self._loader.load_from_source(source_path)
        except PluginLoadError as e:
            raise PluginInitError(f"Failed to load plugin source {module_path}: {e}") from e

        if not ai_features_enabled() and "ai" in manifest.types:
            raise PluginInitError("AI plugins are disabled by --no-ai")
        if granted_permissions is None:
            granted_permissions = self._load_granted_permissions(manifest.id)

        plugin_obj = self._loader.load_plugin(
            plugin_path,
            manifest,
            granted_permissions=granted_permissions,
        )
        if not hasattr(plugin_obj, "metadata"):
            raise PluginInitError(f"Plugin from {module_path} has no metadata")
        metadata = plugin_obj.metadata
        if not isinstance(metadata, PluginMetadata):
            raise PluginInitError(f"Plugin metadata from {module_path} is invalid")
        return plugin_obj, metadata

    def inspect_plugin_source(
        self,
        module_path: str,
    ) -> tuple[PluginManifest, ValidationResult, str | None]:
        """
        Inspect a plugin source path without installation.

        Returns plugin manifest and validation status.
        """
        try:
            return self._loader.inspect_source(module_path)
        except PluginLoadError as e:
            raise PluginInitError(f"Failed to inspect plugin source {module_path}: {e}") from e

    def get_granted_permissions(self, plugin_id: str) -> dict[str, bool | list[str]]:
        """
        Get granted permissions for a plugin.

        Args:
            plugin_id: Plugin ID

        Returns:
            Permission map
        """
        return self._load_granted_permissions(plugin_id)

    def set_granted_permissions(
        self,
        plugin_id: str,
        granted_permissions: dict[str, bool] | Mapping[str, bool | list[str]],
    ) -> None:
        """
        Persist granted permissions for a plugin.

        Args:
            plugin_id: Plugin ID
            granted_permissions: Permissions granted by user
        """
        normalized = self._normalize_permissions(granted_permissions)
        self._settings_manager.set(f"plugins/permissions/{plugin_id}", normalized, emit_event=False)

    def _load_granted_permissions(self, plugin_id: str) -> dict[str, bool | list[str]]:
        raw = self._settings_manager.get(f"plugins/permissions/{plugin_id}", {})
        if not isinstance(raw, dict):
            return {}
        normalized = self._normalize_permissions(raw)
        return normalized

    def _clear_granted_permissions(self, plugin_id: str) -> None:
        self._settings_manager.remove(f"plugins/permissions/{plugin_id}")

    @staticmethod
    def _normalize_permissions(
        granted_permissions: dict[str, bool] | Mapping[str, bool | list[str]] | None,
    ) -> dict[str, bool | list[str]]:
        if not granted_permissions:
            return {}
        normalized: dict[str, bool | list[str]] = {}
        for key in ("file_system", "network", "gpu"):
            value = granted_permissions.get(key, False)
            normalized[key] = bool(value)
        domains = granted_permissions.get("network_domains", [])
        if isinstance(domains, list):
            normalized["network_domains"] = [
                str(item) for item in domains if isinstance(item, str) and item.strip()
            ]
        return normalized

    @staticmethod
    def _normalize_trusted_keys(raw: Any) -> list[str]:
        """Normalize trusted key definitions from settings."""
        if raw is None:
            return []

        if isinstance(raw, str):
            entries = [raw]
        elif isinstance(raw, (list, tuple)):
            entries = list(raw)
        else:
            return []

        normalized: list[str] = []
        for entry in entries:
            if not isinstance(entry, str):
                continue
            value = entry.strip()
            if not value:
                continue
            normalized.append(value)

        return normalized

    def activate(self, plugin_id: str) -> None:
        """
        Activate a plugin.

        Args:
            plugin_id: Plugin ID to activate

        Raises:
            PluginLifecycleError: If activation fails
        """
        if plugin_id not in self._plugins:
            raise PluginLifecycleError(f"Plugin '{plugin_id}' not found")

        if not self._host_api:
            raise PluginLifecycleError("Host API not set")

        try:
            plugin = self._plugins[plugin_id]
            plugin.activate(self._host_api)
        except Exception as e:
            raise PluginLifecycleError(f"Failed to activate plugin '{plugin_id}': {e}") from e

    def deactivate(self, plugin_id: str) -> None:
        """
        Deactivate a plugin.

        Args:
            plugin_id: Plugin ID to deactivate

        Raises:
            PluginLifecycleError: If deactivation fails
        """
        if plugin_id not in self._plugins:
            raise PluginLifecycleError(f"Plugin '{plugin_id}' not found")

        try:
            plugin = self._plugins[plugin_id]
            plugin.deactivate()
        except Exception as e:
            raise PluginLifecycleError(f"Failed to deactivate plugin '{plugin_id}': {e}") from e

    def _clear_backup_if_any(self, backup_path: Path | None) -> None:
        if backup_path is None:
            return
        with suppress(Exception):
            if backup_path.exists():
                shutil.rmtree(backup_path, ignore_errors=True)

    def _cleanup_plugin_backups(
        self,
        plugin_id: str,
        *,
        keep_backup: Path | None,
    ) -> None:
        self._loader.clear_plugin_backups(plugin_id, keep=keep_backup)

    def _latest_plugin_backup(self, plugin_id: str) -> Path | None:
        return self._loader.latest_plugin_backup(plugin_id)

    def _restore_plugin_from_backup(self, plugin_id: str, backup_path: Path | None) -> None:
        if backup_path is None:
            return
        plugin_path = self._loader.plugins_dir / plugin_id
        with suppress(Exception):
            if plugin_path.exists():
                shutil.rmtree(plugin_path, ignore_errors=True)
            shutil.move(str(backup_path), str(plugin_path))

    def _remove_registered_plugin(self, plugin_id: str, *, clear_permissions: bool) -> None:
        if plugin_id not in self._plugins:
            return
        metadata = self._metadata[plugin_id]
        for plugin_type in metadata.types:
            with suppress(ValueError):
                self._by_type[plugin_type].remove(plugin_id)
        del self._plugins[plugin_id]
        del self._metadata[plugin_id]
        if clear_permissions:
            self._clear_granted_permissions(plugin_id)

    def unregister(self, plugin_id: str) -> None:
        """
        Unregister and deactivate a plugin.

        Args:
            plugin_id: Plugin ID to unregister
        """
        if plugin_id not in self._plugins:
            return

        # Deactivate first
        try:  # noqa: SIM105
            self.deactivate(plugin_id)
        except PluginLifecycleError:
            pass  # Continue with unregistration even if deactivation fails

        self._remove_registered_plugin(plugin_id, clear_permissions=True)

    def get(self, plugin_id: str) -> PluginLifecycle | None:
        """
        Get a plugin by ID.

        Args:
            plugin_id: Plugin ID

        Returns:
            Plugin instance or None
        """
        return self._plugins.get(plugin_id)

    def get_metadata(self, plugin_id: str) -> PluginMetadata | None:
        """
        Get plugin metadata by ID.

        Args:
            plugin_id: Plugin ID

        Returns:
            Plugin metadata or None
        """
        return self._metadata.get(plugin_id)

    def all(self) -> Iterable[PluginLifecycle]:
        """
        Get all registered plugins.

        Returns:
            Iterator of all plugins
        """
        return self._plugins.values()

    def all_ids(self) -> Iterable[str]:
        """
        Get all plugin IDs.

        Returns:
            Iterator of plugin IDs
        """
        return self._plugins.keys()

    def by_type(self, plugin_type: PluginType) -> Iterable[PluginLifecycle]:
        """
        Get plugins by type.

        Args:
            plugin_type: Type of plugins to get

        Returns:
            Iterator of plugins of the specified type
        """
        for plugin_id in self._by_type[plugin_type]:
            yield self._plugins[plugin_id]

    def clear(self) -> None:
        """Deactivate and unregister all plugins."""
        # Deactivate all plugins
        for plugin_id in list(self._plugins.keys()):
            self.unregister(plugin_id)

        # Clear registry
        self._plugins.clear()
        self._metadata.clear()
        for plugin_list in self._by_type.values():
            plugin_list.clear()

    async def initialize(self) -> None:
        """
        Initialize the plugin registry.

        Provides unified async initialization interface for consistency
        with other application managers. Currently a no-op as the registry
        is ready after __init__ and plugins are loaded on-demand.
        """
        # Registry is ready after __init__
        # This method exists for interface consistency with other managers
        # Plugin loading happens on-demand via load_from_module() or register()
        pass

    async def cleanup(self) -> None:
        """
        Clean up all registered plugins on app shutdown.

        Prefers each plugin's async ``cleanup()`` hook (new SDK path) and
        falls back to the sync ``deactivate()`` hook (legacy path) for
        plugins without it. Per-plugin exceptions are logged and do not
        stop cleanup of the remaining plugins — settings persistence for
        tools like basic_pen / basic_eraser depends on this hook running.

        After all plugins are cleaned up, the registry maps are cleared so
        a subsequent re-initialization starts from a clean state.
        """
        for plugin_id in list(self._plugins.keys()):
            plugin = self._plugins.get(plugin_id)
            if plugin is None:
                continue
            try:
                cleanup_hook = getattr(plugin, "cleanup", None)
                if callable(cleanup_hook):
                    result = cleanup_hook()
                    if inspect.isawaitable(result):
                        await result
                else:
                    deactivate_hook = getattr(plugin, "deactivate", None)
                    if callable(deactivate_hook):
                        deactivate_hook()
            except Exception as e:  # noqa: BLE001 - cleanup must be robust
                logger.error("Plugin %s cleanup failed: %s", plugin_id, e)

        self._plugins.clear()
        self._metadata.clear()
        for plugin_list in self._by_type.values():
            plugin_list.clear()


# Global registry instance
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """
    Get the global plugin registry.

    Returns:
        The global registry instance
    """
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
