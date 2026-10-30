"""Node dock manager for ComfyUI workflow nodes.

Manages nodes as dockable widgets that can be shown in tabs or as separate panels,
matching the behavior of layer panels and other UI panels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import (
    TranslationBinder,
    translate_with_fallback,
    translated_literal,
)

try:
    from PySide6.QtCore import QPointer
except ImportError:
    # Fallback for older PySide6 versions
    QPointer = None

# Import safety utilities
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.drag_throttler import AnimationFreezer, DragThrottler

# Import MovingAwareDock for crash-safe docking
from loomstroke.ui.moving_aware_dock import MovingAwareDock, create_moving_aware_dock

if TYPE_CHECKING:
    from ..workflow_model import NodeConfig, WorkflowModel
    from .node_tabs import NodeParameterWidget

logger = logging.getLogger(__name__)


class NodeDockManager(QWidget):
    """Manages workflow nodes as dockable widgets.

    This class creates and manages MovingAwareDock instances for each workflow node,
    allowing them to be displayed in tabs or as separate docked/floating panels
    with crash-safe native window dragging.
    """

    nodes_updated = Signal()

    def __init__(self, parent: QWidget | None = None):
        """Initialize the node dock manager."""
        super().__init__(parent)
        self._translation_binder = TranslationBinder(self._translate_runtime)

        self.workflow = None
        self.node_docks: dict[str, MovingAwareDock] = {}  # node_id -> MovingAwareDock
        self.node_widgets: dict[str, NodeParameterWidget] = {}  # node_id -> widget
        self.main_window: QMainWindow | None = None

        # Global drag throttler and animation freezer
        self.drag_throttler: DragThrottler | None = None
        self.animation_freezer = AnimationFreezer()

        # Tab widget for compact display
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(False)
        self.tab_widget.setMovable(True)
        self.tab_widget.setDocumentMode(True)

        # Track which nodes are in tabs vs docked
        self.nodes_in_tabs: list[str] = []

        self.init_ui()
        self._setup_drag_throttler()

    def init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Control bar
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 2, 5, 2)

        # Button to show all nodes as docks
        self.expand_button = QPushButton()
        self._translation_binder.register(
            self.expand_button.setText,
            translated_literal(self._expand_label, "Expand All"),
        )
        self._translation_binder.register(
            self.expand_button.setToolTip,
            self._ts("Show all nodes as separate panels"),
        )
        self.expand_button.clicked.connect(self.expand_all_nodes)
        control_layout.addWidget(self.expand_button)

        # Button to collapse all to tabs
        self.collapse_button = QPushButton()
        self._translation_binder.register(
            self.collapse_button.setText,
            translated_literal(self._collapse_label, "Collapse All"),
        )
        self._translation_binder.register(
            self.collapse_button.setToolTip,
            self._ts("Show all nodes in tabs"),
        )
        self.collapse_button.clicked.connect(self.collapse_all_nodes)
        control_layout.addWidget(self.collapse_button)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        # Tab widget
        layout.addWidget(self.tab_widget)

        self.setLayout(layout)

    def _expand_label(self, text: str) -> str:
        return f"📤 {self._translate_runtime(text)}"

    def _collapse_label(self, text: str) -> str:
        return f"📥 {self._translate_runtime(text)}"

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("NodeDockManager", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()

    def changeEvent(self, event: QEvent) -> None:  # pragma: no cover - Qt passthrough
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def load_workflow(self, workflow: WorkflowModel, node_schemas: dict | None = None) -> None:
        """Load a workflow and create dock widgets for each node.

        Args:
            workflow: Workflow model
            node_schemas: Optional node schema definitions
        """
        # Clear existing
        self.clear_nodes()

        self.workflow = workflow
        self.main_window = self.find_main_window()

        if not self.main_window:
            logger.warning("No main window found, nodes will only be shown in tabs")

        # Create dock for each node with throttling
        for node_id, node in workflow.nodes.items():
            self.create_node_dock_with_throttling(node_id, node, node_schemas)

        logger.info(f"Created docks for {len(workflow.nodes)} nodes")

        # Initially show all in tabs
        self.collapse_all_nodes()

    def create_node_dock(
        self, node_id: str, node: NodeConfig, node_schemas: dict | None = None
    ) -> MovingAwareDock:
        """Create a MovingAwareDock widget for a node.

        Args:
            node_id: Node ID
            node: Node configuration
            node_schemas: Optional schema definitions

        Returns:
            Created MovingAwareDock widget
        """
        from .node_tabs import NodeParameterWidget  # noqa: PLC0415

        # Get schema for this node type
        node_schema = node_schemas.get(node.type) if node_schemas else None

        # Create parameter widget
        widget = NodeParameterWidget(node_id, node, node_schema)
        widget.parameter_changed.connect(self.on_node_parameter_changed)
        self.node_widgets[node_id] = widget

        # Create MovingAwareDock instead of regular QDockWidget
        title = f"{node.type} ({node_id})"
        dock = create_moving_aware_dock(title, widget, self.main_window)
        dock.setObjectName(f"comfyui_node_{node_id}")

        # Connect native move signals for debugging
        dock.native_move_started.connect(
            lambda: logger.debug(f"Node dock {node_id} started native move - updates frozen")
        )
        dock.native_move_ended.connect(
            lambda: logger.debug(f"Node dock {node_id} ended native move - updates resumed")
        )

        # Set up cleanup on destruction
        dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        # Use QPointer if available, otherwise use weak reference tracking
        if QPointer:
            QPointer(dock)

        def _on_destroyed(*_):
            try:
                self.node_docks.pop(node_id, None)
                self.node_widgets.pop(node_id, None)
                logger.debug(f"Cleaned up destroyed dock for node {node_id}")
            except Exception:
                logger.debug("dock destroy cleanup failed", exc_info=True)

        dock.destroyed.connect(_on_destroyed)

        # Store reference
        self.node_docks[node_id] = dock

        # Handle close event

        def handle_close(event):
            # When dock is closed, return to tab
            self.return_to_tab(node_id)
            event.ignore()  # Don't actually close, just hide

        dock.closeEvent = handle_close

        # Register with main window if available
        if self.main_window and hasattr(self.main_window, "register_panel"):
            panel_id = f"comfyui.node.{node_id}"
            self.main_window.register_panel(panel_id, title, dock, default_visible=False)

        logger.debug(f"Created MovingAwareDock for node {node_id}")
        return dock

    def expand_all_nodes(self) -> None:
        """Show all nodes as separate dock widgets."""
        if not self.main_window:
            return

        # Remove all from tabs
        while self.tab_widget.count() > 0:
            self.tab_widget.removeTab(0)
        self.nodes_in_tabs.clear()

        # Show all docks
        for node_id, dock in self.node_docks.items():
            if is_valid(self.main_window) and is_valid(dock):
                # Add to main window if not already there
                if not dock.parent():
                    self.main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

                # Make visible
                dock.show()

                # Tabify with other node docks for compact layout
                for other_id, other_dock in self.node_docks.items():
                    if other_id != node_id and is_valid(other_dock) and other_dock.isVisible():
                        if is_valid(self.main_window):
                            # tabify with safety checks
                            try:
                                self.main_window.tabifyDockWidget(other_dock, dock)
                                logger.debug(f"Tabified dock {node_id} with {other_id}")
                            except Exception as e:
                                logger.debug(f"Could not tabify docks: {e}")
                        break

        logger.info(f"Expanded {len(self.node_docks)} nodes as docks")

    def collapse_all_nodes(self) -> None:
        """Show all nodes in tabs."""
        # Hide all docks
        for dock in self.node_docks.values():
            if is_valid(dock):
                dock.hide()

        # Clear and repopulate tabs
        self.tab_widget.clear()
        self.nodes_in_tabs.clear()

        for node_id, _widget in self.node_widgets.items():
            node = self.workflow.nodes.get(node_id)
            if node:
                title = f"{node.type[:15]}..." if len(node.type) > 15 else node.type
                # Don't take widget from dock, create a proxy tab
                self.add_node_tab(node_id, title)

        logger.info(f"Collapsed {len(self.node_widgets)} nodes to tabs")

    def add_node_tab(self, node_id: str, title: str) -> None:
        """Add a node to the tab widget.

        Args:
            node_id: Node ID
            title: Tab title
        """
        # Create a proxy widget that shows the dock when clicked
        proxy = QWidget()
        layout = QVBoxLayout()

        from loomstroke.ui.widget_translations import bind_widget_text  # noqa: PLC0415

        info_label = bind_widget_text(
            QPushButton(),
            "📋 {title}\n\nClick to show as panel",
            domain="NodeDockManager",
            title=title,
        )
        info_label.setStyleSheet("""
            QPushButton {
                border: 1px solid #555;
                border-radius: 5px;
                padding: 20px;
                background: #2a2a2a;
            }
            QPushButton:hover {
                background: #3a3a3a;
            }
        """)
        info_label.clicked.connect(lambda: self.show_node_dock(node_id))

        layout.addWidget(info_label)
        layout.addStretch()
        proxy.setLayout(layout)

        self.tab_widget.addTab(proxy, title)
        self.nodes_in_tabs.append(node_id)

    def show_node_dock(self, node_id: str) -> None:
        """Show a specific node as a dock widget.

        Args:
            node_id: Node ID to show
        """
        if node_id not in self.node_docks:
            return

        dock = self.node_docks[node_id]

        if not is_valid(dock):
            logger.warning(f"Dock for node {node_id} is invalid")
            return

        if is_valid(self.main_window):
            # Add to main window if needed
            if not dock.parent():
                self.main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

            # Show and raise
            dock.show()
            dock.raise_()

            # Remove from tabs if present
            if node_id in self.nodes_in_tabs:
                # Find and remove the tab
                for _i in range(self.tab_widget.count()):
                    # We need to track which tab corresponds to which node
                    # For now, remove by position
                    pass

        logger.debug(f"Showed dock for node {node_id}")

    def return_to_tab(self, node_id: str) -> None:
        """Return a node dock to tab display.

        Args:
            node_id: Node ID to return to tab
        """
        if node_id not in self.node_docks:
            return

        dock = self.node_docks[node_id]
        dock.hide()

        # Add back to tabs if not there
        if node_id not in self.nodes_in_tabs:
            node = self.workflow.nodes.get(node_id)
            if node:
                title = f"{node.type[:15]}..." if len(node.type) > 15 else node.type
                self.add_node_tab(node_id, title)

        logger.debug(f"Returned node {node_id} to tab")

    def clear_nodes(self) -> None:
        """Clear all nodes and docks."""
        # Clear tabs
        self.tab_widget.clear()
        self.nodes_in_tabs.clear()

        # Close and delete docks with safety
        for dock in self.node_docks.values():
            if is_valid(dock):
                try:
                    dock.close()
                    dock.deleteLater()
                except Exception as e:
                    logger.debug(f"Error cleaning up dock: {e}")

        self.node_docks.clear()
        self.node_widgets.clear()

        logger.debug("Cleared all nodes and docks")

    def on_node_parameter_changed(self, node_id: str, param_name: str, value) -> None:
        """Handle node parameter change.

        Args:
            node_id: Node ID
            param_name: Parameter name
            value: New value
        """
        logger.debug(f"Node {node_id} parameter {param_name} changed to {value}")
        self.nodes_updated.emit()

    def find_main_window(self) -> QMainWindow | None:
        """Find the main window.

        Returns:
            Main window or None
        """
        # Walk up parent hierarchy
        parent = self.parent()
        while parent:
            if isinstance(parent, QMainWindow):
                return parent
            parent = parent.parent()

        # Check application windows
        app = QApplication.instance()
        if app:
            for window in app.topLevelWidgets():
                if isinstance(window, QMainWindow):
                    return window

        return None

    def _setup_drag_throttler(self) -> None:
        """Set up the global drag throttler."""
        app = QApplication.instance()
        if not app:
            logger.warning("No QApplication instance, drag throttler not installed")
            return

        # Create throttler with overlay whitelist
        overlay_whitelist = set()

        # Add any overlay widgets to whitelist here
        # For now, empty set - will be populated when overlays are created

        self.drag_throttler = DragThrottler(overlay_whitelist)
        app.installEventFilter(self.drag_throttler)

        logger.debug("Global drag throttler installed")

    def _on_dock_moving_changed(self, active: bool) -> None:
        """Handle dock movement state changes.

        Args:
            active: True if dock is being moved, False otherwise
        """
        if not self.drag_throttler:
            return

        # Activate/deactivate global paint blocking
        self.drag_throttler.set_active(active)

        # Freeze/resume animations
        if active:
            self.animation_freezer.freeze_animations(self.main_window)
        else:
            self.animation_freezer.resume_animations()

        logger.debug(f"Drag throttling {'activated' if active else 'deactivated'}")

    def create_node_dock_with_throttling(
        self, node_id: str, node: NodeConfig, node_schemas: dict | None = None
    ) -> MovingAwareDock:
        """Create a MovingAwareDock with drag throttling connected.

        This method wraps create_node_dock and connects the drag throttler.

        Args:
            node_id: Node ID
            node: Node configuration
            node_schemas: Optional node schema definitions

        Returns:
            Created MovingAwareDock instance
        """
        dock = self.create_node_dock(node_id, node, node_schemas)

        # Connect to drag throttler
        if dock and self.drag_throttler:
            dock.nativeMoveActiveChanged.connect(self._on_dock_moving_changed)
            logger.debug(f"Connected drag throttler to dock {node_id}")

        return dock

    def get_updated_workflow(self) -> WorkflowModel | None:
        """Get the workflow with updated parameter values.

        Returns:
            Updated workflow or None
        """
        return self.workflow
