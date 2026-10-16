"""ComfyUI workflow management UI.

This module provides workflow file operations and management,
adapted from the legacy implementation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QWidget,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.platform.user_dirs import ensure_directory, plugin_data_dir
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_with_fallback,
    translated_literal,
)

if TYPE_CHECKING:
    from ..extended_service import ExtendedComfyUIService
    from ..workflow_model import WorkflowModel

logger = logging.getLogger(__name__)


class WorkflowManager(QWidget):
    """Workflow file management widget.

    Provides workflow loading, saving, and recent files management.
    """

    # Signals
    workflow_loaded = Signal(object)  # WorkflowModel
    workflow_saved = Signal(str)  # file path
    status_message = Signal(str)

    def __init__(
        self, service: ExtendedComfyUIService | None = None, parent: QWidget | None = None
    ):
        """Initialize workflow manager.

        Args:
            service: ComfyUI service instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.service = service
        self.current_workflow: WorkflowModel | None = None
        self.current_file: Path | None = None
        self.modified = False
        self._workflows_root = ensure_directory(plugin_data_dir("comfyui") / "workflows")
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []

        self.init_ui()
        self.update_recent_menu()
        self._subscribe_language_events()

    def init_ui(self) -> None:
        """Initialize the UI components."""
        layout = QHBoxLayout()
        layout.setContentsMargins(5, 2, 5, 2)

        # Workflow label
        workflow_label_text = self._ts("Workflow:")
        workflow_label = QLabel(workflow_label_text)
        self._translation_binder.register(workflow_label.setText, workflow_label_text)
        layout.addWidget(workflow_label)

        # Workflow combo box
        self.workflow_combo = QComboBox()
        self.workflow_combo.setMinimumWidth(0)
        self.workflow_combo.setMinimumContentsLength(8)
        self.workflow_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.workflow_combo.currentIndexChanged.connect(self.on_workflow_selected)
        layout.addWidget(self.workflow_combo, 1)

        # Load button with menu
        load_text = self._ts("Load")
        self.load_button = QPushButton(load_text)
        self._translation_binder.register(self.load_button.setText, load_text)
        self.load_button.clicked.connect(self.load_workflow)

        # Recent files menu
        self.recent_menu = QMenu(self)
        self.load_button.setMenu(self.recent_menu)

        layout.addWidget(self.load_button)

        # Save button
        save_text = self._ts("Save")
        self.save_button = QPushButton(save_text)
        self._translation_binder.register(self.save_button.setText, save_text)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_workflow)
        layout.addWidget(self.save_button)

        # Save As button
        save_as_text = self._ts("Save As...")
        self.save_as_button = QPushButton(save_as_text)
        self._translation_binder.register(self.save_as_button.setText, save_as_text)
        self.save_as_button.setEnabled(False)
        self.save_as_button.clicked.connect(self.save_workflow_as)
        layout.addWidget(self.save_as_button)

        # New button
        new_text = self._ts("New")
        self.new_button = QPushButton(new_text)
        self._translation_binder.register(self.new_button.setText, new_text)
        self.new_button.clicked.connect(self.new_workflow)
        layout.addWidget(self.new_button)

        layout.addStretch()

        # Modified indicator
        self.modified_label = QLabel("")
        self.modified_label.setStyleSheet("color: orange; font-weight: bold;")
        layout.addWidget(self.modified_label)

        self.setLayout(layout)

        # Initialize combo
        self.update_workflow_combo()

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self.update_workflow_combo()
        self.update_recent_menu()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("WorkflowManager", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def update_workflow_combo(self) -> None:
        """Update the workflow combo box."""
        self.workflow_combo.clear()

        if self.current_file:
            self.workflow_combo.addItem(self.current_file.name, str(self.current_file))
        else:
            self.workflow_combo.addItem(self._ts("(No workflow loaded)"), None)

        # Add available workflows under user_data/plugins/comfyui/workflows
        if self.service:
            available = self.service.list_available_workflows()
            if available:
                self.workflow_combo.insertSeparator(1)
                for path in available:
                    if path != self.current_file:
                        self.workflow_combo.addItem(f"  {path.name}", str(path))

    def update_recent_menu(self) -> None:
        """Update the recent files menu."""
        self.recent_menu.clear()

        if not self.service:
            return

        recent = self.service.list_available_workflows() or self.service.get_recent_workflows()

        if not recent:
            action = self.recent_menu.addAction(self._ts("(No recent files)"))
            action.setEnabled(False)
            return

        for path in recent[:10]:
            action = self.recent_menu.addAction(path.name)
            action.setData(str(path))
            action.triggered.connect(lambda checked, p=path: self.load_workflow_file(p))

        self.recent_menu.addSeparator()

        clear_action = self.recent_menu.addAction(self._ts("Clear Recent"))
        clear_action.triggered.connect(self.clear_recent)

    def load_workflow(self) -> None:
        """Show file dialog to load workflow."""
        # Check for unsaved changes
        if not self.check_save_changes():
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._ts("Load ComfyUI Workflow"),
            str(self._workflows_root),
            self._ts("JSON Files (*.json);;All Files (*)"),
        )

        if file_path:
            self.load_workflow_file(Path(file_path))

    def load_workflow_file(self, path: Path) -> None:
        """Load a workflow from file.

        Args:
            path: Path to workflow file
        """
        if not path.exists():
            QMessageBox.warning(
                self,
                self._ts("Error"),
                self._ts("File not found: {path}").format(path=path),
            )
            return

        import asyncio  # noqa: PLC0415

        async def load():
            try:
                workflow = await self.service.load_workflow(path)
                self.current_workflow = workflow
                saved_path = self.service.last_workflow_path() or path
                self.current_file = saved_path
                self.modified = False

                # Update UI
                self.update_workflow_combo()
                self.update_recent_menu()
                self.save_button.setEnabled(True)
                self.save_as_button.setEnabled(True)
                self.update_modified_indicator()

                # Emit signal
                self.workflow_loaded.emit(workflow)
                self.status_message.emit(self._ts("Loaded: {name}").format(name=saved_path.name))

                logger.info(f"Loaded workflow: {saved_path}")

            except Exception as e:
                QMessageBox.critical(
                    self,
                    self._ts("Error"),
                    self._ts("Failed to load workflow:\n{error}").format(error=e),
                )
                logger.error(f"Failed to load workflow: {e}")

        # Use QTimer to schedule async task in qasync-compatible way

        def schedule_load():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(load())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for load")

        QTimer.singleShot(0, schedule_load)

    def save_workflow(self) -> None:
        """Save the current workflow."""
        if not self.current_workflow:
            return

        if not self.current_file:
            self.save_workflow_as()
            return

        self.save_workflow_to_file(self.current_file)

    def save_workflow_as(self) -> None:
        """Save workflow with new name."""
        if not self.current_workflow:
            return

        default_name = self.current_file.name if self.current_file else "workflow.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save ComfyUI Workflow"),
            str(self._workflows_root / default_name),
            "JSON Files (*.json);;All Files (*)",
        )

        if file_path:
            self.save_workflow_to_file(Path(file_path))

    def save_workflow_to_file(self, path: Path) -> None:
        """Save workflow to file.

        Args:
            path: Target file path
        """
        if not self.current_workflow or not self.service:
            return

        import asyncio  # noqa: PLC0415

        async def save():
            try:
                await self.service.save_workflow(self.current_workflow, path)

                self.current_file = path
                self.modified = False

                # Update UI
                self.update_workflow_combo()
                self.update_recent_menu()
                self.update_modified_indicator()

                # Emit signal
                self.workflow_saved.emit(str(path))
                self.status_message.emit(f"Saved: {path.name}")

                logger.info(f"Saved workflow: {path}")

            except Exception as e:
                QMessageBox.critical(
                    self,
                    self.tr("Error"),
                    self.tr("Failed to save workflow:\n{error}").format(error=e),
                )
                logger.error(f"Failed to save workflow: {e}")

        # Use QTimer to schedule async task in qasync-compatible way

        def schedule_save():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(save())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for save")

        QTimer.singleShot(0, schedule_save)

    def new_workflow(self) -> None:
        """Create a new workflow."""
        # Check for unsaved changes
        if not self.check_save_changes():
            return

        # Create empty workflow
        from ..workflow_model import WorkflowModel  # noqa: PLC0415

        self.current_workflow = WorkflowModel()
        self.current_file = None
        self.modified = False

        # Update UI
        self.update_workflow_combo()
        self.save_button.setEnabled(False)
        self.save_as_button.setEnabled(True)
        self.update_modified_indicator()

        # Emit signal
        self.workflow_loaded.emit(self.current_workflow)
        self.status_message.emit(self.tr("New workflow created"))

    def clear_recent(self) -> None:
        """Clear recent workflows list."""
        if self.service:
            self.service._recent_workflows.clear()
            self.update_recent_menu()
            self.update_workflow_combo()

    def on_workflow_selected(self, index: int) -> None:
        """Handle workflow selection from combo.

        Args:
            index: Selected index
        """
        if index <= 0:
            return

        file_path = self.workflow_combo.itemData(index)
        if file_path and file_path != str(self.current_file) and self.check_save_changes():
            self.load_workflow_file(Path(file_path))

    def set_modified(self, modified: bool = True) -> None:
        """Set workflow modified state.

        Args:
            modified: Whether workflow is modified
        """
        self.modified = modified
        self.update_modified_indicator()

        if modified and self.current_file:
            self.save_button.setEnabled(True)

    def update_modified_indicator(self) -> None:
        """Update the modified indicator."""
        if self.modified:
            self.modified_label.setText("●")
            self.modified_label.setToolTip(self.tr("Workflow has unsaved changes"))
        else:
            self.modified_label.setText("")

    def check_save_changes(self) -> bool:
        """Check if there are unsaved changes.

        Returns:
            True if OK to proceed, False to cancel
        """
        if not self.modified:
            return True

        reply = QMessageBox.question(
            self,
            self.tr("Unsaved Changes"),
            self.tr("The current workflow has unsaved changes.\nDo you want to save them?"),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )

        if reply == QMessageBox.StandardButton.Save:
            self.save_workflow()
            return not self.modified  # Return False if save failed

        return reply == QMessageBox.StandardButton.Discard

    def get_current_workflow(self) -> WorkflowModel | None:
        """Get the current workflow.

        Returns:
            Current workflow or None
        """
        return self.current_workflow
