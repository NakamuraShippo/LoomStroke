"""
Plugin compatibility dialog.

Per Master.md PR#11 specifications, provides UI for plugin validation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...plugins.sdk.validator import PluginManifest, ValidationResult
from ..translations import TranslationBinder, translated_literal_domain

logger = logging.getLogger(__name__)


class PermissionLevel(Enum):
    """Permission risk levels."""

    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"


@dataclass
class PermissionRequest:
    """Plugin permission request details."""

    name: str
    description: str
    level: PermissionLevel
    required: bool = True


class PluginCompatibilityDialog(QDialog):
    """
    Dialog for plugin compatibility and permission requests.

    Shows validation results, compatibility warnings,
    and permission requests for plugins.
    """

    # Signals
    permissions_granted = Signal(dict)  # Dict[str, bool | list[str]]
    installation_approved = Signal(bool)
    permissions_group: QGroupBox | None
    warnings_label: QLabel | None
    warnings_list: QListWidget | None

    def __init__(
        self,
        manifest: PluginManifest,
        validation_result: ValidationResult,
        warnings: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        """
        Initialize compatibility dialog.

        Args:
            manifest: Plugin manifest
            validation_result: Validation result
            warnings: Optional compatibility warnings
            parent: Parent widget
        """
        super().__init__(parent)
        self.manifest = manifest
        self.validation_result = validation_result
        self.warnings = warnings or []
        self.permission_checkboxes: dict[str, QCheckBox] = {}
        self._network_domain_checkboxes: dict[str, QCheckBox] = {}
        self._translation_binder = TranslationBinder(self.tr)
        self._status_map = self._build_status_map()

        self._bind_format(self.setWindowTitle, "Plugin Installation - {name}", name=manifest.name)
        self.setModal(True)
        self.setMinimumWidth(500)

        self._setup_ui()
        self._populate_content()

    def _ts(self, text: str) -> str:
        return translated_literal_domain(
            "PluginCompatibilityDialog",
            text,
            use_fallback=True,
        )

    def _bind_format(
        self,
        setter: Callable[[str], None],
        template: str,
        /,
        **kwargs: object,
    ) -> None:
        values = {key: str(value) for key, value in kwargs.items()}

        def set_formatted_text(
            translated_template: str,
            setter: Callable[[str], None] = setter,
            values: dict[str, str] = values,
        ) -> None:
            setter(translated_template.format(**values))

        self._translation_binder.register(
            set_formatted_text,
            self._ts(template),
        )

    def _build_status_map(self) -> dict[ValidationResult, tuple[str, str]]:
        return {
            ValidationResult.VALID: (self._ts("✅ Valid"), "green"),
            ValidationResult.INCOMPATIBLE_VERSION: (self._ts("❌ Incompatible Version"), "red"),
            ValidationResult.INVALID_MANIFEST: (self._ts("❌ Invalid Manifest"), "red"),
            ValidationResult.MISSING_DEPENDENCY: (self._ts("❌ Missing Dependencies"), "red"),
            ValidationResult.PERMISSION_DENIED: (self._ts("⚠️ Permissions Required"), "orange"),
            ValidationResult.SIGNATURE_INVALID: (self._ts("❌ Invalid Signature"), "red"),
            ValidationResult.UNTRUSTED: (self._ts("⚠️ Untrusted Source"), "orange"),
        }

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._status_map = self._build_status_map()
        self._translation_binder.retranslate()
        self._set_status_label()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        self.info_group = self._build_info_group()
        layout.addWidget(self.info_group)

        self.status_group = self._build_status_group()
        layout.addWidget(self.status_group)

        if self._has_permissions():
            self.permissions_group = self._build_permissions_group()
            layout.addWidget(self.permissions_group)
        else:
            self.permissions_group = None

        self.compat_group = self._build_compatibility_group()
        layout.addWidget(self.compat_group)

        self.button_box = self._build_action_buttons()
        layout.addWidget(self.button_box)

    def _build_info_group(self) -> QGroupBox:
        """Create the plugin information section."""
        title = self._ts("Plugin Information")
        group = QGroupBox(title)
        self._translation_binder.register(group.setTitle, title)
        info_layout = QVBoxLayout()

        header_layout = QHBoxLayout()
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setStyleSheet("border: 1px solid #ccc; border-radius: 4px;")
        header_layout.addWidget(self.icon_label)

        header_info = QVBoxLayout()
        self.name_label = QLabel(f"<b>{self.manifest.name}</b>")
        self.version_label = QLabel()
        self.author_label = QLabel()
        self._bind_format(
            self.version_label.setText,
            "Version: {version}",
            version=self.manifest.version,
        )
        self._bind_format(
            self.author_label.setText,
            "Author: {author}",
            author=self.manifest.author,
        )
        header_info.addWidget(self.name_label)
        header_info.addWidget(self.version_label)
        header_info.addWidget(self.author_label)
        header_info.addStretch()

        header_layout.addLayout(header_info)
        header_layout.addStretch()
        info_layout.addLayout(header_layout)

        self.description_label = QLabel(self.manifest.description)
        self.description_label.setWordWrap(True)
        info_layout.addWidget(self.description_label)

        group.setLayout(info_layout)
        return group

    def _build_status_group(self) -> QGroupBox:
        """Create the validation status section."""
        title = self._ts("Validation Status")
        group = QGroupBox(title)
        self._translation_binder.register(group.setTitle, title)
        status_layout = QVBoxLayout()

        self.status_label = QLabel()
        self._set_status_label()
        status_layout.addWidget(self.status_label)

        if self.warnings:
            warning_title = self._ts("<b>Warnings:</b>")
            self.warnings_label = QLabel(warning_title)
            self._translation_binder.register(self.warnings_label.setText, warning_title)
            status_layout.addWidget(self.warnings_label)

            self.warnings_list = QListWidget()
            self.warnings_list.setMaximumHeight(100)
            for warning in self.warnings:
                item = QListWidgetItem(warning)
                item.setIcon(self._get_icon("warning"))
                self.warnings_list.addItem(item)
            status_layout.addWidget(self.warnings_list)
        else:
            self.warnings_label = None
            self.warnings_list = None

        group.setLayout(status_layout)
        return group

    def _build_permissions_group(self) -> QGroupBox:
        """Create the permissions section."""
        title = self._ts("Required Permissions")
        group = QGroupBox(title)
        self._translation_binder.register(group.setTitle, title)
        perm_layout = QVBoxLayout()

        perm_info = QLabel(self._ts("This plugin requires the following permissions to function:"))
        self._translation_binder.register(
            perm_info.setText,
            self._ts("This plugin requires the following permissions to function:"),
        )
        perm_info.setWordWrap(True)
        perm_layout.addWidget(perm_info)

        self._add_permission_checkbox(
            "file_system",
            self._ts("File System Access"),
            self._ts("Read and write files on your computer"),
            PermissionLevel.HIGH,
            perm_layout,
        )
        self._add_permission_checkbox(
            "network",
            self._ts("Network Access"),
            self._ts("Connect to internet services"),
            PermissionLevel.MODERATE,
            perm_layout,
        )
        if self.manifest.network_domains:
            label = QLabel(self._ts("Allowed network domains:"))
            self._translation_binder.register(label.setText, self._ts("Allowed network domains:"))
            perm_layout.addWidget(label)
            for domain in self.manifest.network_domains:
                domain_checkbox = QCheckBox(domain)
                domain_checkbox.setChecked(True)
                domain_checkbox.setStyleSheet("margin-left: 20px;")
                perm_layout.addWidget(domain_checkbox)
                self._network_domain_checkboxes[domain] = domain_checkbox

        self._add_permission_checkbox(
            "gpu",
            self._ts("GPU Acceleration"),
            self._ts("Use GPU for performance optimization"),
            PermissionLevel.SAFE,
            perm_layout,
        )

        group.setLayout(perm_layout)
        return group

    def _build_compatibility_group(self) -> QGroupBox:
        """Create compatibility information section."""
        title = self._ts("Compatibility")
        group = QGroupBox(title)
        self._translation_binder.register(group.setTitle, title)
        compat_layout = QVBoxLayout()
        self._engine_requirement_label = QLabel()
        self._python_requirement_label = QLabel()
        self._bind_format(
            self._engine_requirement_label.setText,
            "Engine requirement: {engine_version}",
            engine_version=self.manifest.engine_version,
        )
        self._bind_format(
            self._python_requirement_label.setText,
            "Python requirement: {python_version}",
            python_version=self.manifest.python_version,
        )
        compat_layout.addWidget(self._engine_requirement_label)
        compat_layout.addWidget(self._python_requirement_label)
        group.setLayout(compat_layout)
        return group

    def _build_action_buttons(self) -> QDialogButtonBox:
        """Create dialog action buttons."""
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        ok_button = buttons.button(QDialogButtonBox.Ok)
        if self.validation_result != ValidationResult.VALID:
            ok_button.setEnabled(False)
            cannot_install = self._ts("Cannot Install")
            ok_button.setText(cannot_install)
            self._translation_binder.register(ok_button.setText, cannot_install)
        else:
            install = self._ts("Install")
            ok_button.setText(install)
            self._translation_binder.register(ok_button.setText, install)

        return buttons

    def _populate_content(self) -> None:
        """Populate dialog with manifest content."""
        # Set plugin icon if available
        # For now, use a placeholder
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.lightGray)
        self.icon_label.setPixmap(pixmap)

    def _set_status_label(self) -> None:
        """Set status label based on validation result."""
        status_map = self._status_map
        text, color = status_map.get(self.validation_result, (self._ts("Unknown Status"), "gray"))
        text_html = f"<b style='color: {color};'>{text}</b>"
        self.status_label.setText(text_html)

    def _has_permissions(self) -> bool:
        """Check if plugin requires any permissions."""
        return self.manifest.file_system or self.manifest.network or self.manifest.gpu

    def _add_permission_checkbox(
        self, key: str, title: str, description: str, level: PermissionLevel, layout: QVBoxLayout
    ) -> None:
        """Add a permission checkbox to the layout."""
        if not getattr(self.manifest, key, False):
            return

        checkbox = QCheckBox(title)
        self._translation_binder.register(checkbox.setText, title)
        checkbox.setChecked(level == PermissionLevel.SAFE)  # Auto-check safe permissions

        # Style based on risk level
        color_map = {
            PermissionLevel.SAFE: "#4CAF50",
            PermissionLevel.MODERATE: "#FF9800",
            PermissionLevel.HIGH: "#F44336",
        }

        checkbox.setStyleSheet(f"""
            QCheckBox {{
                font-weight: bold;
            }}
            QCheckBox::indicator:checked {{
                background-color: {color_map[level]};
            }}
        """)

        layout.addWidget(checkbox)

        desc_label = QLabel(description)
        self._translation_binder.register(desc_label.setText, description)
        desc_label.setStyleSheet("margin-left: 20px; color: #666;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        self.permission_checkboxes[key] = checkbox

    def _collect_granted_permissions(self) -> dict[str, bool | list[str]]:
        granted: dict[str, bool | list[str]] = {}
        for key, checkbox in self.permission_checkboxes.items():
            granted[key] = checkbox.isChecked()

        if self.manifest.network and self.manifest.network_domains:
            domains = [
                domain
                for domain, checkbox in self._network_domain_checkboxes.items()
                if checkbox.isChecked()
            ]
            granted["network_domains"] = domains

        return granted

    def _get_icon(self, icon_type: str) -> QIcon:
        """Get icon for the given type."""
        # In real implementation, load actual icons
        # For now, return empty icon
        return QIcon()

    def _on_accept(self) -> None:
        """Handle accept button click."""
        # Collect granted permissions
        granted = self._collect_granted_permissions()

        if self.manifest.network and self.manifest.network_domains:
            domains = granted.get("network_domains")
            if not domains:
                self._show_permission_error(
                    self._ts("Network access requires at least one allowed domain")
                )
                return

        # Check if all required permissions are granted
        if self.manifest.file_system and not granted.get("file_system", False):
            self._show_permission_error(self._ts("File system access is required"))
            return

        if self.manifest.network and not granted.get("network", False):
            self._show_permission_error(self._ts("Network access is required"))
            return

        # Emit signals
        self.permissions_granted.emit(granted)
        self.installation_approved.emit(True)
        self.accept()

    def _show_permission_error(self, message: str) -> None:
        """Show permission error message."""
        QMessageBox.warning(
            self,
            self._ts("Permission Required"),
            (
                f"{message}\n\n"
                f"{self._ts('Please grant the required permissions to install this plugin.')}"
            ),
            QMessageBox.Ok,
        )


class PluginUpdateDialog(QDialog):
    """
    Dialog for plugin updates.

    Shows available updates and allows batch updating.
    """

    # Signals
    updates_selected = Signal(list)  # List of plugin IDs to update

    def __init__(
        self,
        updates: list[tuple[str, str, str]],  # (id, current, available)
        parent: QWidget | None = None,
    ):
        """
        Initialize update dialog.

        Args:
            updates: List of available updates
            parent: Parent widget
        """
        super().__init__(parent)
        self.updates = updates
        self.selected_updates: list[str] = []
        self._translation_binder = TranslationBinder(self.tr)

        title = self._ts("Plugin Updates Available")
        self.setWindowTitle(title)
        self._translation_binder.register(self.setWindowTitle, title)
        self.setModal(True)
        self.setMinimumWidth(500)

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Info label
        self._info_label = QLabel()
        update_count = len(self.updates)

        def set_update_count_label(
            translated_text: str,
            label: QLabel = self._info_label,
            count: int = update_count,
        ) -> None:
            label.setText(f"<b>{count} {translated_text}</b>")

        self._translation_binder.register(
            set_update_count_label,
            self._ts("plugin update(s) available"),
        )
        layout.addWidget(self._info_label)

        # Updates list
        self.updates_list = QListWidget()
        self.updates_list.setSelectionMode(QListWidget.MultiSelection)

        for plugin_id, current, available in self.updates:
            item_text = f"{plugin_id}: {current} -> {available}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, plugin_id)
            item.setSelected(True)  # Select all by default
            self.updates_list.addItem(item)

        layout.addWidget(self.updates_list)

        # Buttons
        button_layout = QHBoxLayout()

        select_all_text = self._ts("Select All")
        select_all_btn = QPushButton(select_all_text)
        self._translation_binder.register(select_all_btn.setText, select_all_text)
        select_all_btn.clicked.connect(self._select_all)
        button_layout.addWidget(select_all_btn)

        select_none_text = self._ts("Select None")
        select_none_btn = QPushButton(select_none_text)
        self._translation_binder.register(select_none_btn.setText, select_none_text)
        select_none_btn.clicked.connect(self._select_none)
        button_layout.addWidget(select_none_btn)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        ok_text = self._ts("Update Selected")
        ok_button = self.button_box.button(QDialogButtonBox.Ok)
        ok_button.setText(ok_text)
        self._translation_binder.register(ok_button.setText, ok_text)

        layout.addWidget(self.button_box)

    def _select_all(self) -> None:
        """Select all updates."""
        for i in range(self.updates_list.count()):
            self.updates_list.item(i).setSelected(True)

    def _select_none(self) -> None:
        """Deselect all updates."""
        for i in range(self.updates_list.count()):
            self.updates_list.item(i).setSelected(False)

    def _on_accept(self) -> None:
        """Handle accept button click."""
        # Collect selected updates
        self.selected_updates = []
        for item in self.updates_list.selectedItems():
            plugin_id = item.data(Qt.UserRole)
            self.selected_updates.append(plugin_id)

        if self.selected_updates:
            self.updates_selected.emit(self.selected_updates)
            self.accept()
        else:
            # No updates selected
            QMessageBox.information(
                self,
                self._ts("No Updates Selected"),
                self._ts("Please select at least one plugin to update."),
                QMessageBox.Ok,
            )

    def _ts(self, text: str) -> str:
        return translated_literal_domain(
            "PluginUpdateDialog",
            text,
            use_fallback=True,
        )

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
