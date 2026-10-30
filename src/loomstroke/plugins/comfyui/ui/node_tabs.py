"""Dynamic node tabs for ComfyUI workflows.

This module creates dockable tabs for each node in a loaded workflow.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.widget_translations import bind_widget_text
from loomstroke.ui.widgets.spinbox_sizing import fit_spinbox_to_values

from ..object_info_spec import COMBO_HEAD, combo_choices, split_input_spec
from . import node_parameter_selectors as _node_parameter_selectors
from .node_parameter_layer_widgets import ImageParameterWidget, OutputLayerWidget
from .node_parameter_selectors import (
    NodeChoiceSelectorMixin,
    NodePromptSelectorMixin,
)

# Use only the safe implementation - no drag continuation
from .node_tab_categories import (
    CategoryFilter,
    CategoryPage,
    CategoryTabManager,
    NodeMetadata,
)
from .node_tab_user_filter_dialogs import (
    ManageUserFiltersDialog,
)
from .node_tab_user_filter_dialogs import UserFilterEditDialog as _UserFilterEditDialog
from .node_tab_user_filters import UserFilterConfig, UserFilterManager
from .safe_detach_tabs import SafeDetachableTabWidget

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI

    from ..workflow_model import NodeConfig, WorkflowModel

from loomstroke.host_api import LayerRef
from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.comfyui.metadata_registry import (  # noqa: F821
    MetadataBinding,
    ResolveContext,
    get_metadata_registry,
)
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)
PromptSnippetComboBox = _node_parameter_selectors.PromptSnippetComboBox
UserFilterEditDialog = _UserFilterEditDialog


def _node_tabs_tr(text: str) -> str:
    return translate_with_fallback("NodeTabsWidget", text)


def _is_save_3d_model_node_type(node_type: str) -> bool:
    normalized = "".join(ch for ch in str(node_type or "").lower() if ch.isalnum())
    if normalized in {
        "save3d",
        "save3dmodel",
        "save3dmesh",
        "saveglb",
        "savegltf",
        "saveobj",
        "savemesh",
        "exportglb",
        "exportgltf",
        "exportobj",
    }:
        return True
    if not normalized.startswith(("save", "export")):
        return False
    return any(token in normalized for token in ("3d", "mesh", "glb", "gltf", "obj"))


class NodeParameterWidget(NodePromptSelectorMixin, NodeChoiceSelectorMixin, QWidget):
    """Widget for editing node parameters."""

    parameter_changed = Signal(str, str, object)  # node_id, param_name, value

    def __init__(
        self,
        node_id: str,
        node: NodeConfig,
        schema: dict[str, Any] | None = None,
        models_cache: dict[str, Any] | None = None,
        host_api: HostAPI | None = None,
        parent: QWidget | None = None,
    ):
        """Initialize node parameter widget.

        Args:
            node_id: Node ID
            node: Node configuration
            schema: Node schema (optional)
            models_cache: Cached models from server (optional)
            parent: Parent widget
        """
        super().__init__(parent)

        self.node_id = node_id
        self.node = node
        self.schema: dict[str, Any] | None = schema
        self.models_cache: dict[str, Any] = models_cache or {}
        self.host_api: HostAPI | None = host_api
        self.param_widgets: dict[str, QWidget] = {}  # param_name -> widget
        self._seed_modes: dict[str, str] = {}
        self.output_layer_widget: OutputLayerWidget | None = None
        self.output_3d_layer_widget: OutputLayerWidget | None = None
        self._prompt_selector_combos: dict[str, QComboBox] = {}
        self._prompt_text_widgets: dict[str, QWidget] = {}
        self._updating_prompt_selection = False
        self._header_label: QLabel | None = None
        self._optional_label: QLabel | None = None
        self._metadata_bindings: dict[str, MetadataBinding] = {}

        self.init_ui()

    def set_host_api(self, host_api: HostAPI | None) -> None:
        """Update host API for dependent widgets."""
        self.host_api = host_api
        for widget in self.param_widgets.values():
            if isinstance(widget, ImageParameterWidget):
                widget.set_host_api(host_api)
        if self.output_layer_widget:
            self.output_layer_widget.set_host_api(host_api)
        if self.output_3d_layer_widget:
            self.output_3d_layer_widget.set_host_api(host_api)
        self._refresh_prompt_selector_options()

    def refresh_layer_widgets(self) -> None:
        """Refresh image parameter widgets with current layers."""
        for widget in self.param_widgets.values():
            if isinstance(widget, ImageParameterWidget):
                widget.refresh_layers()
        if self.output_layer_widget:
            self.output_layer_widget.refresh_layers()
        if self.output_3d_layer_widget:
            self.output_3d_layer_widget.refresh_layers()

    def get_seed_modes(self) -> dict[str, str]:
        """Return per-parameter seed mode selections."""
        return dict(self._seed_modes)

    def get_seed_modes(self) -> dict[str, str]:  # noqa: F811
        """Return per-parameter seed mode selections."""
        return dict(self._seed_modes)

    @staticmethod
    def _is_seed_parameter(param_name: str) -> bool:
        return "seed" in param_name.lower()

    @staticmethod
    def _resolve_actual_widget(widget: QWidget) -> QWidget:
        actual = getattr(widget, "_prompt_text_widget", None)
        if isinstance(actual, QWidget):
            return actual
        actual = getattr(widget, "_seed_value_widget", None)
        if isinstance(actual, QWidget):
            return actual
        return widget

    def _create_seed_value_input(
        self,
        param_name: str,
        value: Any,
        *,
        placeholder: str | None = None,
        tooltip: str | None = None,
        placeholder_source: str | None = None,
        placeholder_kwargs: dict[str, Any] | None = None,
        tooltip_source: str | None = None,
        tooltip_kwargs: dict[str, Any] | None = None,
    ) -> QLineEdit:
        widget = QLineEdit()
        if value is not None:
            if isinstance(value, (int, float)):
                widget.setText(str(int(value)))
            else:
                widget.setText(str(value))

        # Stringify large ints in kwargs to avoid Qt int64 overflow in setProperty
        def _safe_kwargs(kw: dict[str, Any] | None) -> dict[str, Any]:
            if not kw:
                return {}
            return {k: str(v) if isinstance(v, int) and v > 2**63 - 1 else v for k, v in kw.items()}

        if placeholder_source:
            safe_ph = _safe_kwargs(placeholder_kwargs)
            widget.setProperty("_translation_placeholder_source", placeholder_source)
            widget.setProperty("_translation_placeholder_kwargs", safe_ph)
            widget.setPlaceholderText(_node_tabs_tr(placeholder_source).format(**safe_ph))
        elif placeholder:
            widget.setPlaceholderText(placeholder)
        if tooltip_source:
            safe_tt = _safe_kwargs(tooltip_kwargs)
            widget.setProperty("_translation_tooltip_source", tooltip_source)
            widget.setProperty("_translation_tooltip_kwargs", safe_tt)
            widget.setToolTip(_node_tabs_tr(tooltip_source).format(**safe_tt))
        elif tooltip:
            widget.setToolTip(tooltip)

        def on_seed_changed(text: str, name: str = param_name) -> None:
            try:
                parsed = int(text) if text else 0
            except ValueError:
                return
            self.on_param_changed(name, parsed)

        widget.textChanged.connect(on_seed_changed)
        return widget

    def _set_runtime_tooltip(self, widget: QWidget, source_text: str, **kwargs: Any) -> None:
        widget.setProperty("_translation_tooltip_source", source_text)
        widget.setProperty("_translation_tooltip_kwargs", kwargs)
        widget.setToolTip(_node_tabs_tr(source_text).format(**kwargs))

    def _refresh_runtime_widget_text(self, widget: QWidget) -> None:
        placeholder_source = widget.property("_translation_placeholder_source")
        placeholder_kwargs = widget.property("_translation_placeholder_kwargs")
        if isinstance(placeholder_source, str) and hasattr(widget, "setPlaceholderText"):
            kwargs = placeholder_kwargs if isinstance(placeholder_kwargs, dict) else {}
            widget.setPlaceholderText(_node_tabs_tr(placeholder_source).format(**kwargs))

        tooltip_source = widget.property("_translation_tooltip_source")
        tooltip_kwargs = widget.property("_translation_tooltip_kwargs")
        if isinstance(tooltip_source, str):
            kwargs = tooltip_kwargs if isinstance(tooltip_kwargs, dict) else {}
            widget.setToolTip(_node_tabs_tr(tooltip_source).format(**kwargs))

    def _wrap_seed_mode_widget(self, widget: QWidget, param_name: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(widget)

        mode_combo = QComboBox()
        mode_items = [
            ("fixed(seed値固定)", "fix"),
            ("increment(現在のseed値に+1)", "increment"),
            ("decrement(現在のseed値に-1)", "decrement"),
            ("randomize(seed値の桁数と同じ桁数でランダムな数値を生成)", "random"),
        ]
        valid_modes = {mode for _, mode in mode_items}
        for label, mode in mode_items:
            mode_combo.addItem(label, mode)

        current_mode = self._seed_modes.get(param_name, "fix")
        if current_mode not in valid_modes:
            current_mode = "fix"
        for index in range(mode_combo.count()):
            if mode_combo.itemData(index) == current_mode:
                mode_combo.setCurrentIndex(index)
                break

        def on_mode_changed(index: int) -> None:
            selected = mode_combo.itemData(index)
            self._seed_modes[param_name] = selected if isinstance(selected, str) else "fix"

        mode_combo.currentIndexChanged.connect(on_mode_changed)
        layout.addWidget(mode_combo)
        self._seed_modes[param_name] = current_mode
        container._seed_value_widget = widget
        return container

    def init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Node header
        self._header_label = QLabel(_node_tabs_tr("Node: {node}").format(node=self.node.type))
        self._header_label.setStyleSheet("font-weight: bold; color: #4a9eff; margin-bottom: 10px;")
        layout.addWidget(self._header_label)

        # Scrollable parameter area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        param_widget = QWidget()
        param_layout = QFormLayout()
        param_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        param_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Add parameters based on schema or current values
        if self.schema:
            self.add_schema_inputs(param_layout, self.schema)
        else:
            self.add_basic_inputs(param_layout)

        if self.node.type == "SaveImage":
            self._add_output_layer_selector(param_layout)
        elif _is_save_3d_model_node_type(self.node.type):
            self._add_output_3d_layer_selector(param_layout)

        param_widget.setLayout(param_layout)
        scroll.setWidget(param_widget)
        layout.addWidget(scroll)

        self.setLayout(layout)

    def add_schema_inputs(self, layout: QFormLayout, schema: dict[str, Any]) -> None:
        """Add input widgets based on node schema.

        Args:
            layout: Form layout to add to
            schema: Input schema definition
        """
        # Build a list of widget names in order (for mapping widgets_values)
        widget_order = []

        # Handle required inputs
        if "input" in schema and "required" in schema["input"]:
            logger.debug(f"Processing required inputs for {self.node.type}")
            for param_name, param_spec in schema["input"]["required"].items():
                logger.debug(f"Creating widget for {param_name}: spec={param_spec}")
                widget = self.create_input_widget(param_name, param_spec)
                if widget:
                    layout.addRow(param_name + ":", widget)
                    actual_widget = self._resolve_actual_widget(widget)
                    self.param_widgets[param_name] = actual_widget
                    # Track widget order for non-link inputs (connection types)
                    # Fixed: Include Format 1b parameters (choices + options dict) with len >= 1
                    if (
                        isinstance(param_spec, list)
                        and len(param_spec) >= 1
                        and isinstance(param_spec[0], list)
                    ):
                        # This is a choices parameter (Format 1a or 1b), include it in widget order
                        widget_order.append(param_name)
                    elif not (
                        isinstance(param_spec, list)
                        and len(param_spec) >= 1
                        and param_spec[0]
                        in [
                            "LATENT",
                            "CONDITIONING",
                            "MODEL",
                            "VAE",
                            "CLIP",
                            "CONTROL_NET",
                            "IMAGE",
                            "MASK",
                        ]
                    ):
                        # Only add to widget order if it's NOT a connection type
                        widget_order.append(param_name)
                    elif (
                        isinstance(param_spec, list)
                        and param_spec[0] == "IMAGE"
                        and (
                            param_name == "image"
                            or param_name.endswith("_image")
                            or param_name.startswith("image_")
                        )
                    ):
                        # Special case: IMAGE parameters that are actually
                        # file inputs should have widgets
                        widget_order.append(param_name)

        # Handle optional inputs
        if "input" in schema and "optional" in schema["input"]:
            self._optional_label = QLabel(f"<i>{_node_tabs_tr('Optional:')}</i>")
            layout.addRow(self._optional_label)
            logger.debug(f"Processing optional inputs for {self.node.type}")
            for param_name, param_spec in schema["input"]["optional"].items():
                logger.debug(f"Creating optional widget for {param_name}: spec={param_spec}")
                widget = self.create_input_widget(param_name, param_spec)
                if widget:
                    layout.addRow(param_name + ":", widget)
                    actual_widget = self._resolve_actual_widget(widget)
                    self.param_widgets[param_name] = actual_widget
                    # Track widget order for non-link inputs (connection types)
                    # Fixed: Include Format 1b parameters (choices + options dict) with len >= 1
                    if (
                        isinstance(param_spec, list)
                        and len(param_spec) >= 1
                        and isinstance(param_spec[0], list)
                    ):
                        # This is a choices parameter (Format 1a or 1b), include it in widget order
                        widget_order.append(param_name)
                    elif not (
                        isinstance(param_spec, list)
                        and len(param_spec) >= 1
                        and param_spec[0]
                        in [
                            "LATENT",
                            "CONDITIONING",
                            "MODEL",
                            "VAE",
                            "CLIP",
                            "CONTROL_NET",
                            "IMAGE",
                            "MASK",
                        ]
                    ):
                        # Only add to widget order if it's NOT a connection type
                        widget_order.append(param_name)
                    elif (
                        isinstance(param_spec, list)
                        and param_spec[0] == "IMAGE"
                        and (
                            param_name == "image"
                            or param_name.endswith("_image")
                            or param_name.startswith("image_")
                        )
                    ):
                        # Special case: IMAGE parameters that are actually
                        # file inputs should have widgets
                        widget_order.append(param_name)

        # Map widgets_values to parameters
        if self.node.widgets_values and widget_order:
            for idx, value in enumerate(self.node.widgets_values):
                if idx < len(widget_order):
                    param_name = widget_order[idx]
                    # Normalised links and edited inputs take precedence over
                    # the original UI widget snapshot.
                    if param_name in self.node.inputs:
                        continue
                    # Update the node inputs with widget values
                    self.node.inputs[param_name] = value
                    logger.debug(f"Mapped widget value {idx} to {param_name}: {value}")

                    # Update the widget with the value from widgets_values
                    if param_name in self.param_widgets:
                        widget = self.param_widgets[param_name]
                        if isinstance(widget, QComboBox):
                            # Set combo box to the value
                            index = widget.findText(str(value))
                            if index >= 0:
                                widget.setCurrentIndex(index)
                        elif isinstance(widget, QSpinBox):
                            widget.setValue(int(value) if isinstance(value, (int, float)) else 0)
                        elif isinstance(widget, QDoubleSpinBox):
                            widget.setValue(
                                float(value) if isinstance(value, (int, float)) else 0.0
                            )
                        elif isinstance(widget, QCheckBox):
                            widget.setChecked(bool(value))
                        elif isinstance(widget, QLineEdit):
                            widget.setText(str(value))
                        elif isinstance(widget, QTextEdit):
                            widget.setPlainText(str(value))

    def add_basic_inputs(self, layout: QFormLayout) -> None:  # noqa: PLR0912, PLR0915
        """Add basic text inputs for current node values.

        Args:
            layout: Form layout to add to
        """
        # First, try to display widgets_values if they exist
        if self.node.widgets_values:
            # Try to map widget values based on common node types
            widget_mapping = self.get_widget_mapping_for_node_type(self.node.type)

            for idx, saved_value in enumerate(self.node.widgets_values):
                if widget_mapping and idx < len(widget_mapping):
                    param_name = widget_mapping[idx]
                else:
                    param_name = f"widget_{idx}"

                value = self.node.inputs.get(param_name, saved_value)
                if param_name in self.node.inputs and isinstance(value, (list, tuple)):
                    continue  # Render connections as read-only inputs below.
                is_seed_param = self._is_seed_parameter(param_name)

                # Create appropriate widget based on value type
                if isinstance(value, bool):
                    widget = QCheckBox()
                    widget.setChecked(value)
                    # Capture param_name in closure properly
                    widget.toggled.connect(
                        lambda checked, name=param_name: self.on_param_changed(name, checked)
                    )
                elif isinstance(value, int):
                    if is_seed_param:
                        seed_input = self._create_seed_value_input(param_name, value)
                        widget = self._wrap_seed_mode_widget(seed_input, param_name)
                    else:
                        widget = QSpinBox()
                        widget.setRange(-999999, 999999)
                        widget.setValue(value)
                        # Capture param_name in closure properly
                        widget.valueChanged.connect(
                            lambda val, name=param_name: self.on_param_changed(name, val)
                        )
                elif isinstance(value, float):
                    if is_seed_param:
                        seed_input = self._create_seed_value_input(param_name, value)
                        widget = self._wrap_seed_mode_widget(seed_input, param_name)
                    else:
                        widget = QDoubleSpinBox()
                        widget.setDecimals(3)
                        widget.setRange(-999999.0, 999999.0)
                        widget.setValue(value)
                        # Capture param_name in closure properly
                        widget.valueChanged.connect(
                            lambda val, name=param_name: self.on_param_changed(name, val)
                        )
                elif isinstance(value, str):
                    if is_seed_param:
                        seed_input = self._create_seed_value_input(param_name, value)
                        widget = self._wrap_seed_mode_widget(seed_input, param_name)
                        layout.addRow(param_name + ":", widget)
                        self.param_widgets[param_name] = self._resolve_actual_widget(widget)
                        # Store in inputs for later use
                        self.node.inputs[param_name] = value
                        continue

                    choice_widget = self._create_basic_string_choice_widget(param_name, value)
                    if choice_widget is not None:
                        widget = choice_widget
                    else:
                        # Regular string
                        widget = QLineEdit(str(value))
                        widget.textChanged.connect(
                            lambda text, name=param_name: self.on_param_changed(name, text)
                        )
                else:
                    # Default case
                    widget = QLineEdit(str(value))
                    widget.textChanged.connect(
                        lambda text, name=param_name: self.on_param_changed(name, text)
                    )

                layout.addRow(param_name + ":", widget)
                self.param_widgets[param_name] = self._resolve_actual_widget(widget)
                # Store in inputs for later use
                self.node.inputs[param_name] = value

        # Then add any additional inputs not covered by widgets_values
        for param_name, param_value in self.node.inputs.items():
            if param_name not in self.param_widgets:  # Only add if not already added
                if isinstance(param_value, (int, float, str)):
                    if self._is_seed_parameter(param_name):
                        seed_input = self._create_seed_value_input(param_name, param_value)
                        widget = self._wrap_seed_mode_widget(seed_input, param_name)
                    else:
                        widget = QLineEdit(str(param_value))
                        widget.textChanged.connect(
                            lambda text, name=param_name: self.on_param_changed(name, text)
                        )
                    layout.addRow(param_name + ":", widget)
                    self.param_widgets[param_name] = self._resolve_actual_widget(widget)
                else:
                    # Complex value - show as read-only text
                    widget = QTextEdit(str(param_value))
                    widget.setMaximumHeight(60)
                    widget.setReadOnly(True)
                    layout.addRow(param_name + ":", widget)

    def get_widget_mapping_for_node_type(self, node_type: str) -> list[str] | None:
        """Get widget parameter names for common node types.

        Args:
            node_type: The ComfyUI node type

        Returns:
            List of parameter names in order, or None if unknown
        """
        # Common node type widget mappings
        mappings = {
            "KSampler": [
                "seed",
                "control_after_generate",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "denoise",
            ],
            "KSamplerAdvanced": [
                "add_noise",
                "noise_seed",
                "control_after_generate",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "start_at_step",
                "end_at_step",
                "return_with_leftover_noise",
            ],
            "CLIPTextEncode": ["text"],
            "CheckpointLoaderSimple": ["ckpt_name"],
            "VAEDecode": [],
            "VAEEncode": [],
            "LoadImage": ["image", "upload"],
            "SaveImage": ["filename_prefix"],
            "EmptyLatentImage": ["width", "height", "batch_size"],
            "LatentUpscale": ["upscale_method", "width", "height", "crop"],
            "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
            "CLIPSetLastLayer": ["stop_at_clip_layer"],
            "ControlNetLoader": ["control_net_name"],
            "ControlNetApply": ["strength"],
        }
        return mappings.get(node_type)

    def create_input_widget(self, param_name: str, param_spec: Any) -> QWidget | None:
        """Create an input widget based on parameter specification.

        Args:
            param_name: Parameter name
            param_spec: Parameter specification from schema

        Returns:
            Created widget or None
        """
        try:
            # Enhanced debug logging for LoadImage node
            if self.node.type == "LoadImage":
                logger.info(f"LoadImage node - Creating widget for {param_name}")
                logger.info(f"  param_spec type: {type(param_spec)}")
            if isinstance(param_spec, list):
                logger.info(f"  param_spec length: {len(param_spec)}")
                if len(param_spec) > 0:
                    logger.info(f"  param_spec[0] type: {type(param_spec[0])}")
                    if isinstance(param_spec[0], (str, list)):
                        sample_value = param_spec[0][:100]
                    else:
                        sample_value = param_spec[0]
                    logger.info(f"  param_spec[0] value: {sample_value}")

            if isinstance(param_spec, (str, list)) and len(str(param_spec)) > 100:
                spec_dump = str(param_spec[:100])
            else:
                spec_dump = param_spec
            logger.debug(
                f"Creating widget for {param_name}, "
                f"spec type: {type(param_spec)}, spec: {spec_dump}"
            )

            # Skip connection type parameters - these represent node connections, not user inputs
            # Exception: Some nodes use IMAGE type for file input parameters
            connection_types = [
                "LATENT",
                "CONDITIONING",
                "MODEL",
                "VAE",
                "CLIP",
                "CONTROL_NET",
                "IMAGE",
                "MASK",
            ]
            if (
                isinstance(param_spec, list)
                and len(param_spec) >= 1
                and param_spec[0] in connection_types
            ):
                # Special case: IMAGE type parameters that are actually file inputs
                if param_spec[0] == "IMAGE" and (
                    param_name == "image"
                    or param_name.endswith("_image")
                    or param_name.startswith("image_")
                ):
                    # Create layer selection widget for image input parameters
                    current_value = self.node.inputs.get(param_name)
                    widget = ImageParameterWidget(param_name, current_value, host_api=self.host_api)
                    widget.parameter_changed.connect(
                        lambda name, value: self.on_param_changed(name, value)
                    )
                    return widget
                else:
                    logger.debug(
                        f"Skipping connection type parameter: {param_name} of type {param_spec[0]}"
                    )
                    return None

            # Format 1c (current ComfyUI): ["COMBO", {"options": [...]}]. The
            # Format 0/1/3 checks below all assume the choices are a list in the
            # spec itself, so this shape fell through to a plain text field.
            if isinstance(param_spec, list) and param_spec and param_spec[0] == COMBO_HEAD:
                _head, combo_options = split_input_spec(param_spec)
                choices = combo_choices(param_spec[0], combo_options)
                if choices:
                    if (
                        param_name == "image"
                        or param_name.endswith("_image")
                        or param_name.startswith("image_")
                    ):
                        current_value = self.node.inputs.get(param_name)
                        widget = ImageParameterWidget(
                            param_name,
                            current_value,
                            host_api=self.host_api,
                            file_list=choices,
                        )
                        widget.parameter_changed.connect(
                            lambda name, value: self.on_param_changed(name, value or current_value)
                        )
                        return widget
                    current_value = self.node.inputs.get(param_name, combo_options.get("default"))
                    return self._create_choice_combo(
                        param_name,
                        choices,
                        current_value=current_value,
                        tooltip=combo_options.get("tooltip") or None,
                        tooltip_context="combo",
                        allow_partial_match=True,
                    )

            # Check for Format 0: Direct list of string choices ["option1", "option2", ...]
            # This is how ComfyUI actually provides sampler_name and scheduler choices
            # Exclude single-element type definitions like ["STRING"], ["INT"], ["MODEL"], etc.
            known_types = [
                "STRING",
                "INT",
                "FLOAT",
                "BOOLEAN",
                "MODEL",
                "CONDITIONING",
                "LATENT",
                "IMAGE",
                "MASK",
                "CONTROL_NET",
                "CLIP",
                "VAE",
            ]
            is_type_definition = (
                isinstance(param_spec, list)
                and len(param_spec) == 1
                and param_spec[0] in known_types
            )

            if (
                isinstance(param_spec, list)
                and not is_type_definition
                and all(isinstance(item, str) for item in param_spec)
            ):
                # Log for LoadImage debugging
                if self.node.type == "LoadImage" and param_name == "image":
                    logger.info("LoadImage image parameter entering Format 0 processing")
                    logger.info(f"  is_type_definition: {is_type_definition}")
                    logger.info(
                        f"  all strings: {all(isinstance(item, str) for item in param_spec)}"
                    )
                    if len(param_spec) > 3:  # noqa: SIM108
                        spec_sample = param_spec[:3]
                    else:
                        spec_sample = param_spec
                    logger.info(f"  param_spec sample: {spec_sample}")

                # Special case: Image parameters should use layer selection widget
                if (
                    param_name == "image"
                    or param_name.endswith("_image")
                    or param_name.startswith("image_")
                ):
                    logger.info(
                        f"Image parameter detected ({param_name}), creating layer selection widget"
                    )
                    current_value = self.node.inputs.get(param_name)
                    # Pass the file list from ComfyUI to the widget
                    widget = ImageParameterWidget(
                        param_name, current_value, host_api=self.host_api, file_list=param_spec
                    )
                    widget.parameter_changed.connect(
                        lambda name, value: self.on_param_changed(name, value or current_value)
                    )
                    return widget

                # Direct list of string choices (e.g., sampler_name, scheduler)
                choices = param_spec
                current_value = self.node.inputs.get(param_name)
                if len(choices) > 5:  # noqa: SIM108
                    choices_preview = choices[:5]
                else:
                    choices_preview = choices
                logger.debug(
                    f"Format 0 (direct string list) detected for {param_name}: {choices_preview}"
                )

                return self._create_choice_combo(
                    param_name,
                    choices,
                    current_value=current_value,
                    tooltip_context="direct",
                )

            # Check if this is a choices parameter (list containing a list)
            # Format 1a: [["option1", "option2", ...]]  (length 1)
            # Format 1b: [["option1", "option2", ...], {"tooltip": "..."}]  (length 2)
            if (
                isinstance(param_spec, list)
                and len(param_spec) >= 1
                # Check if first element is a list of strings (choices)
                and isinstance(param_spec[0], list)
                and all(isinstance(x, str) for x in param_spec[0])
            ):
                # Special case: Image parameters should use layer selection widget
                if (
                    param_name == "image"
                    or param_name.endswith("_image")
                    or param_name.startswith("image_")
                ):
                    fmt_message = (
                        f"Format 1b image parameter detected ({param_name}), "
                        "creating layer selection widget"
                    )
                    logger.info(fmt_message)
                    current_value = self.node.inputs.get(param_name)
                    # Pass the file list from ComfyUI to the widget
                    widget = ImageParameterWidget(
                        param_name,
                        current_value,
                        host_api=self.host_api,
                        file_list=param_spec[0],
                    )
                    widget.parameter_changed.connect(
                        lambda name, value: self.on_param_changed(name, value or current_value)
                    )
                    return widget

                # This is a choices parameter with optional options dict
                choices = param_spec[0]
                # Get tooltip if available
                tooltip = None
                if len(param_spec) > 1 and isinstance(param_spec[1], dict):
                    tooltip = param_spec[1].get("tooltip", "")

                current_value = self.node.inputs.get(param_name)
                logger.debug(
                    f"Format 1 (nested list) detected for {param_name}: {len(choices)} choices"
                )
                if tooltip:
                    logger.debug(f"  Tooltip: {tooltip}")

                return self._create_choice_combo(
                    param_name,
                    choices,
                    current_value=current_value,
                    tooltip=tooltip,
                    tooltip_context="nested",
                    allow_partial_match=True,
                )

            # Check for Format 3:
            # ["STRING", ["choice1", "choice2"]] or ["INT", ["choice1", "choice2"]]
            if (
                isinstance(param_spec, list)
                and len(param_spec) == 2
                and isinstance(param_spec[1], list)
            ):
                # Second element is a list of choices
                param_type = param_spec[0]
                choices = param_spec[1]
                current_value = self.node.inputs.get(param_name)
                if len(choices) > 5:  # noqa: SIM108
                    choices_preview = choices[:5]
                else:
                    choices_preview = choices
                logger.debug(
                    f"Format 3 choices detected for {param_name}: "
                    f"type={param_type}, choices={choices_preview}"
                )

                return self._create_choice_combo(
                    param_name,
                    choices,
                    current_value=current_value,
                    tooltip_context="typed",
                )

            # Extract parameter type and options for regular parameters
            if isinstance(param_spec, list) and len(param_spec) > 0:
                param_type = param_spec[0]
                options = param_spec[1] if len(param_spec) > 1 else {}
            elif isinstance(param_spec, str):
                param_type = param_spec
                options = {}
            else:
                # Fallback to string input
                widget = QLineEdit()
                widget.textChanged.connect(
                    lambda text, name=param_name: self.on_param_changed(name, text)
                )
                return widget

            # Check if options contain choices (sometimes in dict format)
            if isinstance(options, dict) and "choices" in options:
                # Choices embedded in options dict
                choices = options["choices"]
                current_value = self.node.inputs.get(param_name, options.get("default"))

                return self._create_choice_combo(
                    param_name,
                    choices,
                    current_value=current_value,
                )

            # Get current value
            current_value = self.node.inputs.get(param_name)
            # Get default value from schema if no current value
            if current_value is None and isinstance(options, dict):
                current_value = options.get("default", "")

            # Create widget based on type
            if param_type == "INT":
                # Get the raw values
                min_val = options.get("min", -999999) if isinstance(options, dict) else -999999
                max_val = options.get("max", 999999) if isinstance(options, dict) else 999999
                default_val = options.get("default", 0) if isinstance(options, dict) else 0

                # Check if this is a seed parameter or has very large range
                is_seed_param = self._is_seed_parameter(param_name)
                if is_seed_param or max_val > 2147483647:
                    # Use QLineEdit for seeds and very large numbers
                    # QSpinBox only supports 32-bit signed integers (-2147483648 to 2147483647)
                    seed_value = current_value if current_value is not None else default_val
                    widget = self._create_seed_value_input(
                        param_name,
                        seed_value,
                        placeholder_source="Range: {min_val} to {max_val}",
                        placeholder_kwargs={"min_val": min_val, "max_val": max_val},
                        tooltip_source="Integer value (min: {min_val}, max: {max_val})",
                        tooltip_kwargs={"min_val": min_val, "max_val": max_val},
                    )
                    if is_seed_param:
                        widget = self._wrap_seed_mode_widget(widget, param_name)
                    return widget
                else:
                    # Regular integer - create compound widget with slider and spinbox
                    container = QWidget()
                    layout = QHBoxLayout(container)
                    layout.setContentsMargins(0, 0, 0, 0)
                    layout.setSpacing(5)

                    # Clamp values to QSpinBox/QSlider limits
                    min_val = max(min_val, -2147483647)
                    max_val = min(max_val, 2147483647)

                    # Create slider
                    slider = QSlider(Qt.Horizontal)
                    slider.setRange(min_val, max_val)

                    # Create spinbox
                    spinbox = QSpinBox()
                    spinbox.setRange(min_val, max_val)

                    # Set value with proper default handling
                    initial_value = default_val
                    if current_value is not None and isinstance(current_value, (int, float)):
                        initial_value = max(min_val, min(max_val, int(current_value)))

                    slider.setValue(initial_value)
                    spinbox.setValue(initial_value)
                    fit_spinbox_to_values(spinbox, (initial_value,))

                    # Connect slider and spinbox bidirectionally
                    def on_slider_changed(value):
                        spinbox.blockSignals(True)
                        spinbox.setValue(value)
                        spinbox.blockSignals(False)
                        fit_spinbox_to_values(spinbox, (value,))
                        self.on_param_changed(param_name, value)

                    def on_spinbox_changed(value):
                        slider.blockSignals(True)
                        slider.setValue(value)
                        slider.blockSignals(False)
                        self.on_param_changed(param_name, value)

                    slider.valueChanged.connect(on_slider_changed)
                    spinbox.valueChanged.connect(on_spinbox_changed)

                    # Metadata bind button
                    bind_btn = self._create_bind_button(param_name, "int", spinbox)

                    # Add to layout
                    layout.addWidget(slider, 1)
                    layout.addWidget(spinbox, 0)
                    layout.addWidget(bind_btn, 0)

                    return container

            elif param_type == "FLOAT":
                # Create compound widget with slider and double spinbox
                container = QWidget()
                layout = QHBoxLayout(container)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(5)

                min_val = options.get("min", -999999.0) if isinstance(options, dict) else -999999.0
                max_val = options.get("max", 999999.0) if isinstance(options, dict) else 999999.0
                default_val = options.get("default", 0.0) if isinstance(options, dict) else 0.0

                # Determine step size and precision based on range
                val_range = max_val - min_val
                if val_range <= 1.0:
                    step = 0.01
                    decimals = 3
                    slider_steps = int(val_range / 0.001)  # More precision for small ranges
                elif val_range <= 10.0:
                    step = 0.1
                    decimals = 2
                    slider_steps = int(val_range / 0.01)
                elif val_range <= 100.0:
                    step = 1.0
                    decimals = 1
                    slider_steps = int(val_range / 0.1)
                else:
                    step = max(1.0, val_range / 1000)
                    decimals = 0
                    slider_steps = min(1000, int(val_range / step))

                # Create slider (using integer steps internally)
                slider = QSlider(Qt.Horizontal)
                slider.setRange(0, slider_steps)

                # Create double spinbox
                spinbox = QDoubleSpinBox()
                spinbox.setRange(min_val, max_val)
                spinbox.setDecimals(decimals)
                spinbox.setSingleStep(step)

                # Set value with proper default handling
                initial_value = default_val
                if current_value is not None and isinstance(current_value, (int, float)):
                    initial_value = max(min_val, min(max_val, float(current_value)))

                spinbox.setValue(initial_value)
                fit_spinbox_to_values(spinbox, (initial_value,))
                # Convert to slider position
                if val_range > 0:
                    slider_pos = int((initial_value - min_val) / val_range * slider_steps)
                    slider.setValue(slider_pos)

                # Connect slider and spinbox bidirectionally
                def on_float_slider_changed(pos):
                    value = (
                        min_val + (pos / slider_steps * val_range) if slider_steps > 0 else min_val
                    )
                    spinbox.blockSignals(True)
                    spinbox.setValue(value)
                    spinbox.blockSignals(False)
                    fit_spinbox_to_values(spinbox, (value,))
                    self.on_param_changed(param_name, value)

                def on_float_spinbox_changed(value):
                    if val_range > 0 and slider_steps > 0:
                        slider_pos = int((value - min_val) / val_range * slider_steps)
                        slider.blockSignals(True)
                        slider.setValue(slider_pos)
                        slider.blockSignals(False)
                    self.on_param_changed(param_name, value)

                slider.valueChanged.connect(on_float_slider_changed)
                spinbox.valueChanged.connect(on_float_spinbox_changed)

                # Metadata bind button
                bind_btn = self._create_bind_button(param_name, "float", spinbox)

                # Add to layout
                layout.addWidget(slider, 1)
                layout.addWidget(spinbox, 0)
                layout.addWidget(bind_btn, 0)

                return container

            elif param_type == "STRING":
                # Special case: Any node with image parameter (LoadImage, SaveImage, etc.)
                if (
                    param_name == "image"
                    or param_name.endswith("_image")
                    or param_name.startswith("image_")
                ):
                    # Create layer selection widget for image parameters
                    widget = ImageParameterWidget(param_name, current_value, host_api=self.host_api)

                    def handle_image_change(name, value):
                        # Ensure we never send None or invalid values to ComfyUI
                        if value is None or value == "":
                            # Use current value or first file from list
                            if current_value:
                                self.on_param_changed(name, current_value)
                            elif param_spec and len(param_spec) > 0:
                                self.on_param_changed(name, param_spec[0])
                            # else: don't change the parameter
                        else:
                            self.on_param_changed(name, value)

                    widget.parameter_changed.connect(handle_image_change)
                elif isinstance(options, dict) and "multiline" in options and options["multiline"]:
                    text_widget = QTextEdit()
                    text_widget.setMaximumHeight(80)
                    text_widget.setPlainText(
                        str(current_value) if current_value is not None else ""
                    )

                    def handle_text_change(name=param_name):
                        if self._updating_prompt_selection:
                            return
                        self.on_param_changed(name, text_widget.toPlainText())

                    text_widget.textChanged.connect(handle_text_change)
                    return self._wrap_with_prompt_composer(param_name, text_widget)
                else:
                    line_widget = QLineEdit(str(current_value) if current_value is not None else "")

                    def handle_line_change(text: str, name=param_name):
                        if self._updating_prompt_selection:
                            return
                        self.on_param_changed(name, text)
                        self._reset_prompt_combo(name)

                    line_widget.textChanged.connect(handle_line_change)
                    return self._wrap_with_prompt_selector(param_name, line_widget, multiline=False)

            elif param_type == "BOOLEAN":
                widget = QCheckBox()
                widget.setChecked(bool(current_value) if current_value is not None else False)
                widget.toggled.connect(
                    lambda checked, name=param_name: self.on_param_changed(name, checked)
                )
                return widget

            elif param_type == "IMAGE":
                # Special handling for image input parameters
                if (
                    param_name == "image"
                    or param_name.endswith("_image")
                    or param_name.startswith("image_")
                ):
                    # Create layer selection widget for any image input parameter
                    widget = ImageParameterWidget(param_name, current_value, host_api=self.host_api)
                    widget.parameter_changed.connect(
                        lambda name, value: self.on_param_changed(name, value)
                    )
                    return widget
                else:
                    # IMAGE is a connection type for output parameters, should not have a widget
                    logger.debug(f"Skipping IMAGE connection parameter: {param_name}")
                    return None

            else:
                # Default to string input
                widget = QLineEdit(str(current_value))
                widget.textChanged.connect(
                    lambda text, name=param_name: self.on_param_changed(name, text)
                )
                return widget

        except Exception as e:
            logger.error(f"Error creating input widget for {param_name}: {e}")
            # Fallback to simple text input
            widget = QLineEdit(str(current_value) if "current_value" in locals() else "")
            widget.textChanged.connect(
                lambda text, name=param_name: self.on_param_changed(name, text)
            )
            return widget

    def _add_output_layer_selector(self, layout: QFormLayout) -> None:
        """Append a layer selection widget for SaveImage nodes."""
        self.output_layer_widget = OutputLayerWidget(host_api=self.host_api, parent=self)
        layout.addRow(
            bind_widget_text(QLabel(), "Output Layer:", domain="NodeParameterWidget"),
            self.output_layer_widget,
        )

    def get_output_layer_target(self) -> LayerRef | None:
        """Return the selected layer for SaveImage outputs, if any."""
        if self.output_layer_widget:
            return self.output_layer_widget.get_selected_layer()
        return None

    def _add_output_3d_layer_selector(self, layout: QFormLayout) -> None:
        """Append a layer selection widget for Save 3D Model nodes."""
        self.output_3d_layer_widget = OutputLayerWidget(
            host_api=self.host_api,
            info_text="(Optional) Choose a layer to receive Save 3D Model output",
            allowed_layer_types=("raster", "3d"),
            create_new_layer_label="(Create new 3D layer)",
            parent=self,
        )
        layout.addRow(
            bind_widget_text(QLabel(), "3D Output Layer:", domain="NodeParameterWidget"),
            self.output_3d_layer_widget,
        )

    def get_output_3d_layer_target(self) -> LayerRef | None:
        """Return the selected layer for Save 3D Model outputs, if any."""
        if self.output_3d_layer_widget:
            return self.output_3d_layer_widget.get_selected_layer()
        return None

    # ------------------------------------------------------------------
    # Metadata binding
    # ------------------------------------------------------------------

    def _create_bind_button(
        self,
        param_name: str,
        value_type: str,
        spinbox: QSpinBox | QDoubleSpinBox,
    ) -> QToolButton:
        """Create a small link-icon button for metadata binding."""
        btn = QToolButton()
        btn.setFixedSize(20, 20)
        btn.setToolTip(_node_tabs_tr("Bind to metadata"))
        btn.setProperty("param_name", param_name)
        btn.setProperty("value_type", value_type)
        self._update_bind_button_style(btn, bound=False)
        btn.clicked.connect(
            lambda _checked=False, _p=param_name, _t=value_type, _b=btn, _s=spinbox: (
                self._show_bind_menu(_p, _t, _b, _s)
            )
        )
        return btn

    def _update_bind_button_style(self, btn: QToolButton, *, bound: bool) -> None:
        if bound:
            btn.setText("\u2b95")  # right arrow (bound indicator)
            btn.setStyleSheet("QToolButton { color: #4fc3f7; font-weight: bold; border: none; }")
        else:
            btn.setText("\U0001f517")  # link emoji
            btn.setStyleSheet("QToolButton { color: #888; border: none; }")

    def _show_bind_menu(
        self,
        param_name: str,
        value_type: str,
        btn: QToolButton,
        spinbox: QSpinBox | QDoubleSpinBox,
    ) -> None:
        """Show context menu for selecting a metadata binding."""
        registry = get_metadata_registry()
        menu = QMenu(self)

        for category in registry.categories():
            entries = registry.entries_for(category, value_type)
            if not entries:
                continue
            sub = menu.addMenu(_node_tabs_tr(category))
            for entry in entries:
                label = _node_tabs_tr(entry.label)
                if entry.needs_layer:
                    layer_sub = sub.addMenu(label)
                    self._populate_layer_submenu(layer_sub, param_name, entry.key, btn, spinbox)
                else:
                    action = sub.addAction(label)
                    action.triggered.connect(
                        lambda _checked=False, _k=entry.key, _p=param_name, _b=btn, _s=spinbox: (
                            self._apply_binding(_p, _k, None, _b, _s)
                        )
                    )

        # Clear binding option
        if param_name in self._metadata_bindings:
            menu.addSeparator()
            clear_action = menu.addAction(_node_tabs_tr("Clear binding"))
            clear_action.triggered.connect(lambda: self._clear_binding(param_name, btn))

        menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _populate_layer_submenu(
        self,
        menu: QMenu,
        param_name: str,
        entry_key: str,
        btn: QToolButton,
        spinbox: QSpinBox | QDoubleSpinBox,
    ) -> None:
        """Add layer list to a submenu for entries that need a layer."""
        layers: list[LayerRef] = []
        if self.host_api:
            try:
                layers = self.host_api.canvas.get_layers()
            except Exception:
                logger.debug("Failed to get layers for bind menu", exc_info=True)

        if not layers:
            action = menu.addAction(_node_tabs_tr("(no layers)"))
            action.setEnabled(False)
            return

        for layer in layers:
            label = layer.name or layer.id
            action = menu.addAction(label)
            _lid = layer.id
            action.triggered.connect(
                lambda _c=False, _k=entry_key, _l=_lid, _p=param_name, _b=btn, _s=spinbox: (
                    self._apply_binding(_p, _k, _l, _b, _s)
                )
            )

    def _apply_binding(
        self,
        param_name: str,
        entry_key: str,
        layer_id: str | None,
        btn: QToolButton,
        spinbox: QSpinBox | QDoubleSpinBox,
    ) -> None:
        """Set a metadata binding and immediately resolve the value."""
        self._metadata_bindings[param_name] = MetadataBinding(
            entry_key=entry_key, layer_id=layer_id
        )
        self._update_bind_button_style(btn, bound=True)

        # Resolve and apply value — let signals propagate so slider syncs too
        if self.host_api:
            registry = get_metadata_registry()
            ctx = ResolveContext(layer_id=layer_id)
            value = registry.resolve(entry_key, self.host_api, ctx)
            if value is not None:
                spinbox.setValue(int(value) if isinstance(spinbox, QSpinBox) else float(value))

        entry = get_metadata_registry().get_entry(entry_key)
        label = entry.label if entry else entry_key
        logger.info("Bound %s to %s (layer=%s)", param_name, label, layer_id)

    def _clear_binding(self, param_name: str, btn: QToolButton) -> None:
        """Remove a metadata binding."""
        self._metadata_bindings.pop(param_name, None)
        self._update_bind_button_style(btn, bound=False)
        logger.info("Cleared binding for %s", param_name)

    def get_metadata_bindings(self) -> dict[str, MetadataBinding]:
        """Return current metadata bindings for submission-time resolution."""
        return dict(self._metadata_bindings)

    def on_param_changed(self, param_name: str, value: Any) -> None:
        """Handle parameter value change.

        Args:
            param_name: Parameter name
            value: New value
        """
        # Update node data
        self.node.inputs[param_name] = value

        # Emit signal
        self.parameter_changed.emit(self.node_id, param_name, value)

        logger.debug(f"Parameter {param_name} changed to {value}")

    def refresh_translations(self) -> None:
        """Refresh translated labels for the current language."""
        if self._header_label is not None:
            self._header_label.setText(_node_tabs_tr("Node: {node}").format(node=self.node.type))
        if self._optional_label is not None:
            self._optional_label.setText(f"<i>{_node_tabs_tr('Optional:')}</i>")
        for widget in self.findChildren(QWidget):
            self._refresh_runtime_widget_text(widget)
        self._refresh_prompt_selector_options()
        for widget in self.param_widgets.values():
            if hasattr(widget, "refresh_translations"):
                widget.refresh_translations()
        if self.output_layer_widget is not None:
            self.output_layer_widget.refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def get_updated_node(self) -> NodeConfig:
        """Get the node with updated parameter values.

        Returns:
            Updated node configuration
        """
        return self.node


class NodeTabsWidget(QWidget):
    """Category-aware panel for browsing ComfyUI workflow nodes."""

    node_parameters_changed = Signal()

    WORKFLOW_CATEGORY = "workflow"
    USER_SETTINGS_CATEGORY = "user-settings"
    USER_FILTER_SETTINGS_KEY = "comfyui/user_filters"

    def __init__(self, host_api: HostAPI | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.workflow: WorkflowModel | None = None
        self.node_schemas: dict[str, Any] | None = None
        self.models_cache: dict[str, Any] = {}
        self.host_api: HostAPI | None = host_api
        self._settings = get_settings_manager()
        self._user_filter_manager = UserFilterManager(
            self._settings,
            self.USER_FILTER_SETTINGS_KEY,
        )

        self.node_metadata: dict[str, NodeMetadata] = {}
        self._node_order: list[str] = []

        self.tab_widget: SafeDetachableTabWidget = SafeDetachableTabWidget()
        try:
            if hasattr(self.tab_widget, "tab_detached"):
                self.tab_widget.tab_detached.connect(self.on_tab_detached)
        except Exception as exc:  # pragma: no cover - best-effort safety
            logger.warning("Could not connect detach signal: %s", exc)
        self.tab_widget.currentChanged.connect(self._on_parameter_tab_changed)

        self.category_tabs = QTabWidget()
        self.category_tabs.setDocumentMode(True)
        self.category_tabs.currentChanged.connect(self._on_category_changed)

        self.user_filter_tabs = QTabWidget()
        self.user_filter_tabs.setDocumentMode(True)
        self.user_filter_tabs.currentChanged.connect(self._on_user_filter_tab_changed)
        self.user_filter_tabs.hide()

        self._category_manager = CategoryTabManager(
            category_tabs=self.category_tabs,
            parameter_tabs=self.tab_widget,
            translate=self._tr,
            entries_provider=self._iter_entries,
            node_id_for_tab=self._node_id_for_tab,
            tab_index_for_node=self._tab_index_for_node,
        )
        self._category_manager.active_category_key = self.WORKFLOW_CATEGORY
        self.category_pages: dict[str, CategoryPage] = self._category_manager.pages
        self.user_filter_pages: dict[str, CategoryPage] = {}
        self.user_filters: list[UserFilterConfig] = self._load_user_filters()
        self._header_label: QLabel | None = None
        self._user_filters_header_label: QLabel | None = None
        self._edit_filters_button: QPushButton | None = None
        self._user_filters_placeholder: QLabel | None = None

        self.init_ui()

    @property
    def active_category_key(self) -> str:
        """Compatibility mirror for the active category key."""
        return self._category_manager.active_category_key

    @active_category_key.setter
    def active_category_key(self, key: str) -> None:
        self._category_manager.active_category_key = key

    def set_host_api(self, host_api: HostAPI | None) -> None:
        """Set host API and propagate to existing parameter widgets."""
        self.host_api = host_api
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                widget.set_host_api(host_api)

    def refresh_layer_widgets(self) -> None:
        """Refresh all IMAGE widgets with current layers."""
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                widget.refresh_layer_widgets()

    def init_ui(self) -> None:
        """Build the category UI shell."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._header_label = QLabel(self._tr("Workflow Nodes"))
        self._header_label.setStyleSheet("font-weight: bold; color: #4a9eff; padding: 5px;")
        layout.addWidget(self._header_label)
        layout.addWidget(self.category_tabs)

        self._create_builtin_categories()
        self._init_user_settings_tab()

        if self.category_tabs.count():
            self.category_tabs.setCurrentIndex(0)
            self._activate_category(self.WORKFLOW_CATEGORY)

    def _create_builtin_categories(self) -> None:
        self._build_category_page(
            key=self.WORKFLOW_CATEGORY,
            title="Workflow Nodes",
            filter_func=lambda _: True,
        )

    def _init_user_settings_tab(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        header_layout = QHBoxLayout()
        self._user_filters_header_label = QLabel(self._tr("User Filters"))
        header_layout.addWidget(self._user_filters_header_label)
        header_layout.addStretch()
        self._edit_filters_button = QPushButton(self._tr("Edit Filters"))
        self._edit_filters_button.clicked.connect(self._open_filter_manager)
        header_layout.addWidget(self._edit_filters_button)
        layout.addLayout(header_layout)

        layout.addWidget(self.user_filter_tabs)
        placeholder = QLabel(self._tr('No user filters yet.\nUse "Edit Filters" to add.'))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setStyleSheet("color: #888; padding: 12px;")
        layout.addWidget(placeholder)
        self._user_filters_placeholder = placeholder

        index = self.category_tabs.addTab(container, self._tr("User Presets"))
        self._set_tab_key(self.category_tabs, index, self.USER_SETTINGS_CATEGORY)

        self._rebuild_user_filter_tabs()

    def _set_tab_key(self, tab_widget: QTabWidget, index: int, key: str) -> None:
        self._category_manager.set_tab_key(tab_widget, index, key)

    def _tab_key(self, tab_widget: QTabWidget, index: int) -> str | None:
        return self._category_manager.tab_key(tab_widget, index)

    def _build_category_page(
        self,
        *,
        key: str,
        title: str,
        filter_func: CategoryFilter,
        parent_tab: QTabWidget | None = None,
    ) -> CategoryPage:
        return self._category_manager.build_page(
            key=key,
            title=title,
            filter_func=filter_func,
            parent_tab=parent_tab,
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def refresh_translations(self) -> None:
        if self._header_label is not None:
            self._header_label.setText(self._tr("Workflow Nodes"))
        if self._user_filters_header_label is not None:
            self._user_filters_header_label.setText(self._tr("User Filters"))
        if self._edit_filters_button is not None:
            self._edit_filters_button.setText(self._tr("Edit Filters"))
        if self._user_filters_placeholder is not None:
            self._user_filters_placeholder.setText(
                self._tr('No user filters yet.\nUse "Edit Filters" to add.')
            )
        self._set_tab_text(
            self.category_tabs,
            self.WORKFLOW_CATEGORY,
            self._tr("Workflow Nodes"),
        )
        self._set_tab_text(
            self.category_tabs,
            self.USER_SETTINGS_CATEGORY,
            self._tr("User Presets"),
        )
        for page in self.category_pages.values():
            if page.placeholder_label is not None:
                page.placeholder_label.setText(self._tr("No nodes match this category."))
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if hasattr(widget, "refresh_translations"):
                widget.refresh_translations()

    def _translated_text(self, text: str) -> str:
        source_text = getattr(text, "source_text", text)
        return self._tr(source_text) if isinstance(source_text, str) else ""

    def _tr(self, text: str) -> str:
        return translate_with_fallback("NodeTabsWidget", text)

    def _set_tab_text(self, tab_widget: QTabWidget, key: str, text: str) -> None:
        for index in range(tab_widget.count()):
            if self._tab_key(tab_widget, index) == key:
                tab_widget.setTabText(index, text)
                return

    def load_workflow(
        self,
        workflow: WorkflowModel,
        node_schemas: dict[str, Any] | None = None,
        models_cache: dict[str, Any] | None = None,
    ) -> None:
        """Load a workflow and rebuild node tabs."""
        self.workflow = workflow
        self.node_schemas = node_schemas
        self.models_cache = models_cache or {}

        self.tab_widget.clear()
        self.node_metadata.clear()
        self._node_order = []

        if not workflow or not workflow.nodes:
            logger.info("No workflow or nodes to load")
            self._refresh_categories()
            self._apply_active_category_filter()
            return

        logger.info("Loading workflow with %d nodes", len(workflow.nodes))
        if any(isinstance(node.inputs, list) for node in workflow.nodes.values()):
            api_workflow = workflow.to_api_format(node_schemas)
            for node_id, node in workflow.nodes.items():
                if isinstance(node.inputs, list):
                    node.inputs = api_workflow[str(node_id)]["inputs"]
        for node_id, node in workflow.nodes.items():
            str_id = str(node_id)
            self._node_order.append(str_id)
            self.node_metadata[str_id] = self._build_node_metadata(str_id, node)
            self.add_node_tab(str_id, node, node_schemas, models_cache)

        self._refresh_categories()
        self._apply_active_category_filter()

    def add_node_tab(
        self,
        node_id: str,
        node: NodeConfig,
        node_schemas: dict[str, Any] | None = None,
        models_cache: dict[str, Any] | None = None,
    ) -> None:
        schema = None
        if node_schemas and node.type in node_schemas:
            schema = node_schemas[node.type]

        try:
            param_widget = NodeParameterWidget(
                node_id, node, schema, models_cache, host_api=self.host_api
            )
        except Exception as exc:
            logger.error(
                "Failed to build node tab for %s (%s): %s",
                node_id,
                getattr(node, "type", "unknown"),
                exc,
                exc_info=True,
            )
            placeholder = QWidget()
            placeholder_layout = QVBoxLayout(placeholder)
            placeholder_layout.setContentsMargins(6, 6, 6, 6)
            placeholder_layout.setSpacing(4)
            error_label = bind_widget_text(
                QLabel(),
                "Failed to render parameters for node: {node}",
                domain="NodeTabsWidget",
                node=getattr(node, "type", node_id),
            )
            error_label.setWordWrap(True)
            placeholder_layout.addWidget(error_label)
            placeholder_layout.addStretch(1)
            param_widget = placeholder
        else:
            param_widget.parameter_changed.connect(self.on_node_parameter_changed)
            param_widget.setProperty("node_id", node_id)

        tab_title = node.type if len(node.type) <= 12 else f"{node.type[:9]}..."
        self.tab_widget.addTab(param_widget, tab_title)

    def on_tab_detached(self, index: int, dock: QDockWidget) -> None:
        logger.info(f"Tab {index} detached to dock: {dock.windowTitle()}")

    def on_node_parameter_changed(self, node_id: str, param_name: str, value: Any) -> None:
        logger.debug(f"Node {node_id} parameter {param_name} changed to {value}")
        self.node_parameters_changed.emit()

    def get_updated_workflow(self) -> WorkflowModel | None:
        return self.workflow

    def get_image_parameters(self) -> dict[str, ImageParameterWidget]:
        image_params: dict[str, ImageParameterWidget] = {}
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                for param_name, param_widget in widget.param_widgets.items():
                    if isinstance(param_widget, ImageParameterWidget):
                        key = f"{widget.node_id}.{param_name}"
                        image_params[key] = param_widget
        return image_params

    def get_all_metadata_bindings(self) -> dict[str, MetadataBinding]:
        """Collect metadata bindings from all node tabs.

        Returns dict keyed by ``node_id.param_name``.
        """
        bindings: dict[str, MetadataBinding] = {}
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                for param_name, binding in widget.get_metadata_bindings().items():
                    key = f"{widget.node_id}.{param_name}"
                    bindings[key] = binding
        return bindings

    def get_output_layer_targets(self) -> dict[str, LayerRef]:
        targets: dict[str, LayerRef] = {}
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                selection = widget.get_output_layer_target()
                if isinstance(selection, LayerRef) and selection.id:
                    targets[widget.node_id] = selection
        return targets

    def get_output_3d_layer_targets(self) -> dict[str, LayerRef]:
        targets: dict[str, LayerRef] = {}
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                selection = widget.get_output_3d_layer_target()
                if isinstance(selection, LayerRef) and selection.id:
                    targets[widget.node_id] = selection
        return targets

    def get_seed_modes(self) -> dict[str, dict[str, str]]:
        modes: dict[str, dict[str, str]] = {}
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget):
                widget_modes = widget.get_seed_modes()
                if widget_modes:
                    modes[widget.node_id] = widget_modes
        return modes

    def nodes_updated(self) -> None:
        self.node_parameters_changed.emit()

    # --- Category handling -------------------------------------------------

    def _refresh_categories(self) -> None:
        self._category_manager.refresh()

    def _on_category_changed(self, index: int) -> None:
        if index < 0:
            return
        key = self._tab_key(self.category_tabs, index)
        if key == self.USER_SETTINGS_CATEGORY:
            self._activate_current_user_filter()
            return
        self._activate_category(str(key or self.WORKFLOW_CATEGORY))

    def _on_user_filter_tab_changed(self, index: int) -> None:
        if (
            self._tab_key(self.category_tabs, self.category_tabs.currentIndex())
            != self.USER_SETTINGS_CATEGORY
        ):
            return
        if index < 0:
            return
        key = self._tab_key(self.user_filter_tabs, index)
        if isinstance(key, str):
            self._activate_category(key)

    def _activate_current_user_filter(self) -> None:
        if not self.user_filter_pages:
            self._detach_tab_widget_from_parent()
            if self._user_filters_placeholder:
                self._user_filters_placeholder.show()
            self.user_filter_tabs.hide()
            return

        if self._user_filters_placeholder:
            self._user_filters_placeholder.hide()
        self.user_filter_tabs.show()

        index = self.user_filter_tabs.currentIndex()
        if index < 0 and self.user_filter_tabs.count():
            self.user_filter_tabs.setCurrentIndex(0)
            index = 0

        if index >= 0:
            key = self._tab_key(self.user_filter_tabs, index)
            if isinstance(key, str):
                self._activate_category(key)

    def _rebuild_user_filter_tabs(self) -> None:
        for key in list(self.category_pages.keys()):
            if key.startswith("user:"):
                del self.category_pages[key]
        self.user_filter_pages.clear()

        self.user_filter_tabs.blockSignals(True)
        self.user_filter_tabs.clear()
        self.user_filter_tabs.blockSignals(False)

        if not self.user_filters:
            self.user_filter_tabs.hide()
            if self._user_filters_placeholder:
                self._user_filters_placeholder.show()
            return

        self.user_filter_tabs.show()
        if self._user_filters_placeholder:
            self._user_filters_placeholder.hide()

        for config in self.user_filters:
            key = f"user:{config.filter_id}"
            page = self._build_category_page(
                key=key,
                title=config.display_name or "Custom",
                filter_func=lambda meta, cfg=config: self._match_user_filter(meta, cfg),
                parent_tab=self.user_filter_tabs,
            )
            self.user_filter_pages[key] = page

        if self.user_filter_tabs.count():
            self.user_filter_tabs.setCurrentIndex(0)

    def _activate_category(self, key: str) -> None:
        self._category_manager.activate_category(key)

    def _attach_tab_widget(self, page: CategoryPage) -> None:
        self._category_manager.attach_parameter_tabs(page)

    def _detach_tab_widget_from_parent(self) -> None:
        self._category_manager.detach_parameter_tabs()

    def _populate_category(self, page: CategoryPage) -> None:
        self._category_manager.populate(page)

    def _apply_active_category_filter(self) -> None:
        self._category_manager.apply_active_filter()

    def _apply_tab_visibility(self, allowed_ids: set[str] | None) -> None:
        self._category_manager.apply_tab_visibility(allowed_ids)

    def _update_placeholder(self, page: CategoryPage) -> None:
        self._category_manager.update_placeholder(page)

    def _on_list_selection_changed(self, category_key: str) -> None:
        self._category_manager.on_list_selection_changed(category_key)

    def _on_parameter_tab_changed(self, index: int) -> None:
        self._category_manager.on_parameter_tab_changed(index)

    def _focus_node(self, node_id: str | None) -> None:
        self._category_manager.focus_node(node_id)

    def _tab_index_for_node(self, node_id: str) -> int | None:
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if isinstance(widget, NodeParameterWidget) and widget.node_id == node_id:
                return index
        return None

    def _node_id_for_tab(self, index: int) -> str | None:
        if index < 0:
            return None
        widget = self.tab_widget.widget(index)
        if isinstance(widget, NodeParameterWidget):
            return widget.node_id
        return None

    def _select_list_item(self, page: CategoryPage, node_id: str) -> None:
        self._category_manager.select_list_item(page, node_id)

    def _open_filter_manager(self) -> None:
        dialog = ManageUserFiltersDialog(self.user_filters, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.user_filters = dialog.result_filters()
        self._save_user_filters()
        self._rebuild_user_filter_tabs()
        self._refresh_categories()
        if (
            self._tab_key(self.category_tabs, self.category_tabs.currentIndex())
            == self.USER_SETTINGS_CATEGORY
        ):
            self._activate_current_user_filter()

    def _load_user_filters(self) -> list[UserFilterConfig]:
        return self._user_filter_manager.load()

    def _save_user_filters(self) -> None:
        self._user_filter_manager.save(self.user_filters)

    def _match_user_filter(self, meta: NodeMetadata, config: UserFilterConfig) -> bool:
        return self._user_filter_manager.matches(meta, config)

    @staticmethod
    def _keyword_match(targets: Iterable[str], keywords: Iterable[str]) -> bool:
        return UserFilterManager.keyword_match(targets, keywords)

    @staticmethod
    def _normalize_keywords(raw_value: Any) -> tuple[str, ...]:
        return UserFilterManager.normalize_keywords(raw_value)

    def _iter_entries(self) -> Iterable[NodeMetadata]:
        for node_id in self._node_order:
            meta = self.node_metadata.get(node_id)
            if meta:
                yield meta

    def _build_node_metadata(self, node_id: str, node: NodeConfig) -> NodeMetadata:
        param_names = tuple(str(name).lower() for name in node.inputs)
        has_image = any("image" in name for name in param_names)
        node_type = node.type or ""
        node_type_lower = node_type.lower()
        value_types: set[str] = set()
        for value in node.inputs.values():
            inferred = self._infer_value_type(value)
            if inferred:
                value_types.add(inferred)
        if has_image:
            value_types.add("image")
        return NodeMetadata(
            node_id=node_id,
            node_type=node_type,
            display_name=node.title or node_type or node_id,
            param_names=param_names,
            has_image_param=has_image,
            is_sampler="sampler" in node_type_lower,
            is_controlnet=self._detect_controlnet(node_type_lower, param_names),
            value_types=tuple(sorted(value_types)),
        )

    def _detect_controlnet(self, node_type_lower: str, param_names: Iterable[str]) -> bool:
        if "controlnet" in node_type_lower:
            return True
        for name in param_names:
            lowered = name.lower()
            if "controlnet" in lowered or "control_net" in lowered or lowered.startswith("cn_"):
                return True
        return False

    def _infer_value_type(self, value: Any) -> str | None:  # noqa: PLR0911
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            lowered = value.lower()
            if lowered.endswith(("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff")):
                return "image"
            return "string"
        if isinstance(value, (list, tuple)):
            return "list"
        if isinstance(value, dict):
            return "object"
        return None
