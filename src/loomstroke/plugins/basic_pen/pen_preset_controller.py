"""Connect the pen preset UI to the existing tool and its local library."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtWidgets import QDialog, QFileDialog

from ...ui.translations import translated_literal_domain
from .brush_write_feedback import show_brush_operation_notice
from .pen_preset import PenPreset
from .pen_preset_store import PenPresetStore
from .pen_round_tip import round_tip_image

logger = logging.getLogger(__name__)


def _ts(source: str) -> str:
    return translated_literal_domain("PenPresets", source, use_fallback=True)


class PenPresetController(QObject):
    def __init__(self, plugin: Any, panel: Any) -> None:
        super().__init__(panel)
        self.plugin = plugin
        self.panel = panel
        self.store = PenPresetStore(plugin._store.root_dir)
        panel.presetSelected.connect(self.apply_preset)
        panel.requestSave.connect(self.save_new)
        panel.requestUpdate.connect(self.update_selected)
        panel.requestImport.connect(self.import_file)
        panel.requestExport.connect(self.export_file)
        self.refresh()

    @property
    def tool(self) -> Any:
        return self.plugin.pen_tool

    def refresh(self, selected_id: str | None = None) -> None:
        try:
            presets = self.store.list_presets()
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.panel.set_presets(presets, selected_id=selected_id)

    def _error(self, exc: Exception) -> None:
        logger.warning("Pen preset operation failed", exc_info=exc)
        error = str(exc)

        def render() -> str:
            return _ts("Pen preset operation failed: {error}").format(error=error)

        if show_brush_operation_notice(self.plugin, render(), render=render):
            return
        self.plugin._notify(
            _ts("Pen preset operation failed: {error}").format(error=error), level="error"
        )

    def _can_apply(self) -> bool:
        if self.tool.is_stroking:
            self.plugin._notify(_ts("Finish the current stroke before changing pen settings."))
            return False
        return True

    def _capture(self, preset_id: str | None = None) -> PenPreset:
        tip_path = self.tool.get_tip_path()
        if tip_path:
            path = Path(tip_path)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError(_ts("The brush tip is too large for a pen preset."))
            tip_png = path.read_bytes()
            hardness_mode = self.tool._hardness_mode
        else:
            # Round pen presets use the established radial stamp path so their
            # hardness control is effective, including after portable import.
            image = round_tip_image()
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise ValueError(_ts("The brush tip image could not be read."))
            tip_png = bytes(buffer.data())
            hardness_mode = "radial"
        return PenPreset.capture(
            self.panel.preset_name(),
            self.tool.get_settings(),
            tip_png=tip_png,
            hardness_mode=hardness_mode,
            preset_id=preset_id,
        )

    def save_new(self) -> None:
        try:
            preset = self._capture()
            self.store.save_preset(preset)
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.refresh(preset.id)

    def update_selected(self) -> None:
        preset_id = self.panel.selected_preset_id()
        if preset_id is None:
            self.plugin._notify(_ts("Select a pen preset first."))
            return
        try:
            preset = self._capture(preset_id)
            self.store.save_preset(preset)
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.refresh(preset.id)

    def apply_preset(self, preset_id: str) -> None:
        if not self._can_apply():
            return
        try:
            preset = self.store.get_preset(preset_id)
            if preset is None:
                raise ValueError(_ts("The selected pen preset is unavailable."))
            tip = self.store.materialize_tip(preset)
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.tool.set_settings(preset.tool_settings())
        self.tool.set_tip_path(str(tip) if tip else None)
        self.tool.set_hardness_mode(preset.hardness_mode)
        self.plugin._sync_tool_properties(self.tool.get_settings())
        self.panel.set_selected_preset(preset.id)
        self._clear_swatch_selection()
        self._remember(preset.id)

    def _remember(self, preset_id: str | None) -> None:
        settings = None
        previous = None
        had_value = False
        try:
            settings = self.plugin._store.load_settings()
            had_value = "last_selected_pen_id" in settings
            previous = settings.get("last_selected_pen_id")
            settings["last_selected_pen_id"] = preset_id
            self.plugin._store.save_settings(settings)
        except (OSError, ValueError) as exc:
            if settings is not None:
                if had_value:
                    settings["last_selected_pen_id"] = previous
                else:
                    settings.pop("last_selected_pen_id", None)
            self._error(exc)

    def swatch_selected(self) -> None:
        self._remember(None)
        self.panel.set_selected_preset(None)

    def _clear_swatch_selection(self) -> None:
        panel = getattr(self.plugin, "_panel", None)
        clear = getattr(panel, "clear_swatch_selection", None)
        if callable(clear):
            clear()

    def restore_tip(self) -> None:
        """Restore the selected preset tip while retaining saved live settings."""
        try:
            preset_id = self.plugin._store.load_settings().get("last_selected_pen_id")
            if not isinstance(preset_id, str):
                return
            preset = self.store.get_preset(preset_id)
            if preset is None:
                return
            tip = self.store.materialize_tip(preset)
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.tool.set_tip_path(str(tip) if tip else None)
        self.tool.set_hardness_mode(preset.hardness_mode)
        self.panel.set_selected_preset(preset.id)
        self._clear_swatch_selection()

    def import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.panel, _ts("Import Pen"), "", _ts("LoomStroke Pen (*.lspn)")
        )
        if not path:
            return
        try:
            preset = self.store.import_preset(Path(path))
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.refresh(preset.id)
        self.apply_preset(preset.id)

    def export_file(self) -> None:
        preset_id = self.panel.selected_preset_id()
        if preset_id is None:
            self.plugin._notify(_ts("Select a pen preset first."))
            return
        try:
            preset = self.store.get_preset(preset_id)
            if preset is None:
                raise ValueError(_ts("The selected pen preset is unavailable."))
            dialog = QFileDialog(self.panel, _ts("Export Pen"))
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.setNameFilter(_ts("LoomStroke Pen (*.lspn)"))
            dialog.setDefaultSuffix("lspn")
            dialog.selectFile("pen.lspn")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.store.export_preset(preset, Path(dialog.selectedFiles()[0]))
        except (OSError, ValueError) as exc:
            self._error(exc)
