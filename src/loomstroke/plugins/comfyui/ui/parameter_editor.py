"""Dynamic parameter editor for ComfyUI nodes.

This module provides dynamic widget generation for node parameters,
adapted from the legacy implementation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)


@dataclass
class InputSpec:
    """Input specification for a node parameter."""

    name: str
    type: str
    default: Any = None
    min_value: int | float | None = None
    max_value: int | float | None = None
    step: int | float | None = None
    options: list[Any] | None = None
    multiline: bool = False
    tooltip: str | None = None


class ParameterWidget(QWidget):
    """Base class for parameter widgets."""

    value_changed = Signal(str, object)  # param_name, value

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        """Initialize parameter widget.

        Args:
            spec: Input specification
            parent: Parent widget
        """
        super().__init__(parent)
        self.spec = spec
        self.param_name = spec.name

    def get_value(self) -> Any:
        """Get current value."""
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        """Set current value."""
        raise NotImplementedError


class IntParameterWidget(ParameterWidget):
    """Integer parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.spinbox = QSpinBox()
        if spec.min_value is not None:
            self.spinbox.setMinimum(int(spec.min_value))
        else:
            self.spinbox.setMinimum(-999999)

        if spec.max_value is not None:
            self.spinbox.setMaximum(int(spec.max_value))
        else:
            self.spinbox.setMaximum(999999)

        if spec.step is not None:
            self.spinbox.setSingleStep(int(spec.step))

        if spec.default is not None:
            self.spinbox.setValue(int(spec.default))

        if spec.tooltip:
            self.spinbox.setToolTip(spec.tooltip)

        self.spinbox.valueChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))

        layout.addWidget(self.spinbox)
        self.setLayout(layout)

    def get_value(self) -> int:
        return self.spinbox.value()

    def set_value(self, value: Any) -> None:
        self.spinbox.setValue(int(value))


class FloatParameterWidget(ParameterWidget):
    """Float parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.spinbox = QDoubleSpinBox()

        if spec.min_value is not None:
            self.spinbox.setMinimum(float(spec.min_value))
        else:
            self.spinbox.setMinimum(-999999.0)

        if spec.max_value is not None:
            self.spinbox.setMaximum(float(spec.max_value))
        else:
            self.spinbox.setMaximum(999999.0)

        if spec.step is not None:
            self.spinbox.setSingleStep(float(spec.step))
        else:
            self.spinbox.setSingleStep(0.01)

        self.spinbox.setDecimals(3)

        if spec.default is not None:
            self.spinbox.setValue(float(spec.default))

        if spec.tooltip:
            self.spinbox.setToolTip(spec.tooltip)

        self.spinbox.valueChanged.connect(lambda v: self.value_changed.emit(self.param_name, v))

        layout.addWidget(self.spinbox)
        self.setLayout(layout)

    def get_value(self) -> float:
        return self.spinbox.value()

    def set_value(self, value: Any) -> None:
        self.spinbox.setValue(float(value))


class StringParameterWidget(ParameterWidget):
    """String parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        if spec.multiline:
            self.text_edit = QTextEdit()
            self.text_edit.setMaximumHeight(100)

            if spec.default:
                self.text_edit.setPlainText(str(spec.default))

            if spec.tooltip:
                self.text_edit.setToolTip(spec.tooltip)

            self.text_edit.textChanged.connect(
                lambda: self.value_changed.emit(self.param_name, self.text_edit.toPlainText())
            )
            layout.addWidget(self.text_edit)
        else:
            self.line_edit = QLineEdit()

            if spec.default:
                self.line_edit.setText(str(spec.default))

            if spec.tooltip:
                self.line_edit.setToolTip(spec.tooltip)

            self.line_edit.textChanged.connect(
                lambda t: self.value_changed.emit(self.param_name, t)
            )
            layout.addWidget(self.line_edit)

        self.setLayout(layout)

    def get_value(self) -> str:
        if hasattr(self, "text_edit"):
            return self.text_edit.toPlainText()
        else:
            return self.line_edit.text()

    def set_value(self, value: Any) -> None:
        if hasattr(self, "text_edit"):
            self.text_edit.setPlainText(str(value))
        else:
            self.line_edit.setText(str(value))


class BoolParameterWidget(ParameterWidget):
    """Boolean parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.checkbox = QCheckBox()

        if spec.default is not None:
            self.checkbox.setChecked(bool(spec.default))

        if spec.tooltip:
            self.checkbox.setToolTip(spec.tooltip)

        self.checkbox.toggled.connect(lambda c: self.value_changed.emit(self.param_name, c))

        layout.addWidget(self.checkbox)
        layout.addStretch()
        self.setLayout(layout)

    def get_value(self) -> bool:
        return self.checkbox.isChecked()

    def set_value(self, value: Any) -> None:
        self.checkbox.setChecked(bool(value))


class ComboParameterWidget(ParameterWidget):
    """Combo box parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.combo = QComboBox()

        if spec.options:
            for option in spec.options:
                self.combo.addItem(str(option), option)

        if spec.default is not None:
            index = self.combo.findData(spec.default)
            if index >= 0:
                self.combo.setCurrentIndex(index)

        if spec.tooltip:
            self.combo.setToolTip(spec.tooltip)

        self.combo.currentIndexChanged.connect(
            lambda i: self.value_changed.emit(self.param_name, self.combo.currentData())
        )

        layout.addWidget(self.combo)
        self.setLayout(layout)

    def get_value(self) -> Any:
        return self.combo.currentData()

    def set_value(self, value: Any) -> None:
        index = self.combo.findData(value)
        if index >= 0:
            self.combo.setCurrentIndex(index)


class ImageParameterWidget(ParameterWidget):
    """Image/file parameter widget."""

    def __init__(self, spec: InputSpec, parent: QWidget | None = None):
        super().__init__(spec, parent)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(self._translate_runtime("Select image..."))

        if spec.default:
            self.path_edit.setText(str(spec.default))

        if spec.tooltip:
            self.path_edit.setToolTip(spec.tooltip)

        self.browse_button = QPushButton(self._translate_runtime("Browse..."))
        self.browse_button.clicked.connect(self.browse_file)

        layout.addWidget(self.path_edit)
        layout.addWidget(self.browse_button)
        self.setLayout(layout)

        self.path_edit.textChanged.connect(lambda t: self.value_changed.emit(self.param_name, t))

    def browse_file(self) -> None:
        """Show file dialog."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self._translate_runtime("Select Image"),
            "",
            self._translate_runtime("Image Files (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)"),
        )
        if file_path:
            self.path_edit.setText(file_path)

    def get_value(self) -> str:
        return self.path_edit.text()

    def set_value(self, value: Any) -> None:
        self.path_edit.setText(str(value))

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ImageParameterWidget", text)

    def refresh_translations(self) -> None:
        self.path_edit.setPlaceholderText(self._translate_runtime("Select image..."))
        self.browse_button.setText(self._translate_runtime("Browse..."))

    def changeEvent(self, event: QEvent) -> None:  # pragma: no cover - Qt passthrough
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()


class ParameterEditor(QWidget):
    """Dynamic parameter editor for node inputs."""

    parameters_changed = Signal(dict)  # All parameters

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.widgets: dict[str, ParameterWidget] = {}
        self.init_ui()

    def init_ui(self) -> None:
        """Initialize UI."""
        # Main layout with scroll area
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for parameters
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Content widget
        self.content = QWidget()
        self.content_layout = QVBoxLayout()
        self.content_layout.setSpacing(10)
        self.content.setLayout(self.content_layout)

        scroll.setWidget(self.content)
        main_layout.addWidget(scroll)

        self.setLayout(main_layout)

    def load_node_inputs(
        self, node_type: str, input_schema: dict[str, Any], values: dict[str, Any] | None = None
    ) -> None:
        """Load inputs for a node type.

        Args:
            node_type: Node class type
            input_schema: Input schema from object info
            values: Current values (optional)
        """
        # Clear existing widgets
        self.clear()

        # Create title
        title = QLabel(f"<b>{node_type}</b>")
        title.setStyleSheet("font-size: 14px; padding: 5px;")
        self.content_layout.addWidget(title)

        # Process required inputs
        required = input_schema.get("required", {})
        for name, spec in required.items():
            widget = self.create_widget_for_input(name, spec)
            if widget:
                # Set initial value if provided
                if values and name in values:
                    widget.set_value(values[name])

                # Connect signal
                widget.value_changed.connect(self.on_parameter_changed)

                # Add to layout with label
                group = QGroupBox(self.format_label(name))
                group_layout = QVBoxLayout()
                group_layout.addWidget(widget)
                group.setLayout(group_layout)

                self.content_layout.addWidget(group)
                self.widgets[name] = widget

        # Process optional inputs
        optional = input_schema.get("optional", {})
        if optional:
            # Add separator
            sep = QLabel("Optional Parameters")
            sep.setStyleSheet("color: gray; font-weight: bold; padding-top: 10px;")
            self.content_layout.addWidget(sep)

            for name, spec in optional.items():
                widget = self.create_widget_for_input(name, spec)
                if widget:
                    # Set initial value if provided
                    if values and name in values:
                        widget.set_value(values[name])

                    # Connect signal
                    widget.value_changed.connect(self.on_parameter_changed)

                    # Add to layout with label
                    group = QGroupBox(self.format_label(name))
                    group_layout = QVBoxLayout()
                    group_layout.addWidget(widget)
                    group.setLayout(group_layout)

                    self.content_layout.addWidget(group)
                    self.widgets[name] = widget

        # Add stretch at end
        self.content_layout.addStretch()

    def create_widget_for_input(self, name: str, spec: Any) -> ParameterWidget | None:
        """Create appropriate widget for input specification.

        Args:
            name: Parameter name
            spec: Input specification

        Returns:
            Parameter widget or None
        """
        # Parse specification
        if isinstance(spec, list) and len(spec) > 0:
            input_type = spec[0]

            # Extract properties
            props = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}

            # Create InputSpec
            input_spec = InputSpec(
                name=name,
                type=input_type,
                default=props.get("default"),
                min_value=props.get("min"),
                max_value=props.get("max"),
                step=props.get("step"),
                options=spec[1:] if input_type in ["combo", "COMBO"] else None,
                multiline=props.get("multiline", False),
                tooltip=props.get("tooltip"),
            )

            # Create widget based on type
            if input_type in ["INT", "int"]:
                return IntParameterWidget(input_spec)
            elif input_type in ["FLOAT", "float"]:
                return FloatParameterWidget(input_spec)
            elif input_type in ["STRING", "string"]:
                return StringParameterWidget(input_spec)
            elif input_type in ["BOOLEAN", "boolean", "BOOL", "bool"]:
                return BoolParameterWidget(input_spec)
            elif input_type in ["combo", "COMBO"]:
                return ComboParameterWidget(input_spec)
            elif input_type in ["IMAGE", "image"]:
                return ImageParameterWidget(input_spec)
            else:
                # Default to string
                return StringParameterWidget(input_spec)

        return None

    def format_label(self, name: str) -> str:
        """Format parameter name for display.

        Args:
            name: Parameter name

        Returns:
            Formatted label
        """
        # Convert snake_case to Title Case
        return name.replace("_", " ").title()

    def clear(self) -> None:
        """Clear all widgets."""
        # Remove all widgets
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.widgets.clear()

    def on_parameter_changed(self, name: str, value: Any) -> None:
        """Handle parameter change.

        Args:
            name: Parameter name
            value: New value
        """
        # Collect all values
        all_values = self.get_all_values()

        # Emit signal
        self.parameters_changed.emit(all_values)

    def get_all_values(self) -> dict[str, Any]:
        """Get all parameter values.

        Returns:
            Dictionary of parameter values
        """
        values = {}
        for name, widget in self.widgets.items():
            values[name] = widget.get_value()
        return values

    def set_values(self, values: dict[str, Any]) -> None:
        """Set multiple parameter values.

        Args:
            values: Dictionary of values
        """
        for name, value in values.items():
            if name in self.widgets:
                self.widgets[name].set_value(value)
