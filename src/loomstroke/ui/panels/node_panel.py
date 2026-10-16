"""Node panel for ComfyUI node parameter management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.panels.base_panel import BasePanel

logger = logging.getLogger(__name__)


class NodeParameterWidget(QWidget):
    """Widget for editing a single node parameter."""

    value_changed = Signal(str, object)  # param_name, value

    def __init__(
        self, param_name: str, param_info: dict[str, Any], parent: QWidget | None = None
    ) -> None:
        """Initialize parameter widget.

        Args:
            param_name: Parameter name
            param_info: Parameter metadata (type, default, range, etc.)
            parent: Parent widget
        """
        super().__init__(parent)

        self.param_name = param_name
        self.param_info = param_info
        self.param_type = param_info.get("type", "string")

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup UI based on parameter type."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Label
        label = QLabel(self.param_name + ":")
        label.setMinimumWidth(100)
        layout.addWidget(label)

        # Create appropriate widget based on type
        if self.param_type == "integer":
            self._create_integer_widget(layout)
        elif self.param_type == "float":
            self._create_float_widget(layout)
        elif self.param_type == "boolean":
            self._create_boolean_widget(layout)
        elif self.param_type == "select":
            self._create_select_widget(layout)
        elif self.param_type == "string":
            self._create_string_widget(layout)
        elif self.param_type == "slider":
            self._create_slider_widget(layout)
        else:
            self._create_string_widget(layout)  # Fallback

        layout.addStretch()

    def _create_integer_widget(self, layout: QHBoxLayout) -> None:
        """Create integer input widget."""
        spin = QSpinBox()
        spin.setRange(self.param_info.get("min", -999999), self.param_info.get("max", 999999))
        spin.setValue(self.param_info.get("default", 0))
        spin.valueChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))
        layout.addWidget(spin)

    def _create_float_widget(self, layout: QHBoxLayout) -> None:
        """Create float input widget."""
        spin = QDoubleSpinBox()
        spin.setRange(self.param_info.get("min", -999999.0), self.param_info.get("max", 999999.0))
        spin.setSingleStep(self.param_info.get("step", 0.1))
        spin.setValue(self.param_info.get("default", 0.0))
        spin.valueChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))
        layout.addWidget(spin)

    def _create_boolean_widget(self, layout: QHBoxLayout) -> None:
        """Create boolean checkbox widget."""
        check = QCheckBox()
        check.setChecked(self.param_info.get("default", False))
        check.toggled.connect(lambda v: self.value_changed.emit(self.param_name, v))
        layout.addWidget(check)

    def _create_select_widget(self, layout: QHBoxLayout) -> None:
        """Create selection combo box widget."""
        combo = QComboBox()
        options_raw = self.param_info.get("options", [])
        options = [str(option) for option in options_raw]
        combo.addItems(options)

        default = self.param_info.get("default")
        if isinstance(default, str) and default in options:
            combo.setCurrentText(default)

        combo.currentTextChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))
        layout.addWidget(combo)

    def _create_string_widget(self, layout: QHBoxLayout) -> None:
        """Create string input widget."""
        line_edit = QLineEdit()
        line_edit.setText(str(self.param_info.get("default", "")))
        line_edit.textChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))
        layout.addWidget(line_edit)

    def _create_slider_widget(self, layout: QHBoxLayout) -> None:
        """Create slider widget."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(self.param_info.get("min", 0)), int(self.param_info.get("max", 100)))
        slider.setValue(int(self.param_info.get("default", 50)))

        # Value label
        value_label = QLabel(str(slider.value()))
        slider.valueChanged.connect(lambda v: value_label.setText(str(v)))
        slider.valueChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))

        layout.addWidget(slider)
        layout.addWidget(value_label)


class NodePanel(BasePanel):
    """Panel for managing ComfyUI node parameters.

    Displays and edits parameters for workflow nodes.
    Complies with Master.md 400-line limit.
    """

    # Signals
    node_selected = Signal(dict)  # Node data
    parameters_changed = Signal(dict)  # Node ID -> parameters
    node_added = Signal(str)  # Node type
    node_removed = Signal(str)  # Node ID

    def __init__(self, parent: Any | None = None) -> None:
        """Initialize node panel.

        Args:
            parent: Parent widget
        """
        # BasePanel requires panel_id and title
        super().__init__("comfyui.nodes", "Nodes", parent)
        self._node_info_template = self._ts("Node: {title}\nType: {type}")
        self._unknown_node_type = self._ts("Unknown")
        self._tree_header_sources = [self._ts("Node"), self._ts("Type")]
        self._empty_params_text = self._ts("Select a node to edit parameters")
        self._translation_binder.register(self._set_node_info_template, self._node_info_template)
        self._translation_binder.register(self._set_unknown_node_type, self._unknown_node_type)
        self._translation_binder.register(self._set_empty_params_text, self._empty_params_text)

        # State
        self.nodes: dict[str, dict[str, Any]] = {}
        self.current_node_id: str | None = None
        self.node_parameters: dict[str, dict[str, Any]] = {}

        # Node type definitions (would be loaded from ComfyUI)
        self.node_types = self._get_default_node_types()

        # UI elements
        self.node_tree: QTreeWidget | None = None
        self.param_stack: QStackedWidget | None = None
        self.param_widgets: dict[str, list[NodeParameterWidget]] = {}

    def setup_panel_ui(self) -> None:
        """Required by BasePanel - setup UI components."""
        layout = self.main_layout  # Use the layout from BasePanel
        layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()

        title_text = self._ts("Node Parameters")
        title = QLabel(title_text)
        self._translation_binder.register(title.setText, title_text)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Add node button
        add_text = self._ts("Add Node")
        add_btn = QPushButton(add_text)
        self._translation_binder.register(add_btn.setText, add_text)
        add_btn.clicked.connect(self._on_add_node)
        header_layout.addWidget(add_btn)

        layout.addLayout(header_layout)

        # Main content with tree and parameters
        content_layout = QHBoxLayout()

        # Node tree
        tree_group = self._create_tree_group()
        content_layout.addWidget(tree_group, 1)

        # Parameter editor
        param_group = self._create_param_group()
        content_layout.addWidget(param_group, 2)

        layout.addLayout(content_layout)

    def _create_tree_group(self) -> QGroupBox:
        """Create node tree group."""
        group_title = self._ts("Workflow Nodes")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Tree widget
        self.node_tree = QTreeWidget()
        self.node_tree.setHeaderLabels(self._tree_header_sources)
        self.node_tree.itemClicked.connect(self._on_node_selected)
        layout.addWidget(self.node_tree)

        # Tree controls
        controls = QHBoxLayout()

        remove_text = self._ts("Remove")
        remove_btn = QPushButton(remove_text)
        self._translation_binder.register(remove_btn.setText, remove_text)
        remove_btn.clicked.connect(self._on_remove_node)
        controls.addWidget(remove_btn)

        duplicate_text = self._ts("Duplicate")
        duplicate_btn = QPushButton(duplicate_text)
        self._translation_binder.register(duplicate_btn.setText, duplicate_text)
        duplicate_btn.clicked.connect(self._on_duplicate_node)
        controls.addWidget(duplicate_btn)

        layout.addLayout(controls)

        return group

    def _create_param_group(self) -> QGroupBox:
        """Create parameter editor group."""
        group_title = self._ts("Parameters")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Stacked widget for different node types
        self.param_stack = QStackedWidget()
        layout.addWidget(self.param_stack)

        # Add empty widget as default
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_label = QLabel(self._empty_params_text)
        self._translation_binder.register(empty_label.setText, self._empty_params_text)
        empty_layout.addWidget(empty_label)
        empty_layout.addStretch()
        self.param_stack.addWidget(empty_widget)

        # Apply button
        apply_text = self._ts("Apply Changes")
        apply_btn = QPushButton(apply_text)
        self._translation_binder.register(apply_btn.setText, apply_text)
        apply_btn.clicked.connect(self._on_apply_changes)
        layout.addWidget(apply_btn)

        return group

    def _set_node_info_template(self, text: str) -> None:
        self._node_info_template = text

    def _set_unknown_node_type(self, text: str) -> None:
        self._unknown_node_type = text

    def _set_empty_params_text(self, text: str) -> None:
        self._empty_params_text = text

    def on_language_changed(self, _language: str) -> None:
        if self.node_tree:
            self.node_tree.setHeaderLabels([self.tr(text) for text in self._tree_header_sources])
        self._refresh_node_info_labels()

    def _refresh_node_info_labels(self) -> None:
        if not self.param_stack:
            return
        for index in range(self.param_stack.count()):
            widget = self.param_stack.widget(index)
            if widget is None:
                continue
            node_id = widget.property("node_id")
            if not isinstance(node_id, str) or node_id not in self.nodes:
                continue
            info_label = widget.findChild(QLabel, "nodeInfoLabel")
            if info_label is None:
                continue
            node_data = self.nodes[node_id]
            title = node_data.get("title", node_id)
            node_type = node_data.get("class_type", self._unknown_node_type)
            info_label.setText(self._node_info_template.format(title=title, type=node_type))

    def _get_default_node_types(self) -> dict[str, dict[str, Any]]:
        """Get default node type definitions."""
        return {
            "KSampler": {
                "parameters": {
                    "seed": {"type": "integer", "default": -1, "min": -1, "max": 999999999},
                    "steps": {"type": "integer", "default": 20, "min": 1, "max": 10000},
                    "cfg": {"type": "float", "default": 7.0, "min": 0.0, "max": 100.0},
                    "sampler_name": {
                        "type": "select",
                        "options": ["euler", "euler_a", "ddim", "dpm_2"],
                        "default": "euler",
                    },
                    "scheduler": {
                        "type": "select",
                        "options": ["normal", "karras", "exponential", "simple"],
                        "default": "normal",
                    },
                    "denoise": {"type": "float", "default": 1.0, "min": 0.0, "max": 1.0},
                }
            },
            "CLIPTextEncode": {"parameters": {"text": {"type": "string", "default": ""}}},
            "VAEDecode": {"parameters": {}},
            "CheckpointLoaderSimple": {
                "parameters": {"ckpt_name": {"type": "select", "options": [], "default": ""}}
            },
            "EmptyLatentImage": {
                "parameters": {
                    "width": {"type": "integer", "default": 512, "min": 64, "max": 8192},
                    "height": {"type": "integer", "default": 512, "min": 64, "max": 8192},
                    "batch_size": {"type": "integer", "default": 1, "min": 1, "max": 64},
                }
            },
        }

    # Node Operations

    def load_workflow(self, workflow: dict[str, Any]) -> None:
        """Load workflow nodes.

        Args:
            workflow: Workflow data with nodes
        """
        self.nodes = workflow.get("nodes", {})
        self.current_node_id = None

        # Clear tree
        if self.node_tree:
            self.node_tree.clear()

            # Add nodes to tree
            for node_id, node_data in self.nodes.items():
                self._add_node_to_tree(node_id, node_data)

        logger.info(f"Loaded {len(self.nodes)} nodes")

    def _add_node_to_tree(self, node_id: str, node_data: dict[str, Any]) -> None:
        """Add node to tree widget."""
        node_type = node_data.get("class_type", self._unknown_node_type)
        title = node_data.get("title", node_id)

        item = QTreeWidgetItem([title, node_type])
        item.setData(0, Qt.ItemDataRole.UserRole, node_id)
        if self.node_tree:
            self.node_tree.addTopLevelItem(item)

    def _on_node_selected(self, item: QTreeWidgetItem, column: int) -> None:
        """Handle node selection in tree."""
        node_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not node_id or node_id not in self.nodes:
            return

        self.current_node_id = node_id
        node_data = self.nodes[node_id]

        # Create parameter widgets if not exists
        if node_id not in self.param_widgets:
            self._create_param_widgets(node_id, node_data)

        # Show parameter widgets
        if node_id in self.param_widgets and self.param_stack:
            # Find or create widget page
            for i in range(self.param_stack.count()):
                widget = self.param_stack.widget(i)
                if widget and widget.property("node_id") == node_id:
                    self.param_stack.setCurrentIndex(i)
                    break

        # Emit signal
        self.node_selected.emit(node_data)

    def _create_param_widgets(self, node_id: str, node_data: dict[str, Any]) -> None:
        """Create parameter widgets for node."""
        node_type = node_data.get("class_type", "")

        if node_type not in self.node_types:
            logger.warning(f"Unknown node type: {node_type}")
            return

        # Create container widget
        container = QWidget()
        container.setProperty("node_id", node_id)
        layout = QVBoxLayout(container)

        # Node info
        info_label = QLabel(
            self._node_info_template.format(
                title=node_data.get("title", node_id),
                type=node_type,
            )
        )
        info_label.setObjectName("nodeInfoLabel")
        info_label.setStyleSheet("font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(info_label)

        # Create parameter widgets
        param_widgets = []
        params = self.node_types[node_type].get("parameters", {})

        for param_name, param_info in params.items():
            widget = NodeParameterWidget(param_name, param_info)
            widget.value_changed.connect(self._on_param_changed)
            layout.addWidget(widget)
            param_widgets.append(widget)

        layout.addStretch()

        # Store widgets
        self.param_widgets[node_id] = param_widgets

        # Add to stack
        if self.param_stack:
            self.param_stack.addWidget(container)

    def _on_param_changed(self, param_name: str, value: Any) -> None:
        """Handle parameter value change."""
        if not self.current_node_id:
            return

        # Update parameter storage
        if self.current_node_id not in self.node_parameters:
            self.node_parameters[self.current_node_id] = {}

        self.node_parameters[self.current_node_id][param_name] = value

        logger.debug(f"Parameter changed: {self.current_node_id}.{param_name} = {value}")

    def _on_apply_changes(self) -> None:
        """Apply parameter changes."""
        if self.node_parameters:
            # Emit signal with all changes
            self.parameters_changed.emit(self.node_parameters.copy())

            # Publish to event bus
            if self._event_bus:
                self._event_bus.emit("node.parameters_changed", self.node_parameters)

            logger.info(f"Applied changes to {len(self.node_parameters)} nodes")

    def _on_add_node(self) -> None:
        """Add new node."""
        # Show node type selector
        # For now, just add a KSampler
        self.node_added.emit("KSampler")

    def _on_remove_node(self) -> None:
        """Remove selected node."""
        if not self.node_tree:
            return

        current = self.node_tree.currentItem()
        if current:
            node_id = current.data(0, Qt.ItemDataRole.UserRole)
            if node_id:
                self.node_removed.emit(node_id)
                # Remove from tree
                index = self.node_tree.indexOfTopLevelItem(current)
                self.node_tree.takeTopLevelItem(index)

    def _on_duplicate_node(self) -> None:
        """Duplicate selected node."""
        # Implementation would duplicate the selected node
        logger.info("Duplicate node not yet implemented")


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from loomstroke.ui.typing import Signal1, Signal2

    NodeParameterWidget.value_changed = cast(  # type: ignore[assignment]
        Signal2[str, object],
        NodeParameterWidget.value_changed,
    )
    NodePanel.node_selected = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        NodePanel.node_selected,
    )
    NodePanel.parameters_changed = cast(  # type: ignore[assignment]
        Signal1[dict[str, dict[str, Any]]],
        NodePanel.parameters_changed,
    )
    NodePanel.node_added = cast(  # type: ignore[assignment]
        Signal1[str],
        NodePanel.node_added,
    )
    NodePanel.node_removed = cast(  # type: ignore[assignment]
        Signal1[str],
        NodePanel.node_removed,
    )
