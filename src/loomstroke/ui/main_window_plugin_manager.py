"""Plugin Manager workflow coordination for MainWindow."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime
from typing import Any, TypedDict, cast

from ..plugins.sdk import PluginRegistry
from ..plugins.sdk.base import PluginType
from ..plugins.sdk.errors import PluginInitError, PluginLifecycleError
from ..plugins.sdk.validator import ValidationResult

logger = logging.getLogger(__name__)
plugin_audit_logger = logging.getLogger("loomstroke.audit.plugins")


class PluginAuditRecord(TypedDict):
    """Structured payload for plugin manager audit logs."""

    timestamp: str
    action: str
    actor: str
    plugin_id: str | None
    source: str | None
    result: str
    reason: str | None
    details: dict[str, Any] | None


class MainWindowPluginManagerCoordinator:
    """Coordinate Plugin Manager dialog actions and audit logging."""

    def __init__(
        self,
        window: Any,
        *,
        plugin_manager_dialog: Callable[[], Any],
        compatibility_dialog: Callable[[], Any],
        message_box: Callable[[], Any],
        dialog_type: Callable[[], Any],
    ) -> None:
        self._window = window
        self._plugin_manager_dialog = plugin_manager_dialog
        self._compatibility_dialog = compatibility_dialog
        self._message_box = message_box
        self._dialog_type = dialog_type

    def open_plugin_manager(self) -> None:
        registry = getattr(self._window, "_plugin_registry", None)
        if registry is None:
            logger.warning("plugin manager open ignored: plugin registry not available")
            return
        dialog = self._plugin_manager_dialog()(
            registry,
            self._window._event_bus,
            parent=self._window,
        )
        dialog.exec()

    def on_plugin_manager_event(self, payload: Mapping[str, Any] | None) -> None:  # noqa: PLR0911
        """Handle Plugin Manager actions (open/import/update/enable/disable/remove/rollback)."""
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            logger.warning(
                "ui.plugins.manager payload must be mapping; got %s",
                type(payload).__name__,
            )
            payload = {}

        action = self.extract_plugin_manager_action(payload)
        plugin = payload.get("plugin")
        plugin_id = self.extract_plugin_manager_plugin_id(payload, plugin)

        registry = getattr(self._window, "_plugin_registry", None)
        if registry is None:
            logger.warning(
                "ui.plugins.manager event ignored: plugin registry unavailable action=%s",
                action,
            )
            return

        if action == "open":
            self.open_plugin_manager()
            return

        if action in {"import", "load", "install"}:
            self.handle_plugin_manager_import(payload, registry)
            return

        if action == "update":
            self.handle_plugin_manager_update(plugin_id, payload, registry)
            return
        if action == "rollback":
            self.handle_plugin_manager_rollback(plugin_id, registry)
            return

        if plugin is None and plugin_id is not None:
            plugin = registry.get(plugin_id)

        if action in {"enable", "activate"}:
            self.handle_plugin_manager_enable(plugin_id, plugin, registry)
            return

        if action in {"disable", "deactivate"}:
            self.handle_plugin_manager_disable(plugin_id, registry)
            return

        if action in {"remove", "unregister", "delete"}:
            self.handle_plugin_manager_remove(plugin_id, registry)
            return

        logger.warning("ui.plugins.manager received unknown action=%s", action)

    def append_plugin_audit_record(
        self,
        action: str,
        plugin_id: str | None,
        source: str | None,
        result: str,
        reason: str | None = None,
        *,
        actor: str = "ui.plugins.manager",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        safe_details = self.normalize_audit_details(details)
        record: PluginAuditRecord = {
            "timestamp": timestamp,
            "action": action,
            "actor": actor,
            "plugin_id": plugin_id,
            "source": source,
            "result": result,
            "reason": reason,
            "details": safe_details,
        }
        self._window._plugin_audit_log.appendleft(record)
        try:
            self._window._plugin_audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._window._plugin_audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # pragma: no cover - audit logging best-effort
            logger.debug("Failed to append plugin audit log", exc_info=True)

        if plugin_audit_logger.isEnabledFor(logging.INFO):
            plugin_audit_logger.info(
                (
                    "plugin_audit action=%s plugin_id=%s actor=%s result=%s source=%s "
                    "reason=%s details=%s"
                ),
                action,
                plugin_id,
                actor,
                result,
                source,
                reason,
                safe_details,
            )

    @staticmethod
    def normalize_audit_details(
        details: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not details:
            return None

        normalized: dict[str, Any] = {}
        for key, value in details.items():
            safe_key = str(key)
            try:
                json.dumps(value, ensure_ascii=False)
            except TypeError:
                normalized[safe_key] = str(value)
            else:
                normalized[safe_key] = value
        return normalized

    @staticmethod
    def extract_plugin_manager_action(payload: Mapping[str, Any]) -> str:
        action = payload.get("action", payload.get("op", payload.get("command", "")))
        if not isinstance(action, str):
            return ""
        return action.strip().lower()

    @staticmethod
    def extract_plugin_manager_plugin_id(
        payload: Mapping[str, Any],
        plugin: Any,
    ) -> str | None:
        plugin_id = payload.get("plugin_id")
        if isinstance(plugin_id, str):
            return plugin_id.strip() or None
        metadata = getattr(plugin, "metadata", None)
        candidate = getattr(metadata, "id", None) if metadata is not None else None
        return candidate if isinstance(candidate, str) else None

    def handle_plugin_manager_import(  # noqa: C901, PLR0912, PLR0915
        self,
        payload: Mapping[str, Any],
        registry: PluginRegistry,
    ) -> None:
        source = self.extract_plugin_manager_source(payload)
        pre_plugin = None
        pre_version = None
        if source is None:
            logger.warning("ui.plugins.manager import requires module path")
            self.append_plugin_audit_record(
                action="import",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing module path",
                details={"phase": "extract"},
            )
            return
        source_path = source.strip()

        try:
            manifest, validation_result, validation_msg = registry.inspect_plugin_source(
                source_path
            )
        except Exception as exc:
            pre_plugin_id = pre_plugin.id if pre_plugin is not None else None
            self.append_plugin_audit_record(
                action="import",
                plugin_id=pre_plugin_id,
                source=source_path,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "inspect",
                    "error": str(exc),
                    "attempted_path": source_path,
                },
            )
            self._warning("Plugin Import Failed", str(exc))
            logger.warning(
                "plugin manager import inspection failed path=%s error=%s",
                source_path,
                exc,
            )
            return

        warnings = [validation_msg] if validation_msg else []
        dialog = self._compatibility_dialog()(
            manifest=manifest,
            validation_result=validation_result,
            warnings=warnings,
            parent=self._window,
        )

        granted_permissions: dict[str, bool | list[str]] = {}
        install_approved = False

        def _on_permissions_granted(values: dict[str, bool | list[str]]) -> None:
            granted_permissions.clear()
            granted_permissions.update(values)

        def _on_install_approved(approved: bool) -> None:
            nonlocal install_approved
            install_approved = approved

        dialog.permissions_granted.connect(_on_permissions_granted)
        dialog.installation_approved.connect(_on_install_approved)

        if dialog.exec() != self._dialog_type().DialogCode.Accepted or not install_approved:
            self.append_plugin_audit_record(
                action="import",
                plugin_id=manifest.id,
                source=source_path,
                result="cancelled",
                reason="dialog cancelled",
                details={
                    "phase": "consent",
                },
            )
            return

        if validation_result != ValidationResult.VALID:
            self.append_plugin_audit_record(
                action="import",
                plugin_id=manifest.id,
                source=source_path,
                result="failed",
                reason=validation_msg or "validation_failed",
                details={
                    "phase": "validate",
                    "validation_result": (
                        validation_result.value
                        if hasattr(validation_result, "value")
                        else str(validation_result)
                    ),
                    "validation_msg": validation_msg,
                },
            )
            self._warning(
                "Plugin Import Rejected",
                str(
                    validation_msg
                    or "The selected plugin does not satisfy validation requirements."
                ),
            )
            return

        try:
            pre_plugin = registry.get(manifest.id)
            pre_version = (
                getattr(getattr(pre_plugin, "metadata", None), "version", None)
                if pre_plugin is not None
                else None
            )
            plugin_id = registry.load_from_module(
                source_path,
                granted_permissions=granted_permissions,
            )
        except Exception as exc:
            self.append_plugin_audit_record(
                action="import",
                plugin_id=manifest.id,
                source=source_path,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "install",
                    "error": str(exc),
                    "previous_version": pre_version,
                },
            )
            logger.warning(
                "plugin manager import failed path=%s error=%s",
                source_path,
                exc,
            )
            if "Permission check failed" in str(exc):
                self._warning(
                    "Plugin Import Rejected",
                    self._translate(
                        "Permission check failed. Please grant all required permissions."
                    ),
                )
            return
        self.append_plugin_audit_record(
            action="import",
            plugin_id=plugin_id,
            source=source_path,
            result="success",
            reason="plugin imported",
            details={
                "phase": "completed",
                "imported_version": manifest.version,
                "previous_version": pre_version,
                "previous_plugin_existed": pre_version is not None,
            },
        )
        registry.set_granted_permissions(plugin_id, granted_permissions)
        plugin = registry.get(plugin_id)
        if plugin is None:
            return
        types = getattr(getattr(plugin, "metadata", None), "types", None)
        if PluginType.UI_PANEL in (types or []):
            with suppress(Exception):
                self._window._sync_ui_panel_plugin(plugin)

    def handle_plugin_manager_update(  # noqa: C901, PLR0911, PLR0912, PLR0915
        self,
        plugin_id: str | None,
        payload: Mapping[str, Any],
        registry: PluginRegistry,
    ) -> None:
        if plugin_id is None:
            logger.warning("ui.plugins.manager update requires plugin_id")
            self.append_plugin_audit_record(
                action="update",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing plugin_id",
            )
            return

        source = self.extract_plugin_manager_source(payload)
        if source is None:
            logger.warning("ui.plugins.manager update requires module path")
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason="missing module path",
                details={"phase": "extract"},
            )
            return
        source_path = source.strip()
        previous_metadata = registry.get_metadata(plugin_id)
        previous_version = previous_metadata.version if previous_metadata is not None else None
        has_backup_before = False
        checker = getattr(registry, "has_rollback_backup", None)
        if callable(checker):
            with suppress(Exception):
                has_backup_before = bool(checker(plugin_id))
        logger.info(
            "plugin manager update requested plugin_id=%s path=%s",
            plugin_id,
            source_path,
        )

        try:
            manifest, validation_result, validation_msg = registry.inspect_plugin_source(
                source_path
            )
        except Exception as exc:
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=source_path,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "inspect",
                    "error": str(exc),
                },
            )
            self._warning("Plugin Update Failed", str(exc))
            logger.warning(
                "plugin manager update inspection failed path=%s error=%s",
                source_path,
                exc,
            )
            return

        if manifest.id != plugin_id:
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=source_path,
                result="failed",
                reason=(f"plugin_id_mismatch expected={plugin_id} actual={manifest.id}"),
                details={
                    "phase": "validate",
                    "expected": plugin_id,
                    "actual": manifest.id,
                    "source_version": manifest.version,
                },
            )
            self._warning(
                "Plugin Update Rejected",
                self._translate(
                    "The selected plugin package ID does not match the target plugin. "
                    f"Expected {plugin_id}, got {manifest.id}"
                ),
            )
            logger.warning(
                "plugin manager update id mismatch expected=%s actual=%s path=%s",
                plugin_id,
                manifest.id,
                source_path,
            )
            return

        if validation_result != ValidationResult.VALID:
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=source_path,
                result="failed",
                reason=validation_msg or "validation_failed",
                details={
                    "phase": "validate",
                    "validation_result": (
                        validation_result.value
                        if hasattr(validation_result, "value")
                        else str(validation_result)
                    ),
                    "validation_msg": validation_msg,
                },
            )
            self._warning(
                "Plugin Update Rejected",
                str(
                    validation_msg
                    or (
                        "The selected plugin update package does not satisfy "
                        "validation requirements."
                    )
                ),
            )
            return

        granted_permissions = registry.get_granted_permissions(plugin_id)
        install_approved = False

        def _on_permissions_granted(values: dict[str, bool | list[str]]) -> None:
            granted_permissions.clear()
            granted_permissions.update(values)

        def _on_install_approved(approved: bool) -> None:
            nonlocal install_approved
            install_approved = approved

        dialog = self._compatibility_dialog()(
            manifest=manifest,
            validation_result=validation_result,
            warnings=[validation_msg] if validation_msg else [],
            parent=self._window,
        )
        dialog.permissions_granted.connect(_on_permissions_granted)
        dialog.installation_approved.connect(_on_install_approved)

        if dialog.exec() != self._dialog_type().DialogCode.Accepted or not install_approved:
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=source_path,
                result="cancelled",
                reason="dialog cancelled",
                details={"phase": "consent"},
            )
            return

        try:
            registry.update_from_module(
                source_path,
                plugin_id=plugin_id,
                granted_permissions=granted_permissions,
            )
        except Exception as exc:
            self.append_plugin_audit_record(
                action="update",
                plugin_id=plugin_id,
                source=source_path,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "update",
                    "error": str(exc),
                    "previous_version": previous_version,
                    "source_version": manifest.version,
                    "has_backup_before": has_backup_before,
                },
            )
            logger.warning(
                "plugin manager update failed plugin_id=%s path=%s error=%s",
                plugin_id,
                source_path,
                exc,
            )
            if "Permission check failed" in str(exc):
                self._warning(
                    "Plugin Update Rejected",
                    self._translate(
                        "Permission check failed. Please grant all required permissions."
                    ),
                )
                return
            self._warning("Plugin Update Failed", str(exc))
            return

        self.append_plugin_audit_record(
            action="update",
            plugin_id=plugin_id,
            source=source_path,
            result="success",
            reason="plugin updated",
            details={
                "phase": "completed",
                "previous_version": previous_version,
                "source_version": manifest.version,
                "has_backup_before": has_backup_before,
            },
        )
        logger.info(
            "plugin manager update succeeded plugin_id=%s path=%s",
            plugin_id,
            source_path,
        )

        registry.set_granted_permissions(plugin_id, granted_permissions)
        plugin = registry.get(plugin_id)
        if plugin is None:
            return
        self._window._unsync_ui_panel_plugin(plugin_id)
        types = getattr(getattr(plugin, "metadata", None), "types", None)
        if PluginType.UI_PANEL in (types or []):
            with suppress(Exception):
                if getattr(plugin, "enabled", False):
                    self._window._sync_ui_panel_plugin(plugin)

    @staticmethod
    def extract_plugin_manager_source(payload: Mapping[str, Any]) -> str | None:
        source = payload.get("path")
        if source is None:
            source = payload.get("module_path")
        if source is None:
            source = payload.get("plugin_path")
        if not isinstance(source, str) or not source.strip():
            return None
        return source.strip()

    def handle_plugin_manager_enable(
        self,
        plugin_id: str | None,
        plugin: Any,
        registry: PluginRegistry,
    ) -> None:
        if plugin_id is None:
            logger.warning("ui.plugins.manager enable requires plugin_id")
            self.append_plugin_audit_record(
                action="enable",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing plugin_id",
                details={"phase": "request"},
            )
            return
        metadata = (
            getattr(getattr(plugin, "metadata", None), "version", None)
            if plugin is not None
            else None
        )
        try:
            registry.activate(plugin_id)
        except PluginLifecycleError as exc:
            self.append_plugin_audit_record(
                action="enable",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "activate",
                    "error": str(exc),
                    "previous_version": metadata,
                },
            )
            logger.warning(
                "plugin manager enable failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return
        if plugin is None:
            self.append_plugin_audit_record(
                action="enable",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason="plugin not found",
                details={
                    "phase": "activate",
                    "previous_version": metadata,
                },
            )
            return
        panel_synced = False
        types = getattr(getattr(plugin, "metadata", None), "types", None)
        if PluginType.UI_PANEL in (types or []):
            try:
                panel_synced = self._window._sync_ui_panel_plugin(plugin)
            except Exception:
                logger.debug("unknown: suppressed exception", exc_info=True)
                panel_synced = False
        self.append_plugin_audit_record(
            action="enable",
            plugin_id=plugin_id,
            source=None,
            result="success",
            reason=("panel_synced" if panel_synced else "not_a_ui_panel"),
            details={
                "phase": "completed",
                "previous_version": metadata,
                "result_enabled": True,
            },
        )

    def handle_plugin_manager_disable(  # noqa: PLR0912
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        if plugin_id is None:
            logger.warning("ui.plugins.manager disable requires plugin_id")
            self.append_plugin_audit_record(
                action="disable",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing plugin_id",
                details={"phase": "request"},
            )
            return
        plugin = registry.get(plugin_id)
        previous_version = (
            getattr(getattr(plugin, "metadata", None), "version", None)
            if plugin is not None
            else None
        )
        unsync_error: str | None = None
        unsync_count = 0
        try:
            unsync_count = self._window._unsync_ui_panel_plugin(plugin_id)
        except Exception as exc:
            unsync_error = str(exc)
            logger.warning(
                "plugin manager disable unsync failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
        try:
            registry.deactivate(plugin_id)
        except PluginLifecycleError as exc:
            self.append_plugin_audit_record(
                action="disable",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=f"deactivate_failed: {exc}",
                details={
                    "phase": "deactivate",
                    "error": str(exc),
                    "previous_version": previous_version,
                },
            )
            logger.warning(
                "plugin manager disable failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return
        except Exception as exc:
            self.append_plugin_audit_record(
                action="disable",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=f"deactivate_unexpected: {exc}",
                details={
                    "phase": "deactivate",
                    "error": str(exc),
                    "previous_version": previous_version,
                },
            )
            logger.warning(
                "plugin manager disable unexpected error plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return

        self.append_plugin_audit_record(
            action="disable",
            plugin_id=plugin_id,
            source=None,
            result="success" if unsync_error is None else "partial",
            reason=(
                f"unsync_panels={unsync_count}"
                if unsync_error is None
                else f"unsync_failed: {unsync_error}"
            ),
            details={
                "phase": "completed",
                "previous_version": previous_version,
                "unsync_count": unsync_count,
                "unsync_error": unsync_error,
            },
        )

    def handle_plugin_manager_remove(
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        if plugin_id is None:
            logger.warning("ui.plugins.manager remove requires plugin_id")
            self.append_plugin_audit_record(
                action="remove",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing plugin_id",
                details={"phase": "request"},
            )
            return
        plugin = registry.get(plugin_id)
        previous_version = (
            getattr(getattr(plugin, "metadata", None), "version", None)
            if plugin is not None
            else None
        )
        unsync_error: str | None = None
        unsync_count = 0
        try:
            unsync_count = self._window._unsync_ui_panel_plugin(plugin_id)
        except Exception as exc:
            unsync_error = str(exc)
            logger.warning(
                "plugin manager remove unsync failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
        try:
            registry.unregister(plugin_id)
        except Exception as exc:
            self.append_plugin_audit_record(
                action="remove",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=f"unregister_failed: {exc}",
                details={
                    "phase": "unregister",
                    "error": str(exc),
                    "previous_version": previous_version,
                },
            )
            logger.warning(
                "plugin manager remove failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return

        self.append_plugin_audit_record(
            action="remove",
            plugin_id=plugin_id,
            source=None,
            result="success" if unsync_error is None else "partial",
            reason=(
                f"unsync_panels={unsync_count}"
                if unsync_error is None
                else f"unsync_failed: {unsync_error}"
            ),
            details={
                "phase": "completed",
                "previous_version": previous_version,
                "unsync_count": unsync_count,
                "unsync_error": unsync_error,
            },
        )

    def handle_plugin_manager_rollback(  # noqa: C901, PLR0912, PLR0915
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        if plugin_id is None:
            logger.warning("ui.plugins.manager rollback requires plugin_id")
            self.append_plugin_audit_record(
                action="rollback",
                plugin_id=None,
                source=None,
                result="failed",
                reason="missing plugin_id",
                details={"phase": "request"},
            )
            return

        plugin = registry.get(plugin_id)
        previous_version = (
            getattr(getattr(plugin, "metadata", None), "version", None)
            if plugin is not None
            else None
        )
        has_backup = False
        has_backup_before = False
        checker = getattr(registry, "has_rollback_backup", None)
        if callable(checker):
            with suppress(Exception):
                has_backup_before = bool(checker(plugin_id))
                has_backup = has_backup_before
        if not has_backup:
            self.append_plugin_audit_record(
                action="rollback",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason="no rollback backup",
                details={
                    "phase": "precheck",
                    "previous_version": previous_version,
                    "has_backup_before": has_backup_before,
                },
            )
            logger.warning(
                "ui.plugins.manager rollback blocked: no backup plugin_id=%s",
                plugin_id,
            )
            return

        unsync_error = None
        unsync_count = 0
        try:
            unsync_count = self._window._unsync_ui_panel_plugin(plugin_id)
        except Exception as exc:
            unsync_error = str(exc)
            logger.warning(
                "plugin manager rollback unsync failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )

        try:
            registry.rollback_plugin(plugin_id)
        except PluginInitError as exc:
            self.append_plugin_audit_record(
                action="rollback",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "rollback",
                    "error": str(exc),
                    "previous_version": previous_version,
                    "has_backup_before": has_backup_before,
                    "unsync_count": unsync_count,
                    "unsync_error": unsync_error,
                },
            )
            logger.warning(
                "plugin manager rollback failed plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return
        except Exception as exc:
            self.append_plugin_audit_record(
                action="rollback",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason=str(exc),
                details={
                    "phase": "rollback",
                    "error": str(exc),
                    "previous_version": previous_version,
                    "has_backup_before": has_backup_before,
                    "unsync_count": unsync_count,
                    "unsync_error": unsync_error,
                },
            )
            logger.warning(
                "plugin manager rollback unexpected error plugin_id=%s error=%s",
                plugin_id,
                exc,
            )
            return

        plugin = registry.get(plugin_id)
        after_version = (
            getattr(getattr(plugin, "metadata", None), "version", None)
            if plugin is not None
            else None
        )
        if plugin is None:
            self.append_plugin_audit_record(
                action="rollback",
                plugin_id=plugin_id,
                source=None,
                result="failed",
                reason="plugin_not_found_after_rollback",
                details={
                    "phase": "verify",
                    "previous_version": previous_version,
                    "after_version": None,
                    "has_backup_before": has_backup_before,
                    "unsync_count": unsync_count,
                    "unsync_error": unsync_error,
                },
            )
            return

        types = getattr(getattr(plugin, "metadata", None), "types", None)
        if PluginType.UI_PANEL in (types or []):
            with suppress(Exception):
                if getattr(plugin, "enabled", False):
                    self._window._sync_ui_panel_plugin(plugin)

        self.append_plugin_audit_record(
            action="rollback",
            plugin_id=plugin_id,
            source=None,
            result="success" if unsync_error is None else "partial",
            reason=(
                f"unsync_panels={unsync_count}"
                if unsync_error is None
                else f"unsync_failed: {unsync_error}"
            ),
            details={
                "phase": "completed",
                "previous_version": previous_version,
                "after_version": after_version,
                "has_backup_before": has_backup_before,
                "unsync_count": unsync_count,
                "unsync_error": unsync_error,
            },
        )

    def _translate(self, text: str) -> str:
        return cast(str, self._window._ts(text))

    def _warning(self, title: str, message: str) -> None:
        self._message_box().warning(
            self._window,
            self._translate(title),
            message,
        )
