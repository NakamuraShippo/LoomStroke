"""Connect the pen preset UI to the existing tool and its local library."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QDialog, QFileDialog

from ...ui.translations import translated_literal_domain
from .pen_preset import PenPreset
from .pen_preset_store import PenPresetStore

logger = logging.getLogger(__name__)


def _ts(source: str) -> str:
    return translated_literal_domain("PenPresets", source, use_fallback=True)


class PenPresetController(QObject):
    def __init__(self, plugin: Any, panel: Any) -> None:
        super().__init__(panel)
        self.plugin = plugin
        self.panel = panel
        self.store = PenPresetStore(plugin._store.root_dir)
        self._subscriptions: list[tuple[Any, str, Any]] = []
        self._tool_signal: Any = None
        self._settings_handler = self._settings_changed
        self._tool_handler = self._qt_settings_changed
        panel.settingsEdited.connect(self._edit_settings)
        panel.presetSelected.connect(self.apply_preset)
        panel.requestSave.connect(self.save_new)
        panel.requestUpdate.connect(self.update_selected)
        panel.requestImport.connect(self.import_file)
        panel.requestExport.connect(self.export_file)
        self.refresh()

    @property
    def tool(self) -> Any:
        return self.plugin.pen_tool

    def bind(self) -> None:
        self.unbind()
        host = self.plugin.host_api
        manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        signal = getattr(manager, "tool_setting_changed", None)
        if signal is not None:
            signal.connect(self._tool_handler)
            self._tool_signal = signal
        else:
            bus = getattr(host, "events", None)
            subscribe = getattr(bus, "subscribe", None)
            if callable(subscribe):
                for topic in ("tool.settings_changed", "tool.setting_changed"):
                    token = subscribe(topic, self._settings_handler)
                    self._subscriptions.append((bus, topic, token))
        self.refresh_settings()

    def unbind(self) -> None:
        if self._tool_signal is not None:
            try:
                self._tool_signal.disconnect(self._tool_handler)
            except (RuntimeError, TypeError):
                logger.debug("Pen preset ToolManager signal was already destroyed")
            self._tool_signal = None
        for bus, topic, token in self._subscriptions:
            off = getattr(bus, "off", None)
            if callable(off):
                off(topic, token)
        self._subscriptions.clear()

    def _qt_settings_changed(self, tool_id: str, _key: str, _value: Any) -> None:
        if tool_id == "basic_pen":
            self.refresh_settings()

    def _settings_changed(self, payload: Any) -> None:
        if isinstance(payload, dict) and payload.get("tool_id") == "basic_pen":
            self.refresh_settings()

    def refresh_settings(self) -> None:
        self.panel.set_settings(self.tool.get_settings())

    def refresh(self, selected_id: str | None = None) -> None:
        try:
            presets = self.store.list_presets()
        except (OSError, ValueError) as exc:
            self._error(exc)
            return
        self.panel.set_presets(presets, selected_id=selected_id)
        self.refresh_settings()

    def _error(self, exc: Exception) -> None:
        logger.warning("Pen preset operation failed", exc_info=exc)
        self.plugin._notify(
            _ts("Pen preset operation failed: {error}").format(error=str(exc)), level="error"
        )

    def _can_apply(self) -> bool:
        if self.tool.is_stroking:
            self.plugin._notify(_ts("Finish the current stroke before changing pen settings."))
            self.refresh_settings()
            return False
        return True

    def _edit_settings(self, values: dict[str, Any]) -> None:
        if not self._can_apply():
            return
        self.tool.set_settings(values)
        self.plugin._sync_tool_properties(self.tool.get_settings())
        self.refresh_settings()

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
            image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor(255, 255, 255))
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(1, 1, 126, 126)
            painter.end()
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
        self.tool.set_settings(dict(preset.settings))
        self.tool.set_tip_path(str(tip) if tip else None)
        self.tool.set_hardness_mode(preset.hardness_mode)
        self.plugin._sync_tool_properties(self.tool.get_settings())
        self.panel.set_selected_preset(preset.id)
        self._clear_swatch_selection()
        self.refresh_settings()
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
        self.refresh_settings()

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
        self.refresh_settings()

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
