"""Filter selection, parameter editing and brush tips for the filter pen."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.filters import FilterRegistry

from .brush_tip_panel import BrushTipPanel
from .param_widgets import FilterParameterForm
from .tip_library import TipLibrary
from .translations import filter_text, ts


def _setting(settings: Any, key: str, default: Any = None) -> Any:
    key = {
        "effect_type": "filter_type",
        "effect_params": "filter_params",
        "params_by_effect": "params_by_filter",
    }.get(key, key)
    legacy = {
        "filter_type": "effect_type",
        "filter_params": "effect_params",
        "params_by_filter": "params_by_effect",
    }.get(key, key)
    default = (
        settings.get(legacy, default)
        if isinstance(settings, Mapping)
        else getattr(settings, legacy, default)
    )
    return (
        settings.get(key, default)
        if isinstance(settings, Mapping)
        else getattr(settings, key, default)
    )


class FilterPenOptionsWidget(QWidget):
    changed = Signal(dict)

    def __init__(self, tool: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tool = tool
        self._updating = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.filter_label = QLabel(self)
        self.filter_combo = QComboBox(self)
        self.filter_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.filter_combo.setMinimumContentsLength(10)
        for type_id, name, _category in FilterRegistry.list_filters():
            self.filter_combo.addItem(filter_text(name), type_id)
        self.filter_combo.currentIndexChanged.connect(self._filter_changed)
        layout.addWidget(self.filter_label)
        layout.addWidget(self.filter_combo)
        self.parameters = FilterParameterForm(self)
        self.parameters.changed.connect(self._params_changed)
        layout.addWidget(self.parameters)
        library = getattr(tool, "tip_library", None) or TipLibrary()
        self.tips = BrushTipPanel(library, canvas_getter=self._canvas, parent=self)
        self.tips.tip_selected.connect(self._tip_changed)
        layout.addWidget(self.tips)
        self._retranslate()
        self.refresh_settings()
        signal = getattr(tool, "settings_changed", None)
        if signal is not None:
            signal.connect(self._tool_settings_changed)

    @property
    def effect_combo(self):
        return self.filter_combo

    @property
    def effect_label(self):
        return self.filter_label

    def _canvas(self) -> Any:
        host = getattr(self.tool, "host_api", None) or getattr(self.tool, "host", None)
        return getattr(host, "canvas", None)

    def refresh_settings(self) -> None:
        settings = self.tool.get_settings()
        filter_type = _setting(settings, "effect_type", "gaussian_blur")
        by_effect = _setting(settings, "params_by_effect", {})
        current_params = _setting(settings, "effect_params", {})
        params = (
            by_effect.get(filter_type, current_params)
            if isinstance(by_effect, Mapping)
            else current_params
        )
        self._updating = True
        try:
            previous = self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(filter_type)))
            self.filter_combo.blockSignals(previous)
            if self.parameters.filter_type == filter_type:
                self.parameters.set_values(deepcopy(params))
            else:
                self.parameters.set_filter(filter_type, deepcopy(params))
            self.tips.set_tip(_setting(settings, "tip_path"))
        finally:
            self._updating = False

    def set_value(self, _key: str, _value: Any) -> None:
        """Refresh from the tool after ToolManager changes an external setting."""
        self.refresh_settings()

    def _tool_settings_changed(self, _settings: dict[str, Any]) -> None:
        self.refresh_settings()

    def _filter_changed(self, _index: int) -> None:
        if self._updating:
            return
        filter_type = self.filter_combo.currentData()
        if not isinstance(filter_type, str):
            return
        setter = getattr(self.tool, "set_filter", None) or self.tool.set_effect
        setter(filter_type)
        self.refresh_settings()
        self.changed.emit({"effect_type": filter_type})

    def _params_changed(self, params: dict[str, Any]) -> None:
        if self._updating:
            return
        setter = getattr(self.tool, "set_filter_params", None) or self.tool.set_effect_params
        setter(deepcopy(params))
        self.changed.emit({"effect_params": deepcopy(params)})

    def _tip_changed(self, path: str | None) -> None:
        if self._updating:
            return
        self.tool.set_tip(path)
        self.changed.emit({"tip_path": path})

    def _retranslate(self) -> None:
        self.filter_label.setText(ts("Filter"))
        names = {type_id: name for type_id, name, _category in FilterRegistry.list_filters()}
        for index in range(self.filter_combo.count()):
            self.filter_combo.setItemText(
                index, filter_text(names[self.filter_combo.itemData(index)])
            )

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


class FilterPenOptionsDialog(QDialog):
    def __init__(self, tool: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        self.options = FilterPenOptionsWidget(tool, scroll)
        scroll.setWidget(self.options)
        layout.addWidget(scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self.buttons.rejected.connect(self.close)
        layout.addWidget(self.buttons)
        self.resize(360, 660)
        self._retranslate()
        self._panel_position = None
        host = getattr(tool, "host_api", None)
        settings = getattr(host, "settings", None)
        anchor = getattr(parent, "canvas", None)
        if isinstance(anchor, QWidget) and settings is not None:
            from .panel_position import FilterPenPanelPosition  # noqa: PLC0415 - optional UI

            self._panel_position = FilterPenPanelPosition(self, settings, anchor)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        position = getattr(self, "_panel_position", None)
        if position is not None:
            position.dispose()
        super().closeEvent(event)

    def _retranslate(self) -> None:
        self.setWindowTitle(ts("Filter Settings"))
        button = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        if button is not None:
            button.setText(ts("Close"))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


# Compatibility aliases for existing imports.
EffectPenOptionsWidget = FilterPenOptionsWidget
EffectPenOptionsDialog = FilterPenOptionsDialog


_bind_legacy_names(
    __name__,
    {
        "EffectRegistry": "FilterRegistry",
        "EffectParameterForm": "FilterParameterForm",
        "effect_text": "filter_text",
        "EffectPenOptionsWidget": "FilterPenOptionsWidget",
        "EffectPenOptionsDialog": "FilterPenOptionsDialog",
    },
)
