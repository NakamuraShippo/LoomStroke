"""Plugin Manager dialog.

This dialog provides a minimal UI for Plugin Manager operations used by
TSK-007: Import / Enable / Disable / Remove.
TSK-010: Update / rollback is initiated here, with verification handled in
main_window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ...plugins.sdk import PluginRegistry
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...ui.translations import (
    TranslationBinder,
    translate_runtime_text,
    translated_literal_domain,
)


def _ts(text: str) -> str:
    """Translate Plugin Manager literals while preserving their source text."""

    return translated_literal_domain("PluginManagerDialog", text, use_fallback=True)


class EventBusProtocol(Protocol):
    def emit(self, event_name: str, payload: Any = ...) -> None: ...


@dataclass(frozen=True)
class _PluginInfo:
    plugin_id: str
    display_name: str
    types: str
    enabled: bool
    version: str
    source: str
    error: str


class PluginManagerDialog(QDialog):
    """Minimal manager for plugin install and lifecycle actions."""

    def __init__(
        self,
        registry: PluginRegistry,
        event_bus: EventBusProtocol,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self._translation_binder = TranslationBinder(self.tr)
        self._status_poll_interval_ms = 25
        self._status_poll_retries = 12
        self._is_open = True
        self._registry = registry
        self._event_bus = event_bus
        self._plugin_table = QTableWidget(self)
        self._import_btn = QPushButton(self._ts("Import..."), self)
        self._enable_btn = QPushButton(self._ts("Enable"), self)
        self._disable_btn = QPushButton(self._ts("Disable"), self)
        self._update_btn = QPushButton(self._ts("Update..."), self)
        self._remove_btn = QPushButton(self._ts("Remove"), self)
        self._rollback_btn = QPushButton(self._ts("Rollback..."), self)
        self._refresh_btn = QPushButton(self._ts("Refresh"), self)
        self._status_label = QLabel("", self)
        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._translation_binder.register(self.setWindowTitle, self._ts("Plugin Manager"))
        self._setup_ui()
        self._refresh_plugins()
        self.refresh_translations()

    def _ts(self, text: str) -> str:
        """Mark a literal for translation while keeping the source form."""
        return _ts(text)

    def refresh_translations(self) -> None:
        """Retranslate static UI elements after a language change."""

        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self._set_headers()
        self._refresh_plugins()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._import_btn)
        toolbar.addWidget(self._enable_btn)
        toolbar.addWidget(self._disable_btn)
        toolbar.addWidget(self._update_btn)
        toolbar.addWidget(self._remove_btn)
        toolbar.addWidget(self._rollback_btn)
        toolbar.addWidget(self._refresh_btn)
        toolbar.addStretch(1)

        self._plugin_table.setColumnCount(7)
        self._plugin_table.setHorizontalHeaderLabels(
            [
                self._ts("Plugin ID"),
                self._ts("Display Name"),
                self._ts("Types"),
                self._ts("Enabled"),
                self._ts("Version"),
                self._ts("Source"),
                self._ts("Error"),
            ]
        )
        self._plugin_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._plugin_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection,
        )
        self._plugin_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )
        self._plugin_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self._plugin_table.horizontalHeader().setStretchLastSection(True)
        self._plugin_table.itemSelectionChanged.connect(self._update_button_states)

        layout.addLayout(toolbar)
        layout.addWidget(self._plugin_table)
        layout.addWidget(self._status_label)
        layout.addWidget(self._button_box)
        self._button_box.rejected.connect(self.reject)

        self._import_btn.clicked.connect(self._on_import)
        self._enable_btn.clicked.connect(self._on_enable)
        self._disable_btn.clicked.connect(self._on_disable)
        self._update_btn.clicked.connect(self._on_update)
        self._remove_btn.clicked.connect(self._on_remove)
        self._rollback_btn.clicked.connect(self._on_rollback)
        self._refresh_btn.clicked.connect(self._refresh_plugins)
        self._update_button_states()

        close_btn = self._button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setDefault(True)
            self._translation_binder.register(close_btn.setText, self._ts("Close"))

        self._translation_binder.register(self._import_btn.setText, self._ts("Import..."))
        self._translation_binder.register(self._enable_btn.setText, self._ts("Enable"))
        self._translation_binder.register(self._disable_btn.setText, self._ts("Disable"))
        self._translation_binder.register(self._update_btn.setText, self._ts("Update..."))
        self._translation_binder.register(self._remove_btn.setText, self._ts("Remove"))
        self._translation_binder.register(self._rollback_btn.setText, self._ts("Rollback..."))
        self._translation_binder.register(self._refresh_btn.setText, self._ts("Refresh"))

    def _set_headers(self) -> None:
        self._plugin_table.setHorizontalHeaderLabels(
            [
                self._ts("Plugin ID"),
                self._ts("Display Name"),
                self._ts("Types"),
                self._ts("Enabled"),
                self._ts("Version"),
                self._ts("Source"),
                self._ts("Error"),
            ]
        )

    def _status_text(self, enabled: bool) -> str:
        return self._ts("Enabled") if enabled else self._ts("Disabled")

    def _is_active(self) -> bool:
        return bool(self._is_open and self._plugin_table is not None)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._is_open = False
        super().closeEvent(event)

    def _refresh_plugins(self) -> None:
        rows = self._collect_plugin_rows()
        self._plugin_table.setRowCount(len(rows))
        for row, info in enumerate(rows):
            self._plugin_table.setItem(row, 0, QTableWidgetItem(info.plugin_id))
            self._plugin_table.setItem(row, 1, QTableWidgetItem(info.display_name))
            self._plugin_table.setItem(row, 2, QTableWidgetItem(info.types))
            self._plugin_table.setItem(
                row,
                3,
                QTableWidgetItem(self._status_text(info.enabled)),
            )
            self._plugin_table.setItem(row, 4, QTableWidgetItem(info.version))
            self._plugin_table.setItem(row, 5, QTableWidgetItem(info.source))
            self._plugin_table.setItem(row, 6, QTableWidgetItem(info.error))
        self._set_status(self._format("{count} plugin(s) loaded", count=len(rows)))
        self._update_button_states()

    def _collect_plugin_rows(self) -> list[_PluginInfo]:
        rows: list[_PluginInfo] = []
        for plugin_id in sorted(self._registry.all_ids()):
            metadata = self._registry.get_metadata(plugin_id)
            plugin = self._registry.get(plugin_id)
            if metadata is None:
                metadata_obj = getattr(plugin, "metadata", None)
                if isinstance(metadata_obj, PluginMetadata):
                    metadata = metadata_obj
                else:
                    continue

            source = self._ts("Built-in")
            plugin_module = getattr(plugin, "__class__", object).__module__ if plugin else ""
            if not plugin_module.startswith("loomstroke.plugins."):
                source = self._ts("External")
            rows.append(
                _PluginInfo(
                    plugin_id=plugin_id,
                    display_name=translate_runtime_text(metadata.name),
                    types=self._format_types(getattr(metadata, "types", [])),
                    enabled=bool(getattr(plugin, "enabled", True)),
                    version=metadata.version,
                    source=source,
                    error="",
                )
            )
        return rows

    @staticmethod
    def _format_types(types: list[PluginType] | Any) -> str:
        if isinstance(types, (list, tuple, set)):
            normalized: list[str] = []
            for plugin_type in types:
                if isinstance(plugin_type, PluginType):
                    normalized.append(plugin_type.value)
                elif isinstance(plugin_type, str):
                    normalized.append(plugin_type)
                else:
                    normalized.append(str(plugin_type))
            return ",".join(normalized) or "-"
        if isinstance(types, str):
            return types
        return "-"

    def _selected_plugin_id(self) -> str | None:
        row = self._plugin_table.currentRow()
        if row < 0:
            return None
        item = self._plugin_table.item(row, 0)
        return item.text() if item is not None else None

    def _set_status(self, message: str, is_error: bool = False) -> None:
        if not self._is_active():
            return
        self._status_label.setText(message)
        color = "#d10000" if is_error else "#3a3a3a"
        self._status_label.setStyleSheet(f"color: {color};")

    def _format(self, template: str, /, **kwargs: object) -> str:
        values = {key: str(value) for key, value in kwargs.items()}
        return str(self._ts(template)).format(**values)

    def _action_label(self, action: str) -> str:
        mapping = {
            "enable": "Enable",
            "disable": "Disable",
            "update": "Update",
            "remove": "Remove",
            "rollback": "Rollback",
            "import": "Import",
        }
        label = mapping.get(action, action.capitalize())
        return str(self._ts(label))

    def _safe_refresh_plugins(self) -> bool:
        if not self._is_active():
            return False
        try:
            self._refresh_plugins()
        except RuntimeError:
            return False
        return True

    def _plugin_snapshot(
        self,
        plugin_id: str | None = None,
    ) -> tuple[set[str], bool | None, str | None, bool]:
        ids = set(self._registry.all_ids())
        if plugin_id is None:
            return ids, None, None, False
        plugin = self._registry.get(plugin_id)
        version = self._plugin_version(plugin_id)
        has_backup = self._has_rollback_backup(plugin_id)
        if plugin is None:
            return ids, None, version, has_backup
        return ids, bool(getattr(plugin, "enabled", True)), version, has_backup

    def _plugin_version(self, plugin_id: str | None) -> str | None:
        if plugin_id is None:
            return None
        metadata = self._registry.get_metadata(plugin_id)
        if metadata is not None:
            return metadata.version
        plugin = self._registry.get(plugin_id)
        if plugin is None:
            return None
        plugin_metadata = getattr(plugin, "metadata", None)
        if plugin_metadata is None:
            return None
        return getattr(plugin_metadata, "version", None)

    def _has_rollback_backup(self, plugin_id: str | None) -> bool:
        if plugin_id is None:
            return False
        checker = getattr(self._registry, "has_rollback_backup", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(plugin_id))
        except Exception:
            return False

    def _status_for_import(
        self,
        action: str,
        before_ids: set[str],
        after_ids: set[str],
    ) -> tuple[str | None, bool, bool]:
        if action not in {"import", "load", "install"}:
            return None, False, False
        if len(after_ids) > len(before_ids):
            return self._ts("Import completed."), False, False
        return self._ts("Import requested."), False, False

    def _status_for_enable(
        self,
        action: str,
        plugin_id: str | None,
        before_enabled: bool | None,
    ) -> tuple[str | None, bool, bool]:
        if action not in {"enable", "activate"} or plugin_id is None:
            return None, False, False

        plugin = self._registry.get(plugin_id)
        if plugin is None:
            return (
                self._format(
                    "Enable failed: plugin {plugin_id} not found.",
                    plugin_id=plugin_id,
                ),
                True,
                False,
            )

        enabled = bool(getattr(plugin, "enabled", True))
        if enabled:
            if before_enabled is False:
                return self._format("Enabled: {plugin_id}", plugin_id=plugin_id), False, False
            return (
                self._format(
                    "Plugin already enabled: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                False,
            )
        if before_enabled is False:
            return (
                self._format(
                    "Enable may be pending: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                True,
            )
        return self._format("Enable failed: {plugin_id}", plugin_id=plugin_id), True, False

    def _status_for_disable(
        self,
        action: str,
        plugin_id: str | None,
        before_enabled: bool | None,
    ) -> tuple[str | None, bool, bool]:
        if action not in {"disable", "deactivate"} or plugin_id is None:
            return None, False, False

        plugin = self._registry.get(plugin_id)
        if plugin is None:
            return (
                self._format(
                    "Disable failed: plugin {plugin_id} not found.",
                    plugin_id=plugin_id,
                ),
                True,
                False,
            )

        enabled = bool(getattr(plugin, "enabled", False))
        if not enabled:
            if before_enabled is True:
                return self._format("Disabled: {plugin_id}", plugin_id=plugin_id), False, False
            return (
                self._format(
                    "Plugin already disabled: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                False,
            )
        return (
            self._format(
                "Disable may be pending: {plugin_id}",
                plugin_id=plugin_id,
            ),
            False,
            True,
        )

    def _status_for_remove(
        self,
        action: str,
        plugin_id: str | None,
        before_ids: set[str],
        after_ids: set[str],
        attempt: int = 0,
        max_attempts: int = 12,
    ) -> tuple[str | None, bool, bool]:
        if action not in {"remove", "unregister", "delete"} or plugin_id is None:
            return None, False, False

        was_present = plugin_id in before_ids
        is_present = plugin_id in after_ids
        if was_present and not is_present:
            return self._format("Removed: {plugin_id}", plugin_id=plugin_id), False, False
        if is_present:
            if attempt + 1 >= max_attempts:
                return (
                    self._format(
                        "Remove failed: {plugin_id} still exists.",
                        plugin_id=plugin_id,
                    ),
                    True,
                    False,
                )
            return (
                self._format(
                    "Remove may be pending: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                True,
            )
        return self._format("Plugin {plugin_id} not found.", plugin_id=plugin_id), True, False

    def _status_for_update(
        self,
        action: str,
        plugin_id: str | None,
        before_version: str | None,
        after_version: str | None,
        after_ids: set[str],
        attempt: int = 0,
        max_attempts: int = 12,
    ) -> tuple[str | None, bool, bool]:
        if action not in {"update"} or plugin_id is None:
            return None, False, False
        if plugin_id in after_ids:
            if after_version != before_version:
                return (
                    self._format(
                        "Update completed: {plugin_id}",
                        plugin_id=plugin_id,
                    ),
                    False,
                    False,
                )
            if attempt + 1 >= max_attempts:
                return (
                    self._format(
                        "Update completed without version change: {plugin_id} "
                        "(version {version})",
                        plugin_id=plugin_id,
                        version=after_version or "unknown",
                    ),
                    False,
                    False,
                )
            return (
                self._format(
                    "Update may be pending: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                True,
            )
        if attempt + 1 >= max_attempts:
            return (
                self._format(
                    "Update failed: {plugin_id} not found.",
                    plugin_id=plugin_id,
                ),
                True,
                False,
            )
        return (
            self._format(
                "Update may be pending: {plugin_id}",
                plugin_id=plugin_id,
            ),
            False,
            True,
        )

    def _status_for_rollback(
        self,
        action: str,
        plugin_id: str | None,
        before_ids: set[str],
        before_backup: bool,
        after_ids: set[str],
        after_backup: bool,
        attempt: int = 0,
        max_attempts: int = 12,
    ) -> tuple[str | None, bool, bool]:
        if action not in {"rollback"} or plugin_id is None:
            return None, False, False
        if plugin_id not in before_ids and not before_backup:
            return (
                self._format(
                    "Rollback unavailable: no backup for {plugin_id}",
                    plugin_id=plugin_id,
                ),
                True,
                False,
            )

        if plugin_id in after_ids and not after_backup:
            return (
                self._format(
                    "Rollback completed: {plugin_id}",
                    plugin_id=plugin_id,
                ),
                False,
                False,
            )
        if after_backup and attempt + 1 >= max_attempts:
            return (
                self._format(
                    "Rollback failed: backup still exists for {plugin_id}",
                    plugin_id=plugin_id,
                ),
                True,
                False,
            )
        return (
            self._format(
                "Rollback may be pending: {plugin_id}",
                plugin_id=plugin_id,
            ),
            False,
            True,
        )

    def _update_status_after_action(
        self,
        action: str,
        plugin_id: str | None,
        before_ids: set[str],
        before_enabled: bool | None,
        before_version: str | None,
        before_backup: bool,
        attempt: int = 0,
    ) -> None:
        if not self._is_active():
            return
        if not self._safe_refresh_plugins():
            return
        after_ids = set(self._registry.all_ids())
        after_version = self._plugin_version(plugin_id)
        after_backup = self._has_rollback_backup(plugin_id)

        status: str | None = None
        is_error = False
        is_pending = False

        status, is_error, is_pending = self._status_for_import(
            action,
            before_ids,
            after_ids,
        )
        if status is None:
            status, is_error, is_pending = self._status_for_enable(
                action,
                plugin_id,
                before_enabled,
            )
        if status is None:
            status, is_error, is_pending = self._status_for_disable(
                action,
                plugin_id,
                before_enabled,
            )
        if status is None:
            status, is_error, is_pending = self._status_for_remove(
                action,
                plugin_id,
                before_ids,
                after_ids,
                attempt,
                self._status_poll_retries,
            )
        if status is None:
            status, is_error, is_pending = self._status_for_update(
                action,
                plugin_id,
                before_version,
                after_version,
                after_ids,
                attempt,
                self._status_poll_retries,
            )
        if status is None:
            status, is_error, is_pending = self._status_for_rollback(
                action,
                plugin_id,
                before_ids,
                before_backup,
                after_ids,
                after_backup,
                attempt,
                self._status_poll_retries,
            )

        if status is not None:
            self._set_status(status, is_error)

        if is_pending and attempt < self._status_poll_retries:
            QTimer.singleShot(
                self._status_poll_interval_ms,
                lambda: self._update_status_after_action(
                    action,
                    plugin_id,
                    before_ids,
                    before_enabled,
                    before_version,
                    before_backup,
                    attempt + 1,
                ),
            )

    def _execute_action(
        self,
        action: str,
        plugin_id: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        before_ids, before_enabled, before_version, before_backup = self._plugin_snapshot(
            plugin_id,
        )
        self._emit_action(action, plugin_id=plugin_id, path=path)
        self._refresh_plugins()
        if action == "import":
            if path:
                self._set_status(self._format("Import requested: {path}", path=path))
            else:
                self._set_status(str(self._ts("Import requested.")))
        elif action == "update":
            if path:
                self._set_status(
                    self._format(
                        "Update requested ({plugin_id}): {path}",
                        plugin_id=plugin_id or "",
                        path=path,
                    )
                )
            elif plugin_id:
                self._set_status(self._format("Update requested: {plugin_id}", plugin_id=plugin_id))
            else:
                self._set_status(str(self._ts("Update requested.")))
        elif path:
            self._set_status(self._format("Import requested: {path}", path=path))
        elif plugin_id:
            self._set_status(
                self._format(
                    "{action} requested: {plugin_id}",
                    action=self._action_label(action),
                    plugin_id=plugin_id,
                )
            )
        else:
            self._set_status(self._format("{action} requested", action=self._action_label(action)))

        self._update_status_after_action(
            action,
            plugin_id,
            before_ids,
            before_enabled,
            before_version,
            before_backup,
            0,
        )

    def _emit_action(
        self, action: str, plugin_id: str | None = None, path: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"action": action}
        if plugin_id is not None:
            payload["plugin_id"] = plugin_id
        if path is not None:
            payload["path"] = path
        self._event_bus.emit("ui.plugins.manager", payload)

    def _update_button_states(self) -> None:
        has_selection = self._selected_plugin_id() is not None
        self._enable_btn.setEnabled(has_selection)
        self._disable_btn.setEnabled(has_selection)
        self._update_btn.setEnabled(has_selection)
        self._remove_btn.setEnabled(has_selection)
        selected_plugin_id = self._selected_plugin_id()
        self._rollback_btn.setEnabled(
            selected_plugin_id is not None and self._has_rollback_backup(selected_plugin_id),
        )

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            str(self._ts("Import Plugin Package")),
            "",
            str(self._ts("Plugin ZIP (*.zip);;All Files (*.*)")),
        )
        if not path:
            self._set_status(str(self._ts("Import cancelled.")))
            return
        self._execute_action("import", path=path)

    def _on_enable(self) -> None:
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            self._set_status(str(self._ts("No plugin selected.")), is_error=True)
            return
        self._execute_action("enable", plugin_id=plugin_id)

    def _on_disable(self) -> None:
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            self._set_status(str(self._ts("No plugin selected.")), is_error=True)
            return
        self._execute_action("disable", plugin_id=plugin_id)

    def _on_update(self) -> None:
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            self._set_status(str(self._ts("No plugin selected.")), is_error=True)
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            str(self._ts("Update Plugin Package")),
            "",
            str(self._ts("Plugin ZIP (*.zip);;All Files (*.*)")),
        )
        if not path:
            self._set_status(str(self._ts("Update cancelled.")))
            return
        self._execute_action("update", plugin_id=plugin_id, path=path)

    def _on_remove(self) -> None:
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            self._set_status(str(self._ts("No plugin selected.")), is_error=True)
            return

        confirmed, ok = QInputDialog.getText(
            self,
            str(self._ts("Confirm Remove")),
            self._format("Type '{plugin_id}' to confirm removal:", plugin_id=plugin_id),
        )
        if not ok or confirmed.strip() != plugin_id:
            self._set_status(str(self._ts("Remove cancelled.")))
            return
        self._execute_action("remove", plugin_id=plugin_id)

    def _on_rollback(self) -> None:
        plugin_id = self._selected_plugin_id()
        if plugin_id is None:
            self._set_status(str(self._ts("No plugin selected.")), is_error=True)
            return
        if not self._has_rollback_backup(plugin_id):
            self._set_status(
                self._format("No rollback backup for {plugin_id}", plugin_id=plugin_id),
                is_error=True,
            )
            return

        confirmed, ok = QInputDialog.getText(
            self,
            str(self._ts("Confirm Rollback")),
            self._format("Type '{plugin_id}' to confirm rollback:", plugin_id=plugin_id),
        )
        if not ok or confirmed.strip() != plugin_id:
            self._set_status(str(self._ts("Rollback cancelled.")))
            return
        self._execute_action("rollback", plugin_id=plugin_id)
