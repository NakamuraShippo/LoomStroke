"""Preset controller for :mod:`loomstroke.ui.panels.tool_panel`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QComboBox, QInputDialog, QWidget

from loomstroke.core.tool_manager import ToolManager
from loomstroke.plugins.sdk.tool import ToolMetadata, ToolPreset


class ToolPresetController:
    """Owns ToolPropertiesPanel preset combo state and CRUD delegation."""

    def __init__(
        self,
        *,
        parent: QWidget,
        combo: QComboBox,
        tool_manager: Callable[[], ToolManager | None],
        current_tool_id: Callable[[], str | None],
        collect_settings: Callable[[], dict[str, Any]],
        translate_label: Callable[[str], str],
        translate_panel_text: Callable[[str], str],
    ) -> None:
        self._parent = parent
        self._combo = combo
        self._tool_manager = tool_manager
        self._current_tool_id = current_tool_id
        self._collect_settings = collect_settings
        self._translate_label = translate_label
        self._translate_panel_text = translate_panel_text

    def clear(self) -> None:
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.blockSignals(False)

    def refresh(self, metadata: ToolMetadata) -> None:
        selected_name = self.current_name()
        self._combo.blockSignals(True)
        self._combo.clear()
        for preset in metadata.presets:
            source_name = self._source_name(preset)
            self._combo.addItem(self._translate_label(preset.name), source_name)
        if selected_name:
            index = self._combo.findData(selected_name)
            if index >= 0:
                self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)

    def current_name(self) -> str:
        preset_name = self._combo.currentData()
        if isinstance(preset_name, str) and preset_name:
            return preset_name
        return self._combo.currentText()

    def apply_selected(self, preset_name: str) -> None:
        manager = self._tool_manager()
        tool_id = self._current_tool_id()
        if manager is None or not tool_id:
            return
        selected_name = self.current_name() or preset_name
        if not selected_name:
            return
        manager.apply_preset(tool_id, selected_name)

    def save(self) -> None:
        manager = self._tool_manager()
        tool_id = self._current_tool_id()
        if manager is None or not tool_id:
            return
        name, ok = QInputDialog.getText(
            self._parent,
            self._translate_panel_text("Preset name"),
            self._translate_panel_text("Name:"),
        )
        if not ok or not name.strip():
            return
        preset = ToolPreset(name=name.strip(), values=self._collect_settings())
        manager.save_preset(tool_id, preset)
        metadata = manager.get_tool_metadata(tool_id)
        if metadata:
            self.refresh(metadata)
            index = self._combo.findData(preset.name)
            if index >= 0:
                self._combo.setCurrentIndex(index)

    def delete(self) -> None:
        manager = self._tool_manager()
        tool_id = self._current_tool_id()
        if manager is None or not tool_id:
            return
        preset_name = self.current_name()
        if not preset_name:
            return
        manager.delete_preset(tool_id, preset_name)
        metadata = manager.get_tool_metadata(tool_id)
        if metadata:
            self.refresh(metadata)

    def _source_name(self, preset: ToolPreset) -> str:
        source_name = getattr(preset.name, "source_text", None)
        if not isinstance(source_name, str) or not source_name:
            source_name = str(preset.name)
        return source_name
