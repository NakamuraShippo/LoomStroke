"""Layer selection widgets used by ComfyUI node parameter editors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from loomstroke.host_api import LayerRef
from loomstroke.plugins.comfyui.output_targets import CREATE_NEW_3D_LAYER_ID
from loomstroke.ui.translations import translate_with_fallback

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI

logger = logging.getLogger(__name__)


def _node_tabs_tr(text: str) -> str:
    return translate_with_fallback("NodeTabsWidget", text)


class _RefreshComboBox(QComboBox):
    """ComboBox that refreshes its contents when the dropdown is opened."""

    def __init__(self, refresh_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._refresh_callback = refresh_callback

    def showPopup(self) -> None:
        if callable(self._refresh_callback):
            try:
                self._refresh_callback()
            except Exception:
                logger.debug("Layer refresh failed on popup", exc_info=True)
        super().showPopup()


class ImageParameterWidget(QWidget):
    """Widget for IMAGE type parameters with layer selection."""

    parameter_changed = Signal(str, object)  # param_name, value

    def __init__(
        self,
        param_name: str,
        current_value: Any,
        host_api: HostAPI | None = None,
        file_list: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        """Initialize image parameter widget.

        Args:
            param_name: Parameter name
            current_value: Current parameter value
            host_api: Host API for accessing layers
            file_list: Optional list of available files from ComfyUI
            parent: Parent widget
        """
        super().__init__(parent)

        self.param_name = param_name
        self.host_api: HostAPI | None = host_api
        self.selected_layer_ref: LayerRef | None = None
        self.selected_layer_name: str | None = None
        self.selected_file: str | None = None
        self.current_value = current_value  # Store the workflow's image path
        self.file_list: list[str] = list(file_list) if file_list else []
        self._layer_header_index: int | None = None
        self._source_label: QLabel | None = None

        self.init_ui()

    def init_ui(self) -> None:
        """Initialize the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.layer_combo: QComboBox = _RefreshComboBox(self._populate_layers)
        self.layer_combo.currentIndexChanged.connect(self.on_layer_changed)

        self._source_label = QLabel(_node_tabs_tr("Image Source:"))
        layout.addWidget(self._source_label)
        layout.addWidget(self.layer_combo)

        info_text = (
            _node_tabs_tr("Current: {value}").format(value=self.current_value)
            if self.current_value
            else _node_tabs_tr("No image specified")
        )
        self.layer_info_label = QLabel(info_text)
        self.layer_info_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self.layer_info_label)

        self.setLayout(layout)

        self._populate_layers()

    def set_host_api(self, host_api: HostAPI | None) -> None:
        """Update host API reference and refresh layer options."""
        if self.host_api is host_api:
            return
        self.host_api = host_api
        self._populate_layers(preserve_selection=False)

    def refresh_layers(self) -> None:
        """Public helper to refresh available layers."""
        self._populate_layers()

    def refresh_translations(self) -> None:
        """Refresh translated labels for the current language."""
        if self._source_label is not None:
            self._source_label.setText(_node_tabs_tr("Image Source:"))
        self._populate_layers()

    def _populate_layers(self, preserve_selection: bool = True) -> None:  # noqa: PLR0912
        """Populate combo box with file options and host layers."""
        previous_layer_id: str | None = None
        previous_file: str | None = None
        if preserve_selection:
            if isinstance(self.selected_layer_ref, LayerRef):
                previous_layer_id = self.selected_layer_ref.id
            elif self.selected_file:
                previous_file = self.selected_file

        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()

        self.layer_combo.addItem(_node_tabs_tr("(Use workflow file)"), "__workflow__")

        for file_name in (self.file_list or [])[:10]:
            self.layer_combo.addItem(
                _node_tabs_tr("File: {file_name}").format(file_name=file_name),
                file_name,
            )

        self.layer_combo.addItem(_node_tabs_tr("--- Layers ---"), "__header__")
        self._layer_header_index = self.layer_combo.count() - 1
        model = self.layer_combo.model()
        if isinstance(model, QStandardItemModel):
            item = model.item(self._layer_header_index)
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)

        selection_index: int | None = None
        layers = self._get_layers()
        active_layer_id = self._get_active_layer_id()
        name_counts: dict[str, int] = {}
        for layer in layers:
            base_name = layer.name or layer.id or "Layer"
            counter = name_counts.get(base_name, 0)
            name_counts[base_name] = counter + 1
            display_name = base_name if counter == 0 else f"{base_name} ({counter + 1})"
            idx = self.layer_combo.count()
            self.layer_combo.addItem(display_name, layer)
            if (previous_layer_id and layer.id == previous_layer_id) or (
                not previous_layer_id and active_layer_id and layer.id == active_layer_id
            ):
                selection_index = idx

        self.layer_combo.blockSignals(False)

        if selection_index is not None:
            self.layer_combo.setCurrentIndex(selection_index)
        elif previous_file:
            idx = self.layer_combo.findData(previous_file)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
            else:
                self.layer_combo.setCurrentIndex(0)
        else:
            self.layer_combo.setCurrentIndex(0)

        self._update_label()

    def _get_layers(self) -> list[LayerRef]:
        if not self.host_api or not hasattr(self.host_api, "canvas"):
            return []
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas or not hasattr(canvas, "get_layers"):
            return []
        try:
            layers = canvas.get_layers()
        except Exception:
            return []
        valid: list[LayerRef] = []
        for layer in layers or []:
            if getattr(layer, "id", None):
                valid.append(layer)
        return valid

    def _get_active_layer_id(self) -> str | None:
        if not self.host_api or not hasattr(self.host_api, "canvas"):
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas and hasattr(canvas, "current_layer"):
            try:
                current = canvas.current_layer()
            except Exception:
                current = None
            if current and getattr(current, "id", None):
                return current.id
        return None

    def _update_label(self) -> None:
        if self.selected_layer_ref and isinstance(self.selected_layer_ref, LayerRef):
            display = (
                self.selected_layer_name
                or self.selected_layer_ref.name
                or self.selected_layer_ref.id
                or "Layer"
            )
            self.layer_info_label.setText(
                _node_tabs_tr("Using layer: {display}").format(display=display)
            )
        elif self.selected_file:
            self.layer_info_label.setText(
                _node_tabs_tr("Selected file: {file_name}").format(file_name=self.selected_file)
            )
        elif self.current_value:
            self.layer_info_label.setText(
                _node_tabs_tr("Using workflow: {workflow}").format(workflow=self.current_value)
            )
        else:
            self.layer_info_label.setText(_node_tabs_tr("No image specified"))

    def on_layer_changed(self, index: int) -> None:
        """Handle layer selection change."""
        selected_data = self.layer_combo.currentData()

        if selected_data == "__header__":
            self.layer_combo.blockSignals(True)
            self.layer_combo.setCurrentIndex(0)
            self.layer_combo.blockSignals(False)
            return

        if selected_data == "__workflow__":
            self.selected_layer_ref = None
            self.selected_layer_name = None
            self.selected_file = None
            self._update_label()
            if self.current_value:
                self.parameter_changed.emit(self.param_name, self.current_value)
            return

        if isinstance(selected_data, LayerRef):
            self.selected_layer_ref = selected_data
            self.selected_layer_name = selected_data.name or selected_data.id or "Layer"
            self.selected_file = None
            self._update_label()
            if self.current_value:
                self.parameter_changed.emit(self.param_name, self.current_value)
            return

        if isinstance(selected_data, str):
            self.selected_file = selected_data
            self.selected_layer_ref = None
            self.selected_layer_name = None
            self.current_value = selected_data
            self._update_label()
            self.parameter_changed.emit(self.param_name, selected_data)
            return

        self.selected_layer_ref = None
        self.selected_layer_name = None
        self.selected_file = None
        self._update_label()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def get_value(self) -> Any:
        """Get current parameter value for workflow."""
        if self.selected_file:
            return self.selected_file
        if self.selected_layer_ref:
            return self.current_value if self.current_value is not None else ""
        return self.current_value if self.current_value is not None else ""

    def get_selected_layer(self) -> Any:
        """Get the currently selected layer object (for export)."""
        return self.selected_layer_ref if isinstance(self.selected_layer_ref, LayerRef) else None


class OutputLayerWidget(QWidget):
    """Widget for selecting a target layer to receive generated images."""

    layer_changed = Signal(object)  # Emits Optional[str]

    def __init__(
        self,
        host_api: HostAPI | None = None,
        info_text: str | None = None,
        allowed_layer_types: tuple[str, ...] | None = None,
        create_new_layer_label: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.host_api: HostAPI | None = host_api
        self.selected_layer_id: str | None = None
        self._selected_layer: LayerRef | None = None
        self._info_text_source = (
            info_text or "(Optional) Choose a layer to receive SaveImage output"
        )
        self._allowed_layer_types = (
            tuple(str(layer_type).lower() for layer_type in allowed_layer_types)
            if allowed_layer_types
            else None
        )
        self._create_new_layer_label = create_new_layer_label
        self._apply_result_label: QLabel | None = None
        self._info_label: QLabel | None = None

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.layer_combo: QComboBox = _RefreshComboBox(self.refresh_layers)
        self.layer_combo.currentIndexChanged.connect(self._on_selection_changed)
        self._apply_result_label = QLabel(_node_tabs_tr("Apply Result To:"))
        layout.addWidget(self._apply_result_label)
        layout.addWidget(self.layer_combo)

        self._info_label = QLabel(_node_tabs_tr(self._info_text_source))
        self._info_label.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._info_label)

        layout.addStretch()
        self.setLayout(layout)

        self.refresh_layers()

    def set_host_api(self, host_api: HostAPI | None) -> None:
        if self.host_api is host_api:
            return
        self.host_api = host_api
        self.refresh_layers()

    def refresh_translations(self) -> None:
        """Refresh translated labels for the current language."""
        if self._apply_result_label is not None:
            self._apply_result_label.setText(_node_tabs_tr("Apply Result To:"))
        if self._info_label is not None:
            self._info_label.setText(_node_tabs_tr(self._info_text_source))
        self.refresh_layers()

    def refresh_layers(self) -> None:
        previous_layer_id = self.selected_layer_id

        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        self.layer_combo.addItem(_node_tabs_tr("(Do not import)"), None)
        selection_index: int | None = None
        if self._create_new_layer_label:
            create_ref = LayerRef(
                id=CREATE_NEW_3D_LAYER_ID,
                name=self._create_new_layer_label,
                layer_type="3d_create",
            )
            idx = self.layer_combo.count()
            self.layer_combo.addItem(_node_tabs_tr(self._create_new_layer_label), create_ref)
            if previous_layer_id == CREATE_NEW_3D_LAYER_ID:
                selection_index = idx

        layers = self._get_layers()
        name_counts: dict[str, int] = {}
        for layer in layers:
            base_name = layer.name or layer.id or "Layer"
            counter = name_counts.get(base_name, 0)
            name_counts[base_name] = counter + 1
            display_name = base_name if counter == 0 else f"{base_name} ({counter + 1})"
            idx = self.layer_combo.count()
            self.layer_combo.addItem(display_name, layer)
            if previous_layer_id and layer.id == previous_layer_id:
                selection_index = idx

        self.layer_combo.blockSignals(False)

        if selection_index is not None:
            self.layer_combo.setCurrentIndex(selection_index)
        else:
            self.layer_combo.setCurrentIndex(0)
            self.selected_layer_id = None
            self._selected_layer = None

    def get_selected_layer(self) -> LayerRef | None:
        return self._selected_layer

    def set_selected_layer(self, layer_id: str | None) -> None:
        if layer_id is None:
            self.layer_combo.setCurrentIndex(0)
            return
        for idx in range(self.layer_combo.count()):
            data = self.layer_combo.itemData(idx)
            if isinstance(data, LayerRef) and data.id == layer_id:
                self.layer_combo.setCurrentIndex(idx)
                return

    def _on_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        data = self.layer_combo.itemData(index)
        if isinstance(data, LayerRef):
            self.selected_layer_id = data.id
            self._selected_layer = data
            self.layer_changed.emit(data.id)
        else:
            self.selected_layer_id = None
            self._selected_layer = None
            self.layer_changed.emit(None)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _get_layers(self) -> list[LayerRef]:
        if not self.host_api or not hasattr(self.host_api, "canvas"):
            return []
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas or not hasattr(canvas, "get_layers"):
            return []
        try:
            layers = canvas.get_layers() or []
        except Exception:
            return []
        valid: list[LayerRef] = []
        for layer in layers:
            layer_id = getattr(layer, "id", None)
            if not layer_id:
                continue
            layer_type = str(getattr(layer, "layer_type", None) or "raster").lower()
            if self._allowed_layer_types is not None:
                if layer_type not in self._allowed_layer_types:
                    continue
            elif layer_type == "vector" or self._is_vector_layer_id(str(layer_id)):
                continue
            valid.append(layer)
        return valid

    def _is_vector_layer_id(self, layer_id: str) -> bool:
        if not layer_id:
            return False
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        layers = getattr(canvas, "_layers", None) if canvas else None
        if not isinstance(layers, list):
            return False
        for layer in layers:
            if getattr(layer, "id", None) == layer_id:
                return getattr(layer, "layer_type", "raster") == "vector"
        return False
