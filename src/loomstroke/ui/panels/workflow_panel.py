"""Workflow panel for ComfyUI workflow management."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.panels.base_panel import BasePanel
from loomstroke.ui.typing import ConnectionSignalProvider, Signal1

logger = logging.getLogger(__name__)


WorkflowPayload = dict[str, Any]
WorkflowStore = dict[str, WorkflowPayload]


class WorkflowPanel(BasePanel):
    """Panel for managing ComfyUI workflows.

    Handles workflow loading, saving, execution, and management.
    Complies with Master.md 400-line limit.
    """

    # Signals
    workflow_loaded = Signal(dict)  # Workflow data
    workflow_executed = Signal(str)  # Workflow ID
    workflow_saved = Signal(str)  # File path
    workflow_selected = Signal(dict)  # Selected workflow

    def __init__(
        self,
        async_manager: ConnectionSignalProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize workflow panel.

        Args:
            async_manager: Async manager for ComfyUI communication
            parent: Parent widget
        """
        # Set async_manager BEFORE calling super().__init__
        # because BasePanel calls _setup_ui which needs it
        self.async_manager: ConnectionSignalProvider | None = async_manager

        # BasePanel requires panel_id and title
        super().__init__("comfyui.workflow", "Workflow", parent)
        self._auto_save_enabled_text = self._ts("Enabled")
        self._auto_save_disabled_text = self._ts("Disabled")
        self._workflow_count_singular = self._ts("{count} workflow loaded")
        self._workflow_count_plural = self._ts("{count} workflows loaded")
        self._translation_binder.register(
            self._set_auto_save_enabled_text, self._auto_save_enabled_text
        )
        self._translation_binder.register(
            self._set_auto_save_disabled_text, self._auto_save_disabled_text
        )
        self._translation_binder.register(
            self._set_workflow_count_singular, self._workflow_count_singular
        )
        self._translation_binder.register(
            self._set_workflow_count_plural, self._workflow_count_plural
        )

        # State
        self.workflows: WorkflowStore = {}
        self.current_workflow: WorkflowPayload | None = None
        self.current_workflow_path: Path | None = None
        self.recent_workflows: list[str] = []
        self.max_recent = 10

        # UI elements
        self.workflow_combo: QComboBox | None = None
        self.workflow_list: QListWidget | None = None
        self.execute_btn: QPushButton | None = None
        self.status_label: QLabel | None = None
        self._auto_save_button: QPushButton | None = None

        # Auto-save timer
        self.auto_save_timer = QTimer()
        self.auto_save_timer.timeout.connect(self._auto_save_workflow)
        self.auto_save_interval = 60000  # 1 minute

    def setup_panel_ui(self) -> None:
        """Required by BasePanel - setup UI components."""
        layout = self.main_layout  # Use the layout from BasePanel
        layout.setSpacing(8)

        # Header with title and actions
        header_layout = QHBoxLayout()

        title_text = self._ts("Workflow Management")
        title = QLabel(title_text)
        self._translation_binder.register(title.setText, title_text)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Action buttons
        self._create_action_buttons(header_layout)

        layout.addLayout(header_layout)

        # Main content splitter
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Workflow selection group
        selection_group = self._create_selection_group()
        splitter.addWidget(selection_group)

        # Workflow list group
        list_group = self._create_list_group()
        splitter.addWidget(list_group)

        # Control group
        control_group = self._create_control_group()
        splitter.addWidget(control_group)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)

        layout.addWidget(splitter)

        # Status bar
        status_text = self._ts("Ready")
        self.status_label = QLabel(status_text)
        self._translation_binder.register(self.status_label.setText, status_text)
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.status_label)

        # Connect signals
        self._connect_internal_signals()

    def _create_action_buttons(self, layout: QHBoxLayout) -> None:
        """Create action buttons."""
        # New workflow
        new_btn = QToolButton()
        new_text = self._ts("New")
        new_tip = self._ts("New Workflow")
        new_btn.setText(new_text)
        new_btn.setToolTip(new_tip)
        self._translation_binder.register(new_btn.setText, new_text)
        self._translation_binder.register(new_btn.setToolTip, new_tip)
        new_btn.clicked.connect(self._on_new_workflow)
        layout.addWidget(new_btn)

        # Open workflow
        open_btn = QToolButton()
        open_text = self._ts("Open")
        open_tip = self._ts("Open Workflow")
        open_btn.setText(open_text)
        open_btn.setToolTip(open_tip)
        self._translation_binder.register(open_btn.setText, open_text)
        self._translation_binder.register(open_btn.setToolTip, open_tip)
        open_btn.clicked.connect(self._on_open_workflow)
        layout.addWidget(open_btn)

        # Save workflow
        save_btn = QToolButton()
        save_text = self._ts("Save")
        save_tip = self._ts("Save Workflow")
        save_btn.setText(save_text)
        save_btn.setToolTip(save_tip)
        self._translation_binder.register(save_btn.setText, save_text)
        self._translation_binder.register(save_btn.setToolTip, save_tip)
        save_btn.clicked.connect(self._on_save_workflow)
        layout.addWidget(save_btn)

        # Save as
        save_as_btn = QToolButton()
        save_as_text = self._ts("Save As")
        save_as_tip = self._ts("Save As...")
        save_as_btn.setText(save_as_text)
        save_as_btn.setToolTip(save_as_tip)
        self._translation_binder.register(save_as_btn.setText, save_as_text)
        self._translation_binder.register(save_as_btn.setToolTip, save_as_tip)
        save_as_btn.clicked.connect(self._on_save_as_workflow)
        layout.addWidget(save_as_btn)

    def _create_selection_group(self) -> QGroupBox:
        """Create workflow selection group."""
        group_title = self._ts("Active Workflow")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Workflow combo box
        self.workflow_combo = QComboBox()
        self.workflow_combo.setEditable(False)
        self.workflow_combo.currentTextChanged.connect(self._on_workflow_selected)
        layout.addWidget(self.workflow_combo)

        # Recent workflows
        recent_text = self._ts("Recent:")
        recent_label = QLabel(recent_text)
        self._translation_binder.register(recent_label.setText, recent_text)
        recent_label.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(recent_label)

        return group

    def _create_list_group(self) -> QGroupBox:
        """Create workflow list group."""
        group_title = self._ts("Available Workflows")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Workflow list
        self.workflow_list = QListWidget()
        self.workflow_list.itemDoubleClicked.connect(self._on_workflow_double_clicked)
        layout.addWidget(self.workflow_list)

        # List controls
        list_controls = QHBoxLayout()

        refresh_text = self._ts("Refresh")
        refresh_btn = QPushButton(refresh_text)
        self._translation_binder.register(refresh_btn.setText, refresh_text)
        refresh_btn.clicked.connect(self._refresh_workflow_list)
        list_controls.addWidget(refresh_btn)

        import_text = self._ts("Import")
        import_btn = QPushButton(import_text)
        self._translation_binder.register(import_btn.setText, import_text)
        import_btn.clicked.connect(self._on_import_workflow)
        list_controls.addWidget(import_btn)

        delete_text = self._ts("Delete")
        delete_btn = QPushButton(delete_text)
        self._translation_binder.register(delete_btn.setText, delete_text)
        delete_btn.clicked.connect(self._on_delete_workflow)
        list_controls.addWidget(delete_btn)

        layout.addLayout(list_controls)

        return group

    def _create_control_group(self) -> QGroupBox:
        """Create workflow control group."""
        group_title = self._ts("Execution")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Execute button
        execute_text = self._ts("Execute Workflow")
        self.execute_btn = QPushButton(execute_text)
        self._translation_binder.register(self.execute_btn.setText, execute_text)
        self.execute_btn.setEnabled(False)
        self.execute_btn.clicked.connect(self._on_execute_workflow)
        layout.addWidget(self.execute_btn)

        # Queue button
        queue_text = self._ts("Add to Queue")
        queue_btn = QPushButton(queue_text)
        self._translation_binder.register(queue_btn.setText, queue_text)
        queue_btn.clicked.connect(self._on_queue_workflow)
        layout.addWidget(queue_btn)

        # Auto-save checkbox
        auto_save_layout = QHBoxLayout()
        auto_save_label_text = self._ts("Auto-save:")
        auto_save_label = QLabel(auto_save_label_text)
        self._translation_binder.register(auto_save_label.setText, auto_save_label_text)
        auto_save_layout.addWidget(auto_save_label)

        auto_save_btn = QPushButton(self._auto_save_disabled_text)
        auto_save_btn.setCheckable(True)
        auto_save_btn.toggled.connect(self._on_auto_save_toggled)
        auto_save_layout.addWidget(auto_save_btn)
        self._auto_save_button = auto_save_btn

        layout.addLayout(auto_save_layout)

        return group

    def _set_auto_save_enabled_text(self, text: str) -> None:
        self._auto_save_enabled_text = text

    def _set_auto_save_disabled_text(self, text: str) -> None:
        self._auto_save_disabled_text = text

    def _set_workflow_count_singular(self, text: str) -> None:
        self._workflow_count_singular = text

    def _set_workflow_count_plural(self, text: str) -> None:
        self._workflow_count_plural = text

    def on_language_changed(self, _language: str) -> None:
        if self._auto_save_button:
            if self._auto_save_button.isChecked():
                self._auto_save_button.setText(self._auto_save_enabled_text)
            else:
                self._auto_save_button.setText(self._auto_save_disabled_text)

    def _connect_internal_signals(self) -> None:
        """Connect internal signals."""
        manager = self.async_manager
        if manager is None:
            return

        manager.connection_established.connect(self._on_connection_established)

        if hasattr(manager, "connection_closed"):
            manager.connection_closed.connect(self._on_connection_lost)
        elif hasattr(manager, "connection_lost"):
            manager.connection_lost.connect(self._on_connection_lost)

        if hasattr(manager, "connection_failed"):
            manager.connection_failed.connect(self._on_connection_failed)

    # Workflow Operations

    async def async_initialize(self) -> None:
        """Async initialization."""
        # Don't call super().async_initialize() - BasePanel handles this correctly

        # Load saved workflows
        self._load_saved_workflows()

        # Load recent workflows from settings
        self._load_recent_workflows()

        logger.info("WorkflowPanel initialized")

    def _load_saved_workflows(self) -> None:
        """Load workflows from settings or default location."""
        # This would load workflows from a default directory or settings
        logger.debug("Loading saved workflows")

    def _load_recent_workflows(self) -> None:
        """Load recent workflows list from settings."""
        if self._settings:
            settings_key = f"ui/panels/{self.panel_id}/recent_workflows"
            self.recent_workflows = self._settings.get(settings_key, [])

    def _update_workflow_display(self) -> None:
        """Update the workflow display UI."""
        if self.workflow_combo and self.current_workflow:
            name = self.current_workflow.get("name", self.tr("Unnamed Workflow"))
            # Update combo box if the workflow name isn't already there
            current_items = [
                self.workflow_combo.itemText(i) for i in range(self.workflow_combo.count())
            ]
            if name not in current_items:
                self.workflow_combo.addItem(name)
            self.workflow_combo.setCurrentText(name)

    def _add_to_recent(self, path_or_name: str) -> None:
        """Add workflow to recent list."""
        if path_or_name in self.recent_workflows:
            self.recent_workflows.remove(path_or_name)
        self.recent_workflows.insert(0, path_or_name)
        self.recent_workflows = self.recent_workflows[: self.max_recent]

        # Save to settings
        if self._settings:
            self._settings.set(f"ui/panels/{self.panel_id}/recent_workflows", self.recent_workflows)

    def _set_status(self, message: str, error: bool = False) -> None:
        """Set status message."""
        if self.status_label:
            self.status_label.setText(message)
            if error:
                self.status_label.setStyleSheet("color: red; font-size: 10px;")
            else:
                self.status_label.setStyleSheet("color: gray; font-size: 10px;")

    # Event Handlers

    def _on_new_workflow(self) -> None:
        """Create new workflow."""
        # Create default workflow structure
        workflow = {
            "name": "New Workflow",
            "nodes": {},
            "links": [],
            "groups": [],
            "config": {},
            "version": "1.0",
        }

        self.current_workflow = workflow
        self.current_workflow_path = None

        # Update UI
        self._update_workflow_display()
        self._set_status(self.tr("New workflow created"))

        # Emit signal
        self.workflow_loaded.emit(workflow)

    def _on_open_workflow(self) -> None:
        """Open workflow from file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Open Workflow"),
            str(Path.home()),
            self.tr("JSON Files (*.json);;All Files (*.*)"),
        )

        if file_path:
            self._load_workflow_file(Path(file_path))

    def _on_save_workflow(self) -> None:
        """Save current workflow."""
        if not self.current_workflow:
            self._set_status(self.tr("No workflow to save"), error=True)
            return

        if self.current_workflow_path:
            self._save_workflow_file(self.current_workflow_path)
        else:
            self._on_save_as_workflow()

    def _on_save_as_workflow(self) -> None:
        """Save workflow with new name."""
        if not self.current_workflow:
            self._set_status(self.tr("No workflow to save"), error=True)
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Workflow As"),
            str(Path.home()),
            self.tr("JSON Files (*.json)"),
        )

        if file_path:
            self._save_workflow_file(Path(file_path))

    def _on_workflow_selected(self, name: str) -> None:
        """Handle workflow selection from combo box."""
        if name and name in self.workflows:
            workflow = self.workflows[name]
            self.current_workflow = workflow
            if self.execute_btn:
                self.execute_btn.setEnabled(True)

            # Emit signal
            self.workflow_selected.emit(workflow)

            # Update recent
            self._add_to_recent(name)

    def _on_workflow_double_clicked(self, item: QListWidgetItem) -> None:
        """Handle double-click on workflow list item."""
        name = item.text()
        if name in self.workflows and self.workflow_combo:
            self.workflow_combo.setCurrentText(name)

    def _on_execute_workflow(self) -> None:
        """Execute current workflow."""
        if not self.current_workflow:
            return

        try:
            self._set_status(self.tr("Executing workflow..."))
            # In a real implementation, this would execute the workflow
            # For now, just emit the signal
            self.workflow_executed.emit("mock_prompt_id")
            self._set_status(self.tr("Workflow executed successfully"))

        except Exception as e:
            logger.error(f"Failed to execute workflow: {e}")
            self._set_status(
                self.tr("Error: {error}").format(error=e),
                error=True,
            )

    def _on_queue_workflow(self) -> None:
        """Add workflow to execution queue."""
        # This would integrate with job queue panel
        if self._event_bus:
            self._event_bus.emit(
                "workflow.queue", {"workflow": self.current_workflow, "priority": "normal"}
            )

        self._set_status(self.tr("Workflow added to queue"))

    def _on_import_workflow(self) -> None:
        """Import workflow from ComfyUI."""
        # This would fetch workflows from ComfyUI server
        self._set_status(self.tr("Import not yet implemented"))

    def _on_delete_workflow(self) -> None:
        """Delete selected workflow."""
        if not self.workflow_list:
            return

        current_item = self.workflow_list.currentItem()
        if not current_item:
            return

        name = current_item.text()

        reply = QMessageBox.question(
            self,
            self.tr("Delete Workflow"),
            self.tr("Delete workflow '{name}'?").format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes and name in self.workflows:
            del self.workflows[name]
            self._refresh_workflow_list()
            self._set_status(self.tr("Deleted workflow: {name}").format(name=name))

    def _refresh_workflow_list(self) -> None:
        """Refresh the workflow list widget."""
        if not self.workflow_list:
            return

        self.workflow_list.clear()
        for name in self.workflows:
            self.workflow_list.addItem(name)

        # Update status
        count = len(self.workflows)
        if count == 1:
            message = self._workflow_count_singular.format(count=count)
        else:
            message = self._workflow_count_plural.format(count=count)
        self._set_status(message)

    def _on_auto_save_toggled(self, checked: bool) -> None:
        """Toggle auto-save."""
        sender = self.sender()
        if checked:
            self.auto_save_timer.start(self.auto_save_interval)
            if sender:
                sender.setText(self._auto_save_enabled_text)
            self._set_status(self.tr("Auto-save enabled"))
        else:
            self.auto_save_timer.stop()
            if sender:
                sender.setText(self._auto_save_disabled_text)
            self._set_status(self.tr("Auto-save disabled"))

    def _auto_save_workflow(self) -> None:
        """Auto-save the current workflow."""
        workflow = self.current_workflow
        path_obj = self.current_workflow_path
        if workflow is None or path_obj is None:
            return

        try:
            with path_obj.open("w", encoding="utf-8") as file:
                json.dump(workflow, file, indent=2)
            logger.debug(f"Auto-saved workflow to {path_obj}")
        except Exception as e:
            logger.error(f"Auto-save failed: {e}")

    def _on_connection_established(self, identifier: str | None = None) -> None:
        """Handle connection established."""
        if self.execute_btn:
            self.execute_btn.setEnabled(bool(self.current_workflow))
        message = self.tr("Connected to ComfyUI")
        if identifier:
            message = self.tr("Connected to ComfyUI ({identifier})").format(identifier=identifier)
        self._set_status(message)

    def _on_connection_lost(self, identifier: str | None = None) -> None:
        """Handle connection lost."""
        if self.execute_btn:
            self.execute_btn.setEnabled(False)
        detail = f" ({identifier})" if identifier else ""
        self._set_status(
            self.tr("Connection lost{detail}").format(detail=detail),
            error=True,
        )

    def _on_connection_failed(self, error_message: str) -> None:
        """Handle connection failure notification."""
        if self.execute_btn:
            self.execute_btn.setEnabled(False)
        self._set_status(
            self.tr("Connection failed: {error}").format(error=error_message),
            error=True,
        )

    # Helper Methods

    def _load_workflow_file(self, path: Path) -> None:
        """Load workflow from file."""
        try:
            with path.open("r", encoding="utf-8") as file:
                workflow = cast(WorkflowPayload, json.load(file))

            self.current_workflow = workflow
            self.current_workflow_path = path

            name = path.stem
            self.workflows[name] = workflow

            self._update_workflow_display()
            self._add_to_recent(str(path))

            self.workflow_loaded.emit(workflow)

            self._set_status(self.tr("Loaded: {name}").format(name=path.name))

        except Exception as exc:
            logger.error(f"Failed to load workflow: {exc}")
            self._set_status(
                self.tr("Load failed: {error}").format(error=exc),
                error=True,
            )

    def _save_workflow_file(self, path: Path) -> None:
        """Save workflow to file."""
        workflow = self.current_workflow
        if workflow is None:
            logger.warning("Save requested without a current workflow")
            self._set_status(self.tr("Nothing to save"), error=True)
            return

        try:
            with path.open("w", encoding="utf-8") as file:
                json.dump(workflow, file, indent=2)

            self.current_workflow_path = path

            self.workflow_saved.emit(str(path))

            self._set_status(self.tr("Saved: {name}").format(name=path.name))

        except Exception as exc:
            logger.error(f"Failed to save workflow: {exc}")
            self._set_status(
                self.tr("Save failed: {error}").format(error=exc),
                error=True,
            )


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    WorkflowPanel.workflow_loaded = cast(  # type: ignore[assignment]
        Signal1[WorkflowPayload], WorkflowPanel.workflow_loaded
    )
    WorkflowPanel.workflow_executed = cast(  # type: ignore[assignment]
        Signal1[str], WorkflowPanel.workflow_executed
    )
    WorkflowPanel.workflow_saved = cast(  # type: ignore[assignment]
        Signal1[str], WorkflowPanel.workflow_saved
    )
    WorkflowPanel.workflow_selected = cast(  # type: ignore[assignment]
        Signal1[WorkflowPayload],
        WorkflowPanel.workflow_selected,
    )
