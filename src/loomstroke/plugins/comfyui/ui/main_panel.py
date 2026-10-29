"""Main ComfyUI panel implementation.

This is a complete version of the panel with all major components integrated,
preserving the user experience from the legacy implementation.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from asyncio import Task

    from loomstroke.host_api import HostAPI
    from loomstroke.plugins.comfyui.comfyui_generation_provider import (
        ComfyUIGenerationProvider,
    )
    from loomstroke.services.generation_history_store import GenerationHistoryStore

    from ..extended_service import ExtendedComfyUIService
    from ..workflow_model import WorkflowModel
    from .node_tabs import NodeTabsWidget

# Import UI components
from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.platform.user_dirs import ensure_directory, plugin_data_dir
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_runtime_text,
    translated_literal_domain,
)

from .connection_bar import ConnectionBar
from .workflow_manager import WorkflowManager

logger = logging.getLogger(__name__)
_PANEL_MINIMUM_SIZE_HINT = QSize(100, 50)


# Re-exported under the legacy private name so existing callers in
# this module (``await asyncio.to_thread(_generate_thumbnail, image_bytes)``)
# don't need to be touched. The implementation moved to
# :mod:`loomstroke.services.thumbnails` so the AI Effect adapter can
# produce the same WebP shape — keeping the helper duplicated would
# guarantee the Gallery would split into mismatched thumbnail formats
# the next time someone tweaked one copy.
from loomstroke.services.thumbnails import (  # noqa: E402, PLC0415
    generate_gallery_thumbnail as _generate_thumbnail,
)


class ComfyUIMainPanel(QWidget):
    """Main ComfyUI integration panel.

    This panel provides the primary interface for ComfyUI integration,
    including connection management, workflow operations, and job execution.
    """

    # Signals
    status_message = Signal(str)
    layer_import_requested = Signal(str, str)  # layer_name, image_path
    connection_status_changed = Signal(bool)

    def __init__(
        self,
        service: ExtendedComfyUIService,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
    ):
        """Initialize the main panel.

        Args:
            service: Extended ComfyUI service instance
            host_api: Optional host API for canvas operations
            parent: Optional parent widget
        """
        super().__init__(parent)

        self.service: ExtendedComfyUIService = service
        self.host_api: HostAPI | None = host_api

        # Generation history integration
        self._history_store: GenerationHistoryStore | None = None
        self._generation_provider: ComfyUIGenerationProvider | None = None
        self._pending_contexts: dict[str, dict[str, Any]] = {}

        # Track workflow state
        self.current_workflow: WorkflowModel | None = None
        self.current_job_id: str | None = None
        self._loading_workflow_from_combo = False
        # Ensure plugin-local data roots exist under user_data/
        self._data_root = plugin_data_dir("comfyui")
        self._workflows_root = ensure_directory(self._data_root / "workflows")

        # UI components (created in init_ui)
        self.connection_bar: ConnectionBar | None = None
        self.workflow_manager: WorkflowManager | None = None
        self.node_tabs: NodeTabsWidget | None = None
        self.queue_tab: QWidget | None = None
        self.queue_table: QTableWidget | None = None
        self.queue_stats_label: QLabel | None = None
        self.queue_tab_cancel_button: QPushButton | None = None
        self.queue_tab_preview_button: QPushButton | None = None
        self.queue_tab_clear_button: QPushButton | None = None
        self.queue_repeat_label: QLabel | None = None
        self.queue_repeat_spin: QSpinBox | None = None
        self.queue_jobs: dict[str, dict[str, Any]] = {}
        self.queue_repeat_infinite = False
        self.queue_repeat_remaining: int | None = None
        self._queue_task: Task[None] | None = None

        # Event bus bookkeeping
        self._job_event_tokens: list[tuple[str, Any]] = []
        self._job_event_handlers: dict[str, Callable[[Any], None]] = {}
        self._job_event_bus: Any = None
        self._translation_binder = TranslationBinder(self.tr)
        self._event_bus = get_extended_event_bus()
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._queue_stats_template = self._ts("{pending} pending, {running} running")
        self._queue_header_sources = [
            self._ts("ID"),
            self._ts("Status"),
            self._ts("Progress"),
            self._ts("Workflow"),
            self._ts("Started"),
            self._ts("Notes"),
        ]
        self._translation_binder.register(
            self._set_queue_stats_template, self._queue_stats_template
        )

        # Initialize UI
        self.init_ui()
        self.connect_signals()
        self._subscribe_language_events()

        # Ensure event subscriptions are cleaned when the panel is destroyed
        self.destroyed.connect(self._on_panel_destroyed)

        # Schedule initial updates
        QTimer.singleShot(100, self.initial_setup)

    def minimumSizeHint(self) -> QSize:
        return _PANEL_MINIMUM_SIZE_HINT

    def _create_queue_tab(self) -> QWidget:  # noqa: PLR0915
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        header_layout = QHBoxLayout()
        title_text = self._ts("Job Queue")
        title = QLabel(title_text)
        self._translation_binder.register(title.setText, title_text)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.queue_stats_label = QLabel(self._queue_stats_template.format(pending=0, running=0))
        self.queue_stats_label.setStyleSheet("color: gray;")
        header_layout.addWidget(self.queue_stats_label)
        layout.addLayout(header_layout)

        self.queue_table = QTableWidget()
        self.queue_table.setColumnCount(6)
        self.queue_table.setHorizontalHeaderLabels(self._queue_header_sources)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.queue_table.verticalHeader().setVisible(False)
        header = self.queue_table.horizontalHeader()
        header.setStretchLastSection(False)
        self.queue_table.setColumnWidth(0, 80)
        self.queue_table.setColumnWidth(1, 80)
        self.queue_table.setColumnWidth(2, 120)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setColumnWidth(4, 120)
        self.queue_table.setColumnWidth(5, 100)
        self.queue_table.itemSelectionChanged.connect(self._on_queue_table_selection_changed)
        layout.addWidget(self.queue_table)

        controls = QHBoxLayout()
        cancel_text = self._ts("Cancel")
        self.queue_tab_cancel_button = QPushButton(cancel_text)
        self._translation_binder.register(self.queue_tab_cancel_button.setText, cancel_text)
        self.queue_tab_cancel_button.setEnabled(False)
        self.queue_tab_cancel_button.clicked.connect(self._on_queue_tab_cancel_requested)
        controls.addWidget(self.queue_tab_cancel_button)
        preview_text = self._ts("Preview")
        self.queue_tab_preview_button = QPushButton(preview_text)
        self._translation_binder.register(self.queue_tab_preview_button.setText, preview_text)
        self.queue_tab_preview_button.setEnabled(False)
        self.queue_tab_preview_button.clicked.connect(self._on_queue_tab_preview_requested)
        controls.addWidget(self.queue_tab_preview_button)
        controls.addStretch()
        clear_text = self._ts("Clear Finished")
        self.queue_tab_clear_button = QPushButton(clear_text)
        self._translation_binder.register(self.queue_tab_clear_button.setText, clear_text)
        self.queue_tab_clear_button.clicked.connect(self._on_queue_tab_clear_finished)
        controls.addWidget(self.queue_tab_clear_button)
        layout.addLayout(controls)

        self._update_queue_stats()
        return widget

    # ------------------------------------------------------------------
    # Generation history integration
    # ------------------------------------------------------------------

    def set_history_store(
        self,
        store: GenerationHistoryStore,
        provider: ComfyUIGenerationProvider,
    ) -> None:
        """Attach a history store and provider for recording generation results."""
        self._history_store = store
        self._generation_provider = provider

    def _update_queue_stats(self) -> None:
        pending = sum(1 for job in self.queue_jobs.values() if job.get("status") == "pending")
        running = sum(1 for job in self.queue_jobs.values() if job.get("status") == "running")
        if self.queue_stats_label:
            self.queue_stats_label.setText(
                self._queue_stats_template.format(pending=pending, running=running)
            )

    def _set_queue_stats_template(self, text: str) -> None:
        self._queue_stats_template = text

    def _register_tab_title(self, widget: QWidget, title: str) -> None:
        if not hasattr(self, "content_tabs") or self.content_tabs is None:
            return
        index = self.content_tabs.indexOf(widget)
        if index < 0:
            return
        self._translation_binder.register(
            lambda text, idx=index: self.content_tabs.setTabText(idx, text),
            title,
        )

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self.update_workflow_list()
        if self.queue_table:
            self.queue_table.setHorizontalHeaderLabels(
                [translate_runtime_text(text, self.tr) for text in self._queue_header_sources]
            )
            for job_id in list(self.queue_jobs):
                self._refresh_queue_row(job_id)
        if self.node_tabs and hasattr(self.node_tabs, "refresh_translations"):
            self.node_tabs.refresh_translations()
        self._update_queue_stats()

    def _ts(self, text: str) -> str:
        return translated_literal_domain("ComfyUIMainPanel", text, use_fallback=True)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _current_workflow_label(self) -> str:
        if self.workflow_combo and self.workflow_combo.currentText():
            label = self.workflow_combo.currentText().strip()
            if label:
                return label
        return str(self._ts("Workflow"))

    def _queue_status_text(self, status: str) -> str:
        mapping = {
            "pending": self._ts("Pending"),
            "running": self._ts("Running"),
            "completed": self._ts("Completed"),
            "failed": self._ts("Failed"),
            "cancelled": self._ts("Cancelled"),
        }
        translated = mapping.get(status.lower())
        if translated is not None:
            return str(translated)
        return status.title()

    def _register_queue_job(self, job_id: str, workflow_name: str | None = None) -> None:
        if not job_id:
            return
        if job_id not in self.queue_jobs:
            started = datetime.now()
            self.queue_jobs[job_id] = {
                "status": "pending",
                "progress": 0,
                "total": 0,
                "workflow": workflow_name or self._current_workflow_label(),
                "started": started,
                "started_display": started.strftime("%H:%M:%S"),
                "message": "",
            }
        if self.queue_table:
            self._ensure_queue_row(job_id)
        self._refresh_queue_row(job_id)
        self._update_queue_stats()

    def _queue_row_for_job(self, job_id: str) -> int:
        if not self.queue_table:
            return -1
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == job_id:
                return row
        return -1

    def _ensure_queue_row(self, job_id: str) -> int:
        if not self.queue_table:
            return -1
        row = self._queue_row_for_job(job_id)
        if row != -1:
            return row
        row = self.queue_table.rowCount()
        self.queue_table.insertRow(row)
        item = QTableWidgetItem(job_id[:8])
        item.setData(Qt.ItemDataRole.UserRole, job_id)
        self.queue_table.setItem(row, 0, item)
        return row

    def _refresh_queue_row(self, job_id: str) -> None:
        if not self.queue_table:
            return
        job = self.queue_jobs.get(job_id)
        if not job:
            return
        row = self._ensure_queue_row(job_id)
        if row == -1:
            return

        def _set(col: int, text: str, *, user_data: str | None = None) -> None:
            item = self.queue_table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.queue_table.setItem(row, col, item)
            item.setText(text)
            if user_data is not None:
                item.setData(Qt.ItemDataRole.UserRole, user_data)

        _set(0, job_id[:8], user_data=job_id)
        _set(1, self._queue_status_text(str(job.get("status", "pending"))))
        _set(2, self._format_queue_progress(job))
        _set(3, str(job.get("workflow", self._ts("Workflow"))))
        _set(4, job.get("started_display", "--:--:--"))
        _set(5, job.get("message", ""))

    def _format_queue_progress(self, job: dict[str, Any]) -> str:
        step = job.get("progress") or job.get("step") or 0
        total = job.get("total") or 0
        if total > 0:
            percent = int(max(0, min(100, (step / total) * 100))) if total else 0
            return f"{percent}% ({step}/{total})"
        if step:
            return str(step)
        return "-"

    def _set_queue_job_status(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        if not job_id:
            return
        if job_id not in self.queue_jobs:
            self._register_queue_job(job_id, "ComfyUI")
        job = self.queue_jobs[job_id]
        if status:
            job["status"] = status
        if progress is not None:
            job["progress"] = max(0, progress)
        if total is not None and total >= 0:
            job["total"] = total
        if message is not None:
            job["message"] = message or ""
        self._refresh_queue_row(job_id)
        self._update_queue_stats()

    def _selected_queue_job_id(self) -> str | None:
        if not self.queue_table:
            return None
        selected = self.queue_table.selectedItems()
        if not selected:
            return None
        row = self.queue_table.row(selected[0])
        item = self.queue_table.item(row, 0)
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _remove_queue_job_row(self, job_id: str) -> None:
        if not self.queue_table:
            return
        row = self._queue_row_for_job(job_id)
        if row >= 0:
            self.queue_table.removeRow(row)

    def _handle_repeat_queue_after_event(self) -> None:
        if self.queue_repeat_infinite:
            if not self._queue_workflow_once():
                self._stop_repeat_queue()
            return

        if self.queue_repeat_remaining is None:
            self._stop_repeat_queue()
            return

        if self.queue_repeat_remaining > 0:
            self.queue_repeat_remaining -= 1
            if not self._queue_workflow_once():
                self._stop_repeat_queue()
        else:
            self._stop_repeat_queue()

    def _stop_repeat_queue(self) -> None:
        self.queue_repeat_infinite = False
        self.queue_repeat_remaining = None

    def _on_queue_table_selection_changed(self) -> None:
        job_id = self._selected_queue_job_id()
        job = self.queue_jobs.get(job_id) if job_id else None
        status = job.get("status") if job else None
        if self.queue_tab_cancel_button:
            self.queue_tab_cancel_button.setEnabled(status in {"pending", "running"})
        if self.queue_tab_preview_button:
            self.queue_tab_preview_button.setEnabled(status == "completed")

    def _on_queue_tab_cancel_requested(self) -> None:
        job_id = self._selected_queue_job_id()
        if not job_id:
            return
        self._request_job_cancel(job_id)
        if self.queue_table:
            self.queue_table.clearSelection()

    def _on_queue_tab_preview_requested(self) -> None:
        job_id = self._selected_queue_job_id()
        if not job_id:
            return
        short_id = job_id[:8]
        self.status_message.emit(
            self._ts("Preview for job {job_id} is not available yet.").format(job_id=short_id)
        )

    def _clear_finished_jobs(self) -> int:
        finished = [
            job_id
            for job_id, job in self.queue_jobs.items()
            if job.get("status") in {"completed", "failed", "cancelled"}
        ]
        for job_id in finished:
            self._remove_queue_job_row(job_id)
            self.queue_jobs.pop(job_id, None)
        self._update_queue_stats()
        return len(finished)

    def _on_queue_tab_clear_finished(self) -> None:
        cleared = self._clear_finished_jobs()
        if cleared:
            self.status_message.emit(
                self._ts("Cleared {count} finished jobs").format(count=cleared)
            )
        else:
            self.status_message.emit(str(self._ts("No finished jobs to clear")))

    def set_host_api(self, host_api: HostAPI | None) -> None:
        """Update host API references for child components."""
        if host_api is self.host_api:
            return

        self._clear_layer_event_tokens()
        self.host_api = host_api
        if self.service:
            self.service.host_api = host_api
        if self.node_tabs:
            self.node_tabs.set_host_api(host_api)
        self.update_layer_list()
        self._subscribe_layer_events()
        self._subscribe_job_events()

    def init_ui(self) -> None:  # noqa: PLR0915
        """Initialize the user interface."""
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Connection bar
        self.connection_bar = ConnectionBar(self.service)
        self.connection_bar.connection_changed.connect(self.on_connection_changed)
        layout.addWidget(self.connection_bar)

        # Workflow manager bar (placeholder)
        workflow_layout = QHBoxLayout()

        workflow_label_text = self._ts("Workflow:")
        workflow_label = QLabel(workflow_label_text)
        self._translation_binder.register(workflow_label.setText, workflow_label_text)
        workflow_layout.addWidget(workflow_label)
        self.workflow_combo: QComboBox = QComboBox()
        self.workflow_combo.setMinimumWidth(0)
        self.workflow_combo.setMinimumContentsLength(8)
        self.workflow_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.workflow_combo.currentIndexChanged.connect(self.on_workflow_combo_changed)
        workflow_layout.addWidget(self.workflow_combo)

        load_text = self._ts("Load")
        self.load_workflow_button = self._create_compact_icon_button(
            self,
            tooltip=load_text,
            theme_icon="document-open",
            fallback_icon=QStyle.SP_DialogOpenButton,
            handler=self.on_load_workflow,
        )
        workflow_layout.addWidget(self.load_workflow_button)

        save_text = self._ts("Save")
        self.save_workflow_button = self._create_compact_icon_button(
            self,
            tooltip=save_text,
            theme_icon="document-save",
            fallback_icon=QStyle.SP_DialogSaveButton,
            handler=lambda: None,
        )
        self.save_workflow_button.setEnabled(False)
        workflow_layout.addWidget(self.save_workflow_button)

        workflow_layout.addStretch()
        layout.addLayout(workflow_layout)

        # Main content area (tabs for workflow nodes)
        self.content_tabs: QTabWidget = QTabWidget()

        # Node tabs - dynamically created for loaded workflows
        try:
            from .node_tabs import NodeTabsWidget  # noqa: PLC0415

            self.node_tabs = NodeTabsWidget(host_api=self.host_api)
            # Connect the correct signal
            if self.node_tabs is not None and hasattr(self.node_tabs, "node_parameters_changed"):
                self.node_tabs.node_parameters_changed.connect(self.on_nodes_updated)
            if self.node_tabs is not None:
                nodes_title = self._ts("Workflow Nodes")
                self.content_tabs.addTab(self.node_tabs, nodes_title)
                self._register_tab_title(self.node_tabs, nodes_title)
        except Exception as e:
            # Fallback if node tabs not available
            logger.warning("Could not create NodeTabsWidget: %s", e, exc_info=True)
            node_widget = QWidget()
            node_layout = QVBoxLayout()
            placeholder_text = self._ts("Node editor will appear here")
            placeholder_label = QLabel(placeholder_text)
            self._translation_binder.register(placeholder_label.setText, placeholder_text)
            node_layout.addWidget(placeholder_label)
            node_layout.addStretch()
            node_widget.setLayout(node_layout)
            nodes_title = self._ts("Workflow Nodes")
            self.content_tabs.addTab(node_widget, nodes_title)
            self._register_tab_title(node_widget, nodes_title)
            self.node_tabs = None

        # Queue tab with inline job queue UI
        self.queue_tab = self._create_queue_tab()
        queue_title = self._ts("Queue")
        self.content_tabs.addTab(self.queue_tab, queue_title)
        self._register_tab_title(self.queue_tab, queue_title)

        layout.addWidget(self.content_tabs)

        # Execution buttons
        button_layout = QHBoxLayout()

        queue_text = self._ts("Queue")
        self.queue_button = QPushButton(queue_text)
        self._translation_binder.register(self.queue_button.setText, queue_text)
        self.queue_button.setEnabled(False)
        self.queue_button.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                padding: 4px 10px;
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.queue_button.setFixedHeight(28)
        self.queue_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.queue_button.clicked.connect(self.on_queue_workflow)
        button_layout.addWidget(self.queue_button)

        cancel_text = self._ts("Cancel")
        self.cancel_button = self._create_compact_icon_button(
            self,
            tooltip=cancel_text,
            theme_icon="window-close",
            fallback_icon=QStyle.SP_DialogCancelButton,
            handler=self.on_cancel_job,
            icon_size=16,
            button_size=28,
        )
        self.cancel_button.setEnabled(False)
        button_layout.addWidget(self.cancel_button)

        repeat_layout = QHBoxLayout()
        repeat_layout.setContentsMargins(0, 0, 0, 0)
        repeat_layout.setSpacing(4)
        repeat_text = self._ts("Repeat:")
        repeat_tip = self._ts("Number of sequential runs (0 = infinite until cancel)")
        self.queue_repeat_label = QLabel(repeat_text)
        self.queue_repeat_label.setToolTip(repeat_tip)
        self._translation_binder.register(self.queue_repeat_label.setText, repeat_text)
        self._translation_binder.register(self.queue_repeat_label.setToolTip, repeat_tip)
        repeat_layout.addWidget(self.queue_repeat_label)
        self.queue_repeat_spin = QSpinBox()
        self.queue_repeat_spin.setRange(0, 9999)
        self.queue_repeat_spin.setValue(1)
        self.queue_repeat_spin.setFixedWidth(56)
        self.queue_repeat_spin.setToolTip(repeat_tip)
        self._translation_binder.register(self.queue_repeat_spin.setToolTip, repeat_tip)
        repeat_layout.addWidget(self.queue_repeat_spin)
        button_layout.addLayout(repeat_layout)

        button_layout.addStretch()

        clear_text = self._ts("Clear Finished")
        self.clear_button = QPushButton(clear_text)
        self._translation_binder.register(self.clear_button.setText, clear_text)
        self.clear_button.clicked.connect(self.on_clear_finished)
        button_layout.addWidget(self.clear_button)

        layout.addLayout(button_layout)

        self.setLayout(layout)

    def _create_compact_icon_button(
        self,
        parent: QWidget,
        *,
        tooltip: str,
        theme_icon: str,
        fallback_icon: QStyle.StandardPixmap,
        handler: Callable[[], None],
        icon_size: int = 16,
        button_size: int = 24,
    ) -> QToolButton:
        button = QToolButton(parent)
        icon = QIcon.fromTheme(theme_icon)
        if icon.isNull():
            icon = self.style().standardIcon(fallback_icon)
        button.setIcon(icon)
        self._translation_binder.register(button.setToolTip, tooltip)
        button.setIconSize(QSize(icon_size, icon_size))
        button.setFixedSize(QSize(button_size, button_size))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.clicked.connect(handler)
        return button

    def connect_signals(self) -> None:
        """Connect internal signals."""
        self._subscribe_job_events()

    def initial_setup(self) -> None:
        """Perform initial setup after UI is ready."""
        # Load recent workflows
        self.update_workflow_list()

        # Do not auto-connect - wait for user to click Connect button
        # Connection is now manual only

    def _subscribe_job_events(self) -> None:
        """Subscribe to job lifecycle events so UI can react."""
        event_bus = self._resolve_event_bus()
        if event_bus is None:
            logger.debug("No event bus available for ComfyUI job events")
            return

        if self._job_event_bus is event_bus and self._job_event_tokens:
            # Already subscribed to this bus
            logger.debug("Already subscribed to job events on this bus")
            return

        if self._job_event_bus is not event_bus:
            self._clear_job_event_tokens()

        subscribe = getattr(event_bus, "subscribe", None) or getattr(event_bus, "on", None)
        if subscribe is None:
            logger.warning(
                "Event bus does not expose subscribe/on; skipping job event subscription"
            )
            return

        logger.debug("Subscribing to job events on event bus: %s", type(event_bus).__name__)

        for event_name in (
            Events.AI_JOB_PROGRESS,
            Events.AI_JOB_RESULT,
            Events.AI_JOB_CANCELLED,
            Events.AI_JOB_FAILED,
        ):
            handler = self._make_job_event_handler(event_name)
            try:
                token = subscribe(event_name, handler)
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.debug("Failed to subscribe to '%s': %s", event_name, exc)
                continue

            self._job_event_handlers[event_name] = handler
            self._job_event_tokens.append((event_name, token))

        self._job_event_bus = event_bus if self._job_event_handlers else None
        logger.debug(
            "Job event subscription complete: %d handlers, bus=%s",
            len(self._job_event_handlers),
            self._job_event_bus is not None,
        )

    def _clear_job_event_tokens(self) -> None:
        """Unsubscribe job lifecycle handlers."""
        if not self._job_event_tokens:
            self._job_event_handlers.clear()
            self._job_event_bus = None
            return

        event_bus = self._job_event_bus or self._resolve_event_bus()
        if event_bus:
            off = getattr(event_bus, "off", None) or getattr(event_bus, "unsubscribe", None)
            if off:
                for event_name, token in self._job_event_tokens:
                    if token is None:
                        continue
                    try:
                        off(event_name, token)
                    except Exception as exc:  # pragma: no cover - defensive guard
                        logger.debug("Failed to unsubscribe from '%s': %s", event_name, exc)

        self._job_event_tokens.clear()
        self._job_event_handlers.clear()
        self._job_event_bus = None

    def _resolve_event_bus(self) -> Any:
        """Resolve the event bus used for ComfyUI events."""
        if self.host_api and hasattr(self.host_api, "events"):
            return self.host_api.events
        return getattr(self.service, "_event_bus", None)

    def _make_job_event_handler(self, event_name: str) -> Callable[[Any], None]:
        """Create a strong-referenced handler for the given event."""

        def _handler(payload: Any) -> None:
            self._handle_job_event(event_name, payload)

        return _handler

    def _handle_job_event(self, event_name: str, payload: Any) -> None:
        """React to job lifecycle events."""
        job_id = self._extract_event_field(payload, "job_id")
        if not job_id:
            return

        if event_name == Events.AI_JOB_PROGRESS:
            progress = self._extract_event_field(payload, "progress")
            if progress is None:
                progress = self._extract_event_field(payload, "step")
            total = self._extract_event_field(payload, "total") or 0
            message = self._extract_event_field(payload, "message")

            def update_progress() -> None:
                self._set_queue_job_status(
                    job_id,
                    status="running",
                    progress=progress or 0,
                    total=total,
                    message=message,
                )

            self._schedule_ui_update(update_progress)
            return

        success = bool(self._extract_event_field(payload, "success"))
        error_text = self._extract_event_field(payload, "error")
        cancelled = event_name == Events.AI_JOB_CANCELLED
        failed = (event_name == Events.AI_JOB_FAILED) or (
            event_name == Events.AI_JOB_RESULT and not success
        )
        status = "completed"
        if cancelled:
            status = "cancelled"
        elif failed:
            status = "failed"
        logger.debug(
            "Job event: event=%s job=%s success=%s failed=%s status=%s",
            event_name,
            job_id[:8],
            success,
            failed,
            status,
        )

        def update_ui() -> None:
            self._set_queue_job_status(job_id, status=status, message=error_text)
            if self.current_job_id == job_id:
                self.current_job_id = None
                if hasattr(self, "queue_button") and self.queue_button:
                    self.queue_button.setEnabled(True)
                if hasattr(self, "cancel_button") and self.cancel_button:
                    self.cancel_button.setEnabled(False)

            short_id = job_id[:8]
            if status == "cancelled":
                self.status_message.emit(self._ts("Job {job_id} cancelled").format(job_id=short_id))
            elif status == "failed":
                message = self._ts("Job {job_id} failed").format(job_id=short_id)
                if error_text:
                    message = self._ts("{message}: {error}").format(
                        message=message,
                        error=error_text,
                    )
                self.status_message.emit(message)
            else:
                self.status_message.emit(self._ts("Job {job_id} completed").format(job_id=short_id))

            self._handle_repeat_queue_after_event()

        self._schedule_ui_update(update_ui)

        # Record generation history without polling
        if status in {"completed", "failed", "cancelled"}:
            import asyncio  # noqa: PLC0415

            result_data = self._extract_event_field(payload, "result_data")
            try:
                loop = asyncio.get_event_loop()
                task = loop.create_task(  # noqa: RUF006
                    self._record_history(job_id, status, result_data=result_data)
                )
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except RuntimeError:
                logger.warning("No event loop for history recording of %s", job_id)

    async def _record_history(
        self,
        job_id: str,
        status: str,
        *,
        result_data: dict[str, Any] | None = None,
    ) -> None:
        """Record generation history for a completed/failed/cancelled job."""
        import asyncio  # noqa: PLC0415

        try:
            context = self._pending_contexts.pop(job_id, None)
            if not context or not self._history_store or not self._generation_provider:
                return

            record = self._generation_provider.build_record(job_id, None, context)
            record.status = status

            # Populate output_images from history entry if available
            if not record.output_images and isinstance(result_data, dict):
                outputs = result_data.get("outputs", {})
                images: list[dict[str, Any]] = []
                for node_output in outputs.values():
                    if not isinstance(node_output, dict):
                        continue
                    for img in node_output.get("images", []):
                        if isinstance(img, dict) and img.get("filename"):
                            images.append(img)
                if images:
                    record.output_images = images

            # Use pre-fetched image bytes if available, otherwise fetch
            try:
                image_bytes: bytes | None = None
                if isinstance(result_data, dict):
                    image_bytes = result_data.get("_image_bytes")
                if not image_bytes:
                    image_bytes = await self._generation_provider.fetch_result_image(record)
                if image_bytes:
                    record.image_path = self._history_store.save_image(job_id, image_bytes)
                    thumb_bytes = await asyncio.to_thread(_generate_thumbnail, image_bytes)
                    if thumb_bytes:
                        record.thumbnail_path = self._history_store.save_thumbnail(
                            job_id, thumb_bytes
                        )
            except Exception:
                logger.exception("Failed to save generation image for %s", job_id)

            self._history_store.add_record(record)
            if record.image_path:
                self._history_store.update_record(record)
            logger.info("Recorded generation history for %s (status=%s)", job_id[:8], status)
        except Exception:
            logger.exception("Failed to record generation history for %s", job_id)

    def _extract_event_field(self, payload: Any, field: str) -> Any:
        """Extract field value from dataclass-like or dict payload."""
        if payload is None:
            return None
        if hasattr(payload, field):
            return getattr(payload, field)
        if isinstance(payload, dict):
            return payload.get(field)
        return None

    def _schedule_ui_update(self, callback: Callable[[], None]) -> None:
        """Ensure UI updates run on the Qt event loop."""
        QTimer.singleShot(0, callback)

    def _is_external_services_allowed(self) -> bool:
        """Check whether external services are currently allowed for this panel."""
        try:
            settings = getattr(self.service, "_settings", None)
            if settings is None:
                settings = get_settings_manager()
            return is_external_services_allowed(settings)
        except Exception as exc:
            logger.debug(
                "Failed to resolve external service consent, defaulting to deny: %s",
                exc,
            )
            return False

    def auto_connect(self) -> None:
        """Attempt automatic connection to ComfyUI."""
        import asyncio  # noqa: PLC0415

        async def connect():
            try:
                if not self._is_external_services_allowed():
                    logger.debug("Skipping auto-connect because external services are disabled")
                    return

                await self.service.initialize()
                health = await self.service.health()

                if health:
                    self.status_message.emit(str(self._ts("Connected to ComfyUI")))

                    # Load object info if available
                    if hasattr(self.service, "get_object_info"):
                        await self.service.get_object_info()

            except Exception as e:
                logger.error(f"Auto-connect failed: {e}")

        # Use QTimer to schedule async task in qasync-compatible way
        def schedule_connect():
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(connect())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop available for auto-connect")

        QTimer.singleShot(0, schedule_connect)

    def update_layer_list(self) -> None:
        """Refresh layer-dependent widgets."""
        if self.node_tabs:
            self.node_tabs.refresh_layer_widgets()

    def _on_panel_destroyed(self, _obj: Any = None) -> None:
        """Cleanup handler invoked when the panel is being destroyed."""
        self._clear_job_event_tokens()

    def update_workflow_list(self) -> None:
        """Update the workflow combo box with recent workflows."""
        selected_path = self.service.last_workflow_path() if self.service else None
        if selected_path is None:
            current_data = self.workflow_combo.currentData()
            if current_data:
                try:
                    selected_path = Path(str(current_data))
                except (TypeError, ValueError):
                    selected_path = None

        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for label, user_data, translatable in self._workflow_combo_entries():
            display_text = translate_runtime_text(label, self.tr) if translatable else str(label)
            self.workflow_combo.addItem(display_text, user_data)

        if selected_path is not None:
            selected_value = str(selected_path)
            selected_index = self.workflow_combo.findData(selected_value)
            if selected_index == -1:
                self.workflow_combo.addItem(Path(selected_value).name, selected_value)
                selected_index = self.workflow_combo.findData(selected_value)
            if selected_index >= 0:
                self.workflow_combo.setCurrentIndex(selected_index)
        else:
            self.workflow_combo.setCurrentIndex(0)
        self.workflow_combo.blockSignals(False)

    def _workflow_combo_entries(self) -> list[tuple[str, str | None, bool]]:
        # Centralize combo item definitions so future helper entries can opt into
        # runtime retranslation without mixing display text and selection data.
        entries: list[tuple[str, str | None, bool]] = [
            (self._ts("(No workflow loaded)"), None, True)
        ]
        if self.service:
            for path in self.service.list_available_workflows():
                entries.append((path.name, str(path), False))
        return entries

    def on_workflow_combo_changed(self, index: int) -> None:
        """Load workflow when the user selects it from the list."""
        if self._loading_workflow_from_combo:
            return
        path_data = self.workflow_combo.itemData(index)
        if not path_data:
            return
        try:
            path = Path(str(path_data))
        except Exception:
            return
        if not path.exists():
            return
        self._loading_workflow_from_combo = True
        try:
            self.load_workflow_file(path)
        finally:
            self._loading_workflow_from_combo = False

    # --- Event Handlers ---

    def on_connection_changed(self, connected: bool) -> None:
        """Handle connection status change.

        Args:
            connected: True if connected, False otherwise
        """
        self.connection_status_changed.emit(connected)
        if connected:
            self.queue_button.setEnabled(self.current_workflow is not None)
            self.status_message.emit(str(self._ts("Connected to ComfyUI")))
            if not self._is_external_services_allowed():
                logger.debug("Skipping node-tab refresh because external services are disabled")
                return

            # If a workflow is loaded, refresh its node tabs with the newly cached object_info
            if self.current_workflow:
                import asyncio  # noqa: PLC0415

                from PySide6.QtCore import QTimer  # noqa: PLC0415

                async def refresh_nodes():
                    try:
                        await self.update_node_tabs(self.current_workflow)
                        logger.info("Updated node tabs with cached object_info schemas")
                    except Exception as e:
                        logger.error(f"Failed to update node tabs after connection: {e}")

                def schedule_refresh():
                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(refresh_nodes())  # noqa: RUF006
                    except RuntimeError:
                        logger.warning("No event loop for node refresh")

                QTimer.singleShot(100, schedule_refresh)  # Small delay to ensure cache is ready

        else:
            self.queue_button.setEnabled(False)
            self.cancel_button.setEnabled(False)
            self.status_message.emit(str(self._ts("Disconnected from ComfyUI")))

    def is_connected(self) -> bool:
        if self.connection_bar is None:
            return False
        return self.connection_bar.is_connected()

    def on_connect_clicked(self) -> None:
        """Handle connect/disconnect button click."""
        # This method is not actually used since connection_bar handles its own connections
        pass

    def on_load_workflow(self) -> None:
        """Handle load workflow button click."""
        from PySide6.QtWidgets import QFileDialog  # noqa: PLC0415

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            str(self._ts("Load ComfyUI Workflow")),
            str(self._workflows_root),
            str(self._ts("JSON Files (*.json);;All Files (*)")),
        )

        if file_path:
            self.load_workflow_file(Path(file_path))

    def load_workflow_file(self, path: Path) -> None:
        """Load a workflow file.

        Args:
            path: Path to workflow JSON file
        """
        import asyncio  # noqa: PLC0415

        async def load():
            try:
                workflow = await self.service.load_workflow(path)
                self.current_workflow = workflow

                # Update UI
                saved_path = self.service.last_workflow_path() or path
                index = self.workflow_combo.findData(str(saved_path))
                if index == -1:
                    self.workflow_combo.addItem(saved_path.name, str(saved_path))
                    index = self.workflow_combo.findData(str(saved_path))
                if index >= 0:
                    self.workflow_combo.setCurrentIndex(index)

                # Enable execution
                self.queue_button.setEnabled(True)
                self.save_workflow_button.setEnabled(True)

                self.status_message.emit(self._ts("Loaded workflow: {name}").format(name=path.name))

                # Update node tabs with workflow nodes
                await self.update_node_tabs(workflow)

            except Exception as e:
                QMessageBox.critical(
                    self,
                    self._ts("Error"),
                    self._ts("Failed to load workflow: {error}").format(error=e),
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

    def on_queue_workflow(self) -> None:
        """Handle queue workflow button click."""
        repeat_value = self.queue_repeat_spin.value() if self.queue_repeat_spin else 1
        self.queue_repeat_infinite = repeat_value == 0
        if self.queue_repeat_infinite:
            self.queue_repeat_remaining = None
        else:
            remaining = max(0, repeat_value - 1)
            self.queue_repeat_remaining = remaining if remaining > 0 else None
        self._queue_workflow_once()

    def _queue_workflow_once(self) -> bool:  # noqa: PLR0915
        """Queue the current workflow a single time."""
        if not self.current_workflow:
            QMessageBox.warning(
                self,
                self._ts("Warning"),
                self._ts("No workflow loaded"),
            )
            return False
        if not self._is_external_services_allowed():
            self._stop_repeat_queue()
            if self.queue_button:
                self.queue_button.setEnabled(True)
            if self.cancel_button:
                self.cancel_button.setEnabled(False)
            self.status_message.emit(
                self._ts("Enable 'Allow external AI services' in Preferences to queue workflows.")
            )
            logger.debug("Skipping workflow queue because external services are disabled")
            return False

        import asyncio  # noqa: PLC0415

        async def queue() -> None:
            try:
                image_parameters = None
                if self.node_tabs and hasattr(self.node_tabs, "get_image_parameters"):
                    image_parameters = self.node_tabs.get_image_parameters()
                    if image_parameters:
                        logger.info("Processing %s IMAGE parameters", len(image_parameters))

                output_targets = None
                if self.node_tabs and hasattr(self.node_tabs, "get_output_layer_targets"):
                    output_targets = self.node_tabs.get_output_layer_targets()
                    if output_targets:
                        logger.info("Configured %s SaveImage output targets", len(output_targets))

                model_output_targets = None
                if self.node_tabs and hasattr(self.node_tabs, "get_output_3d_layer_targets"):
                    model_output_targets = self.node_tabs.get_output_3d_layer_targets()
                    if model_output_targets:
                        logger.info(
                            "Configured %s Save 3D Model output targets",
                            len(model_output_targets),
                        )

                metadata_bindings = None
                if self.node_tabs and hasattr(self.node_tabs, "get_all_metadata_bindings"):
                    metadata_bindings = self.node_tabs.get_all_metadata_bindings()
                    if metadata_bindings:
                        logger.info("Processing %s metadata bindings", len(metadata_bindings))

                workflow_to_send = self._prepare_workflow_for_submission(self.current_workflow)
                job_id = await self.service.submit_workflow(
                    workflow_to_send,
                    image_parameters=image_parameters,
                    output_targets=output_targets,
                    model_output_targets=model_output_targets,
                    metadata_bindings=metadata_bindings,
                )

                # Save submission context for generation history
                # Note: image_parameters/output_targets may contain non-serializable
                # objects (e.g. LayerRef), so we exclude them from the snapshot.
                self._pending_contexts[job_id] = {
                    "workflow_name": self._current_workflow_label(),
                    "workflow_json": workflow_to_send.to_api_format(),
                    "workflow_model": self.current_workflow.to_json(),
                    "seed_modes": (self.node_tabs.get_seed_modes() if self.node_tabs else {}),
                    "created_at": datetime.now().astimezone().isoformat(),
                }

                self.current_job_id = job_id
                self._register_queue_job(job_id)
                self._set_queue_job_status(
                    job_id,
                    status="pending",
                    progress=0,
                    total=0,
                    message=self._ts("Waiting to start"),
                )

                self.queue_button.setEnabled(False)
                self.cancel_button.setEnabled(True)
                self.status_message.emit(
                    self._ts("Queued job: {job_id}...").format(job_id=job_id[:8])
                )

            except Exception as exc:  # pragma: no cover - defensive user feedback
                error_msg = str(exc)
                if "Invalid image file" in error_msg:
                    QMessageBox.warning(
                        self,
                        self._ts("Invalid Image Path"),
                        self._ts(
                            "The workflow contains LoadImage nodes with invalid file paths.\n\n"
                            "Please check that all image paths in your workflow are valid.\n"
                        )
                        + self._ts("Error: {error}").format(error=error_msg),
                    )
                else:
                    QMessageBox.critical(
                        self,
                        self._ts("Error"),
                        self._ts("Failed to queue workflow: {error}").format(error=error_msg),
                    )
                logger.error("Failed to queue workflow: %s", exc)
                self.current_job_id = None
                if self.queue_button:
                    self.queue_button.setEnabled(True)
                if self.cancel_button:
                    self.cancel_button.setEnabled(False)
            finally:
                self._queue_task = None

        def schedule_queue() -> None:
            try:
                loop = asyncio.get_event_loop()
                self._queue_task = loop.create_task(queue())
            except RuntimeError as exc:
                logger.warning("Failed to schedule queue task: %s", exc)
                QMessageBox.warning(
                    self,
                    self._ts("Error"),
                    self._ts("Failed to queue workflow. Please try again."),
                )

        QTimer.singleShot(0, schedule_queue)
        return True

    def on_cancel_job(self) -> None:
        """Handle cancel job button click."""
        if self.current_job_id:
            self._request_job_cancel(self.current_job_id)

    def _request_job_cancel(self, job_id: str | None) -> None:
        """Request cancellation for a job via the service."""
        if not job_id or not self.service:
            return

        self._stop_repeat_queue()

        import asyncio  # noqa: PLC0415

        async def _cancel() -> None:
            short_id = job_id[:8]
            try:
                result = await self.service.cancel(job_id)
                if result:
                    self.status_message.emit(f"Cancel requested for job {short_id}")
                else:
                    self.status_message.emit(
                        self._ts("Unable to cancel job {short_id}").format(short_id=short_id)
                    )
            except Exception as exc:
                logger.error("Failed to cancel job %s: %s", job_id, exc)
                self.status_message.emit(f"Failed to cancel job {short_id}")

        def schedule_cancel() -> None:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(_cancel())  # noqa: RUF006
            except RuntimeError as exc:
                logger.warning("No event loop available for cancel: %s", exc)
                self.status_message.emit(self._ts("Unable to cancel job - event loop unavailable"))

        QTimer.singleShot(0, schedule_cancel)

    def on_clear_finished(self) -> None:
        """Handle clear finished jobs button click."""
        self._on_queue_tab_clear_finished()

    def _prepare_workflow_for_submission(self, workflow: WorkflowModel) -> WorkflowModel:
        from .dynamic_prompt_submission import apply_dynamic_prompts  # noqa: PLC0415

        if not workflow:
            raise ValueError("workflow is not loaded")
        clone = workflow.clone() if hasattr(workflow, "clone") else workflow
        if self.node_tabs:
            self._apply_seed_modes(clone)
            apply_dynamic_prompts(self.node_tabs, clone)
        return clone

    def _apply_seed_modes(self, workflow: WorkflowModel) -> None:
        if not self.node_tabs:
            return
        seed_modes = getattr(self.node_tabs, "get_seed_modes", lambda: {})()
        if not seed_modes:
            return
        for node_id, modes in seed_modes.items():
            node = workflow.nodes.get(node_id)
            if not node or not isinstance(node.inputs, dict):
                continue
            for param_name, mode in modes.items():
                if mode in {"fix", "fixed"}:
                    continue
                value = node.inputs.get(param_name)
                adjusted = self._adjust_seed_value(value, mode)
                if adjusted is not None:
                    node.inputs[param_name] = adjusted

    def _adjust_seed_value(self, value: Any, mode: str) -> Any:
        try:
            seed_int = int(value)
        except (TypeError, ValueError):
            return value

        if mode == "increment":
            seed_int += 1
        elif mode == "decrement":
            seed_int -= 1
        elif mode in {"random", "randomize"}:
            digits = max(1, len(str(abs(seed_int))))
            lower = 10 ** (digits - 1)
            upper = (10**digits) - 1
            seed_int = random.randint(lower, upper)
        # fix leaves value unchanged
        return seed_int

    # --- Panel lifecycle methods ---

    def on_panel_shown(self) -> None:
        """Called when panel is shown."""
        self.update_workflow_list()

    def on_panel_hidden(self) -> None:
        """Called when panel is hidden."""
        pass

    def on_settings_changed(self, key: str, value: Any) -> None:
        """Called when settings change.

        Args:
            key: Setting key
            value: New value
        """
        if key in ["comfyui.host", "comfyui.port", "comfyui.use_ssl"]:
            # Don't auto-reconnect - user must manually reconnect
            self.status_message.emit("Connection settings updated. Please reconnect manually.")
            # If currently connected, suggest disconnecting first
            if hasattr(self, "connection_bar") and self.connection_bar.is_connected():
                self.status_message.emit("Disconnect and reconnect to apply new settings.")

    def reconnect(self) -> None:
        """Reconnect to ComfyUI server."""
        if self.connection_bar is None:
            return
        if self.connection_bar.is_connected():
            self.connection_bar.disconnect()
            QTimer.singleShot(0, self.connection_bar.connect)
            return
        self.connection_bar.connect()

    async def update_node_tabs(self, workflow: WorkflowModel) -> None:
        """Update node tabs with workflow nodes.

        Args:
            workflow: Loaded workflow
        """
        try:
            # Get node schemas and models from ComfyUI
            node_schemas: dict[str, Any] = {}
            models_cache: dict[str, Any] = {}
            if self.service and self._is_external_services_allowed():
                object_info = None
                try:
                    object_info = await self.service.get_object_info()
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch ComfyUI object info; loading workflow without schemas: %s",
                        exc,
                        exc_info=True,
                    )
                if object_info:
                    if "nodes" in object_info:
                        node_schemas = object_info["nodes"]
                    if "models" in object_info:
                        models_cache = object_info["models"]

            # Load workflow into node tabs with both schemas and models
            if not self.node_tabs:
                logger.debug("Node tabs widget unavailable; skipping workflow update")
                return

            self.node_tabs.load_workflow(workflow, node_schemas, models_cache)

            # Switch to workflow nodes tab
            self.content_tabs.setCurrentWidget(self.node_tabs)

            logger.info(f"Updated node tabs with {len(workflow.nodes)} nodes")

        except Exception as e:
            logger.error(f"Failed to update node tabs: {e}")

    def on_nodes_updated(self) -> None:
        """Handle node parameter updates."""
        # Update the current workflow with changes from node tabs
        if self.node_tabs and hasattr(self.node_tabs, "workflow") and self.node_tabs.workflow:
            self.current_workflow = self.node_tabs.get_updated_workflow()
            logger.debug("Workflow updated with node parameter changes")
