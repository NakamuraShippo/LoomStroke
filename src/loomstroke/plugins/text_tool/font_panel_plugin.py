from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import fields
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.events import Events
from loomstroke.core.japanese_text.character_geometry import GEOMETRY_LIMITS
from loomstroke.core.japanese_text.typography import normalize_typography
from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin

from .annotations_widget import TextAnnotationsWidget
from .character_geometry_widget import CharacterGeometryWidget
from .common import (
    TEXT_TOOL_ID,
    TEXT_TOOL_IDS,
    TEXT_TOOL_VERTICAL_ID,
    VERTICAL_FLOW_LEFT,
    VERTICAL_FLOW_RIGHT,
    TextToolSettings,
    load_fonts_from_dirs,
    load_text_tool_settings,
    normalize_vertical_flow,
    save_text_tool_setting,
)
from .frame_flow_widget import FrameFlowWidget
from .named_styles_widget import NamedStylesWidget
from .typography_widget import TypographyWidget

logger = logging.getLogger(__name__)
_TEXT_TOOL_ID_SET = set(TEXT_TOOL_IDS)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import (  # noqa: PLC0415
        translate_with_fallback,
        translated_literal,
    )

    return translated_literal(
        lambda source: translate_with_fallback("TextFontPanel", source),
        text,
    )


class FontPanelWidget(QWidget):
    def __init__(self, host_api: HostAPI, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host_api
        self._block_updates = False
        self._color = QColor(0, 0, 0, 255)
        self._status_source: str | None = None
        self._status_kwargs: dict[str, Any] = {}

        self._wrap_check = QCheckBox(self._ts("Word Wrap"), self)
        self._family_combo = QComboBox(self)
        self._style_combo = QComboBox(self)
        self._size_spin = QDoubleSpinBox(self)
        self._vertical_toggle = QCheckBox(self._ts("Vertical Writing"), self)
        self._vertical_flow_combo = QComboBox(self)
        self._align_combo = QComboBox(self)
        self._line_height_spin = QDoubleSpinBox(self)
        self._tracking_spin = QDoubleSpinBox(self)
        self._padding_top_spin = QDoubleSpinBox(self)
        self._padding_right_spin = QDoubleSpinBox(self)
        self._padding_bottom_spin = QDoubleSpinBox(self)
        self._padding_left_spin = QDoubleSpinBox(self)
        self._color_button = QPushButton(self)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._opacity_label = QLabel("100%", self)
        self._font_dirs_list = QListWidget(self)
        self._status_label = QLabel("", self)

        self._label_font_family = QLabel(self._ts("Font Family"), self)
        self._label_font_style = QLabel(self._ts("Font Style"), self)
        self._label_font_size = QLabel(self._ts("Font Size (pt)"), self)
        self._label_paragraph_flow = QLabel(self._ts("Paragraph Flow"), self)
        self._label_alignment = QLabel(self._ts("Alignment"), self)
        self._label_line_height = QLabel(self._ts("Line Height"), self)
        self._label_tracking = QLabel(self._ts("Tracking"), self)
        self._label_padding = QLabel(self._ts("Padding"), self)
        self._label_pad_top = QLabel(self._ts("Top"), self)
        self._label_pad_right = QLabel(self._ts("Right"), self)
        self._label_pad_bottom = QLabel(self._ts("Bottom"), self)
        self._label_pad_left = QLabel(self._ts("Left"), self)
        self._label_color = QLabel(self._ts("Color"), self)
        self._label_opacity = QLabel(self._ts("Opacity"), self)
        self._label_custom_folders = QLabel(self._ts("Custom Font Folders"), self)
        self._add_folder_button = QPushButton(self._ts("Add Folder"), self)
        self._remove_folder_button = QPushButton(self._ts("Remove"), self)
        self._reload_fonts_button = QPushButton(self._ts("Reload Fonts"), self)
        self._typography_widget = TypographyWidget(self)
        self._annotations_widget = TextAnnotationsWidget(self)

        self._setup_ui()
        self._bind_signals()
        self._reload_font_families()
        self._load_initial_values()
        self._connect_tool_manager_signal()
        self._retranslate_ui()

    def _setup_ui(self) -> None:  # noqa: PLR0915
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self._content_scroll = QScrollArea(self)
        self._content_scroll.setWidgetResizable(True)
        self._content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content_widget = QWidget(self._content_scroll)
        self._content_scroll.setWidget(self._content_widget)
        outer_layout.addWidget(self._content_scroll)
        layout = QVBoxLayout(self._content_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._size_spin.setRange(1.0, 512.0)
        self._size_spin.setDecimals(1)
        self._size_spin.setSingleStep(1.0)

        self._align_combo.addItem(self._ts("Left"), "left")
        self._align_combo.addItem(self._ts("Center"), "center")
        self._align_combo.addItem(self._ts("Right"), "right")
        self._align_combo.addItem(self._ts("Justify"), "justify")

        self._line_height_spin.setRange(0.5, 4.0)
        self._line_height_spin.setDecimals(2)
        self._line_height_spin.setSingleStep(0.1)

        self._tracking_spin.setRange(-64.0, 128.0)
        self._tracking_spin.setDecimals(1)
        self._tracking_spin.setSingleStep(0.5)

        for _ps in (
            self._padding_top_spin,
            self._padding_right_spin,
            self._padding_bottom_spin,
            self._padding_left_spin,
        ):
            _ps.setRange(0.0, 256.0)
            _ps.setDecimals(1)
            _ps.setSingleStep(1.0)

        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(100)

        self._font_dirs_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._status_label.setWordWrap(True)

        self._wrap_check.setChecked(True)
        self._vertical_flow_combo.addItem(self._ts("Leftward"), VERTICAL_FLOW_LEFT)
        self._vertical_flow_combo.addItem(self._ts("Rightward"), VERTICAL_FLOW_RIGHT)

        layout.addWidget(self._wrap_check)
        layout.addWidget(self._label_font_family)
        layout.addWidget(self._family_combo)
        layout.addWidget(self._label_font_style)
        layout.addWidget(self._style_combo)
        layout.addWidget(self._label_font_size)
        layout.addWidget(self._size_spin)
        layout.addWidget(self._vertical_toggle)
        layout.addWidget(self._label_paragraph_flow)
        layout.addWidget(self._vertical_flow_combo)

        layout.addWidget(self._label_alignment)
        layout.addWidget(self._align_combo)
        layout.addWidget(self._label_line_height)
        layout.addWidget(self._line_height_spin)
        layout.addWidget(self._label_tracking)
        layout.addWidget(self._tracking_spin)
        layout.addWidget(self._label_padding)
        pad_row1 = QHBoxLayout()
        pad_row1.addWidget(self._label_pad_top)
        pad_row1.addWidget(self._padding_top_spin, 1)
        pad_row1.addWidget(self._label_pad_bottom)
        pad_row1.addWidget(self._padding_bottom_spin, 1)
        layout.addLayout(pad_row1)
        pad_row2 = QHBoxLayout()
        pad_row2.addWidget(self._label_pad_right)
        pad_row2.addWidget(self._padding_right_spin, 1)
        pad_row2.addWidget(self._label_pad_left)
        pad_row2.addWidget(self._padding_left_spin, 1)
        layout.addLayout(pad_row2)

        color_row = QHBoxLayout()
        color_row.addWidget(self._label_color)
        self._color_button.setText("#000000")
        color_row.addWidget(self._color_button, 1)
        layout.addLayout(color_row)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self._label_opacity)
        opacity_row.addWidget(self._opacity_slider, 1)
        opacity_row.addWidget(self._opacity_label)
        layout.addLayout(opacity_row)

        layout.addWidget(self._typography_widget)
        layout.addWidget(self._annotations_widget)
        self._named_styles_widget = NamedStylesWidget(self)
        layout.addWidget(self._named_styles_widget)
        self._geometry_widget = CharacterGeometryWidget(self)
        layout.addWidget(self._geometry_widget)
        self._flow_widget = FrameFlowWidget(self)
        layout.addWidget(self._flow_widget)

        layout.addWidget(self._label_custom_folders)
        layout.addWidget(self._font_dirs_list, 1)

        buttons_row = QHBoxLayout()
        self._add_folder_button.clicked.connect(self._add_font_folder)
        self._remove_folder_button.clicked.connect(self._remove_selected_folder)
        self._reload_fonts_button.clicked.connect(self._reload_custom_fonts)
        buttons_row.addWidget(self._add_folder_button)
        buttons_row.addWidget(self._remove_folder_button)
        buttons_row.addWidget(self._reload_fonts_button)
        layout.addLayout(buttons_row)

        layout.addWidget(self._status_label)

    def _bind_signals(self) -> None:
        self._typography_widget.typography_changed.connect(
            lambda values: self._save("typography", values)
        )
        self._annotations_widget.annotation_requested.connect(self._save)
        self._named_styles_widget.requested.connect(self._save)
        self._geometry_widget.requested.connect(self._save)
        self._flow_widget.requested.connect(self._save)
        self._wrap_check.toggled.connect(lambda st: self._save("word_wrap", bool(st)))
        self._family_combo.currentTextChanged.connect(self._on_family_text_changed)
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)
        self._size_spin.valueChanged.connect(lambda value: self._save("font_size_pt", float(value)))
        self._vertical_toggle.toggled.connect(lambda state: self._save("vertical", bool(state)))
        self._vertical_flow_combo.currentIndexChanged.connect(self._on_vertical_flow_changed)
        self._align_combo.currentIndexChanged.connect(self._on_align_changed)
        self._line_height_spin.valueChanged.connect(
            lambda value: self._save("line_height", float(value))
        )
        self._tracking_spin.valueChanged.connect(lambda value: self._save("tracking", float(value)))
        self._padding_top_spin.valueChanged.connect(lambda v: self._save("padding_top", float(v)))
        self._padding_right_spin.valueChanged.connect(
            lambda v: self._save("padding_right", float(v))
        )
        self._padding_bottom_spin.valueChanged.connect(
            lambda v: self._save("padding_bottom", float(v))
        )
        self._padding_left_spin.valueChanged.connect(lambda v: self._save("padding_left", float(v)))
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._color_button.clicked.connect(self._pick_color)
        highlighted = getattr(self._family_combo, "textHighlighted", None)
        if hasattr(highlighted, "connect"):
            with suppress(Exception):
                highlighted.connect(self._on_family_text_highlighted)

    def _load_initial_values(self) -> None:
        self._block_updates = True
        try:
            settings = self._display_settings()
            self._set_settings(settings)
            self._sync_vertical_toggle_affordance()
            for directory in self._font_dirs():
                self._font_dirs_list.addItem(QListWidgetItem(directory))
            self._reload_custom_fonts()
        finally:
            self._block_updates = False

    def _connect_tool_manager_signal(self) -> None:
        tool_manager = getattr(self._host, "_tool_manager", None) or getattr(
            self._host,
            "tool_manager",
            None,
        )
        signal = getattr(tool_manager, "tool_setting_changed", None) if tool_manager else None
        if hasattr(signal, "connect"):
            with suppress(Exception):
                signal.connect(self._on_tool_setting_changed)
        activated = getattr(tool_manager, "tool_activated", None) if tool_manager else None
        if hasattr(activated, "connect"):
            with suppress(Exception):
                activated.connect(self._on_tool_activated)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def refresh_translations(self) -> None:
        self._retranslate_ui()
        self._sync_vertical_toggle_affordance()

    def _on_tool_setting_changed(self, tool_id: str, key: str, value: Any) -> None:
        del value
        if tool_id not in _TEXT_TOOL_ID_SET:
            return
        if key not in {
            "word_wrap",
            "font_family",
            "font_style",
            "font_size_pt",
            "vertical",
            "vertical_flow",
            "align",
            "line_height",
            "tracking",
            "padding_top",
            "padding_right",
            "padding_bottom",
            "padding_left",
            "color_rgba",
            "typography",
            "annotation_selection",
            "typography_diagnostics",
            "named_style",
            "named_styles",
            "text_flow",
            "text_frame_selected",
            "link_targets",
            "paragraph_columns",
            *GEOMETRY_LIMITS,
        }:
            return
        self._block_updates = True
        try:
            self._set_settings(self._display_settings())
            self._sync_vertical_toggle_affordance()
        finally:
            self._block_updates = False

    def _on_tool_activated(self, tool_id: str) -> None:
        if tool_id not in _TEXT_TOOL_ID_SET:
            self._block_updates = True
            try:
                self._set_settings(self._display_settings())
            finally:
                self._block_updates = False
            return
        self._block_updates = True
        try:
            self._set_settings(self._display_settings())
            self._sync_vertical_toggle_affordance()
        finally:
            self._block_updates = False

    def _display_settings(self) -> TextToolSettings:
        defaults = load_text_tool_settings(self._host.settings)
        manager = self._tool_manager()
        defaults.typography = normalize_typography(
            getattr(manager, "_text_typography_defaults", None)
        )
        tool_id = self._active_text_tool_id(manager)
        getter = getattr(manager, "get_tool_settings", None)
        if tool_id is None or not callable(getter):
            return defaults
        try:
            values = getter(tool_id)
        except Exception:
            logger.debug("Could not read text settings for the font panel", exc_info=True)
            return defaults
        if not isinstance(values, dict):
            return defaults
        displayed = load_text_tool_settings(
            {
                f"text_tool/{field.name}": values.get(field.name, getattr(defaults, field.name))
                for field in fields(TextToolSettings)
            }
        )
        displayed.typography = normalize_typography(values.get("typography"))
        return displayed

    def _set_settings(self, settings: TextToolSettings) -> None:
        self._wrap_check.setChecked(bool(settings.word_wrap))
        if settings.font_family:
            index = self._family_combo.findText(settings.font_family)
            if index < 0:
                self._family_combo.addItem(settings.font_family)
                index = self._family_combo.count() - 1
            self._family_combo.setCurrentIndex(index)
        else:
            self._family_combo.setCurrentIndex(-1)
        self._reload_styles(
            settings.font_family or self._family_combo.currentText(),
            settings.font_style,
            preserve_missing=True,
        )
        self._size_spin.setValue(float(settings.font_size_pt))
        self._vertical_toggle.setChecked(bool(settings.vertical))
        flow_index = self._vertical_flow_combo.findData(
            normalize_vertical_flow(settings.vertical_flow, default=VERTICAL_FLOW_LEFT)
        )
        if flow_index >= 0:
            self._vertical_flow_combo.setCurrentIndex(flow_index)
        align_index = self._align_combo.findData(settings.align)
        if align_index >= 0:
            self._align_combo.setCurrentIndex(align_index)
        self._line_height_spin.setValue(float(settings.line_height))
        self._tracking_spin.setValue(float(settings.tracking))
        self._padding_top_spin.setValue(float(settings.padding_top))
        self._padding_right_spin.setValue(float(settings.padding_right))
        self._padding_bottom_spin.setValue(float(settings.padding_bottom))
        self._padding_left_spin.setValue(float(settings.padding_left))

        r, g, b, a = settings.color_rgba
        self._color = QColor(r, g, b, a)
        self._opacity_slider.setValue(round((a / 255.0) * 100.0))
        self._update_color_button()
        self._opacity_label.setText(f"{self._opacity_slider.value()}%")
        self._sync_vertical_toggle_affordance()

        self._typography_widget.set_typography(settings.typography, vertical=settings.vertical)
        manager = self._tool_manager()
        tool_id = self._active_text_tool_id(manager)
        getter = getattr(manager, "get_tool_settings", None)
        values = getter(tool_id) if tool_id is not None and callable(getter) else {}
        selection = values.get("annotation_selection", {}) if isinstance(values, dict) else {}
        self._annotations_widget.set_selection(selection, vertical=settings.vertical)
        self._named_styles_widget.set_values(values)
        self._geometry_widget.set_values(values)
        self._flow_widget.set_values(values)
        diagnostics = values.get("typography_diagnostics", []) if isinstance(values, dict) else []
        self._typography_widget.set_diagnostics(diagnostics)

    def _reload_font_families(self) -> None:
        previous = self._family_combo.currentText()
        self._family_combo.blockSignals(True)
        self._family_combo.clear()
        families = sorted(QFontDatabase.families())
        self._family_combo.addItems(families)
        if previous:
            index = self._family_combo.findText(previous)
            if index >= 0:
                self._family_combo.setCurrentIndex(index)
        self._family_combo.blockSignals(False)

    def _reload_styles(
        self, family: str, preferred_style: str = "", *, preserve_missing: bool = False
    ) -> None:
        self._style_combo.blockSignals(True)
        self._style_combo.clear()
        styles = QFontDatabase.styles(family) if family else []
        if not styles:
            styles = ["Regular"]
        for style in styles:
            self._style_combo.addItem(style)
        if preferred_style:
            index = self._style_combo.findText(preferred_style)
            if index < 0 and preserve_missing:
                self._style_combo.addItem(preferred_style)
                index = self._style_combo.count() - 1
            if index >= 0:
                self._style_combo.setCurrentIndex(index)
        self._style_combo.blockSignals(False)

    def _on_family_text_changed(self, family: str) -> None:
        self._apply_family_selection(family)

    def _on_family_text_highlighted(self, family: str) -> None:
        self._apply_family_selection(family)

    def _apply_family_selection(self, family: str) -> None:
        if self._block_updates:
            return
        family = str(family or "").strip()
        if not family:
            return
        self._reload_styles(family, self._style_combo.currentText())
        style = self._style_combo.currentText()
        self._save("font_family", family)
        self._save("font_style", style)

    def _on_style_changed(self, _index: int) -> None:
        self._save("font_style", self._style_combo.currentText())

    def _on_align_changed(self, _index: int) -> None:
        align = self._align_combo.currentData()
        if isinstance(align, str):
            self._save("align", align)

    def _on_vertical_flow_changed(self, _index: int) -> None:
        value = self._vertical_flow_combo.currentData()
        if not isinstance(value, str):
            return
        self._save("vertical_flow", normalize_vertical_flow(value, default=VERTICAL_FLOW_LEFT))

    def _on_opacity_changed(self, value: int) -> None:
        if self._block_updates:
            return
        clamped = max(0, min(100, int(value)))
        self._opacity_label.setText(f"{clamped}%")
        self._color.setAlpha(round((clamped / 100.0) * 255.0))
        self._save_color()
        self._update_color_button()

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(self._color, self.window())
        if not chosen.isValid():
            return
        chosen.setAlpha(self._color.alpha())
        self._color = chosen
        self._save_color()
        self._update_color_button()

    def _save_color(self) -> None:
        rgba = [
            int(self._color.red()),
            int(self._color.green()),
            int(self._color.blue()),
            int(self._color.alpha()),
        ]
        self._save("color_rgba", rgba)

    def _update_color_button(self) -> None:
        rgb_hex = self._color.name(QColor.NameFormat.HexRgb).upper()
        self._color_button.setText(rgb_hex)
        swatch = QColor(self._color)
        swatch.setAlpha(255)
        fg = "#000000" if swatch.lightness() > 128 else "#FFFFFF"
        self._color_button.setStyleSheet(
            f"QPushButton{{background:{swatch.name()};color:{fg};padding:4px;}}"
        )

    def _save(self, key: str, value: Any) -> None:
        if self._block_updates:
            return
        manager = self._tool_manager()
        getter = getattr(manager, "get_tool_settings", None)
        tool_id = self._active_text_tool_id(manager)
        values = getter(tool_id) if callable(getter) and tool_id is not None else {}
        range_color = key in {
            "vertical_flow",
            "color_rgba",
            "font_family",
            "font_style",
            "font_size_pt",
            "align",
        } and values.get("color_range_selected", False)
        if (
            key
            not in {
                "typography",
                "ruby",
                "tcy",
                "emphasis",
                "warichu",
                "jidori",
                "named_style",
                "text_flow",
                "link_frames",
                "paragraph_columns",
                *GEOMETRY_LIMITS,
            }
            and not range_color
        ):
            save_text_tool_setting(self._host.settings, key, value)
        dispatched_direct = self._dispatch_tool_setting_direct(key, value)
        self._emit_settings_changed({key: value}, emit_tool_event=not dispatched_direct)

    def _dispatch_tool_setting_direct(self, key: str, value: Any) -> bool:
        tool_manager = self._tool_manager()
        if tool_manager is None:
            return False
        primary_tool_id, secondary_tool_ids = self._resolve_target_tool_ids(tool_manager)
        setter = getattr(tool_manager, "set_tool_setting", None) if tool_manager else None
        if not callable(setter):
            return False
        try:
            setter(primary_tool_id, key, value)
        except Exception:
            return False
        sync = getattr(tool_manager, "sync_tool_setting", None)
        if callable(sync):
            if key == "typography":
                getter = getattr(tool_manager, "get_tool_settings", None)
                if callable(getter):
                    value = getter(primary_tool_id).get(key, value)
            for tool_id in secondary_tool_ids:
                with suppress(Exception):
                    sync(tool_id, key, value)
        return True

    def _tool_manager(self) -> Any | None:
        return getattr(self._host, "_tool_manager", None) or getattr(
            self._host,
            "tool_manager",
            None,
        )

    def _resolve_target_tool_ids(self, tool_manager: Any | None = None) -> tuple[str, list[str]]:
        manager = tool_manager if tool_manager is not None else self._tool_manager()
        if manager is None:
            return (TEXT_TOOL_ID, [TEXT_TOOL_VERTICAL_ID])

        has_tool = getattr(manager, "has_tool", None)
        registered: list[str] = []
        if callable(has_tool):
            for tool_id in TEXT_TOOL_IDS:
                with suppress(Exception):
                    if bool(has_tool(tool_id)):
                        registered.append(tool_id)
        if not registered:
            registered = list(TEXT_TOOL_IDS)

        active_tool_id = self._active_text_tool_id(manager)
        if active_tool_id in registered:
            primary = active_tool_id
        elif TEXT_TOOL_ID in registered:
            primary = TEXT_TOOL_ID
        else:
            primary = registered[0]
        secondary = [tool_id for tool_id in registered if tool_id != primary]
        return (primary, secondary)

    def _active_text_tool_id(self, tool_manager: Any | None = None) -> str | None:
        manager = tool_manager if tool_manager is not None else self._tool_manager()
        if manager is None:
            return None
        getter = getattr(manager, "get_active_tool", None)
        if not callable(getter):
            return None
        with suppress(Exception):
            descriptor = getter()
            tool_id = getattr(descriptor, "id", None)
            if isinstance(tool_id, str) and tool_id in _TEXT_TOOL_ID_SET:
                return tool_id
        return None

    def _sync_vertical_toggle_affordance(self) -> None:
        previous_block = self._block_updates
        self._block_updates = True
        active_tool_id = self._active_text_tool_id()
        try:
            is_vertical = self._vertical_toggle.isChecked()
            if active_tool_id == TEXT_TOOL_VERTICAL_ID:
                self._vertical_toggle.setEnabled(False)
                self._vertical_toggle.setToolTip(
                    self._ts("Vertical Text tool keeps output in vertical writing.")
                    if is_vertical
                    else ""
                )
            elif active_tool_id == TEXT_TOOL_ID:
                self._vertical_toggle.setEnabled(False)
                self._vertical_toggle.setToolTip(
                    self._ts("Text tool keeps output in horizontal writing.")
                    if not is_vertical
                    else ""
                )
            else:
                self._vertical_toggle.setEnabled(True)
                self._vertical_toggle.setToolTip("")
            self._vertical_flow_combo.setEnabled(is_vertical)
            self._vertical_flow_combo.setToolTip("")
            self._update_vertical_labels(vertical=is_vertical)
        finally:
            self._block_updates = previous_block

    def _update_vertical_labels(self, *, vertical: bool) -> None:
        if self._align_combo.count() >= 4:
            if vertical:
                self._align_combo.setItemText(0, self._ts("Top"))
                self._align_combo.setItemText(1, self._ts("Center"))
                self._align_combo.setItemText(2, self._ts("Bottom"))
                self._align_combo.setItemText(3, self._ts("Justify"))
            else:
                self._align_combo.setItemText(0, self._ts("Left"))
                self._align_combo.setItemText(1, self._ts("Center"))
                self._align_combo.setItemText(2, self._ts("Right"))
                self._align_combo.setItemText(3, self._ts("Justify"))
        self._label_line_height.setText(self._ts("Line Height"))

    def _emit_settings_changed(
        self,
        payload: dict[str, Any],
        *,
        emit_tool_event: bool = True,
    ) -> None:
        events = getattr(self._host, "events", None)
        if events is None:
            return
        publish = getattr(events, "publish", None)
        emit = getattr(events, "emit", None)
        legacy_data = {"source": "text_font_panel", "settings": payload}
        primary_tool_id, secondary_tool_ids = self._resolve_target_tool_ids()
        tool_updates: list[dict[str, Any]] = []
        for key, value in payload.items():
            tool_updates.append(
                {
                    "tool_id": primary_tool_id,
                    "key": key,
                    "value": value,
                    "source": "text_font_panel",
                }
            )
            for tool_id in secondary_tool_ids:
                tool_updates.append(
                    {
                        "tool_id": tool_id,
                        "key": key,
                        "value": value,
                        "source": "text_font_panel",
                        "sync_only": True,
                    }
                )
        if callable(publish):
            publish("text_tool.settings_changed", legacy_data)
            if emit_tool_event:
                for update in tool_updates:
                    publish("tool.settings_changed", update)
            return
        if callable(emit):
            emit("text_tool.settings_changed", legacy_data)
            if emit_tool_event:
                for update in tool_updates:
                    emit("tool.settings_changed", update)

    def _font_dirs(self) -> list[str]:
        getter = getattr(self._host.settings, "get", None)
        if not callable(getter):
            return []
        raw = getter("text_tool/custom_font_dirs", [])
        if not isinstance(raw, (list, tuple)):
            return []
        result: list[str] = []
        for value in raw:
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if candidate and candidate not in result:
                result.append(candidate)
        return result

    def _save_font_dirs(self, dirs: Sequence[str]) -> None:
        cleaned: list[str] = []
        for directory in dirs:
            candidate = str(directory).strip()
            if candidate and candidate not in cleaned:
                cleaned.append(candidate)
        save_text_tool_setting(self._host.settings, "custom_font_dirs", cleaned)

    def _add_font_folder(self) -> None:
        start = str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, _ts("Select Font Folder"), start)
        if not directory:
            return
        current = self._font_dirs()
        if directory not in current:
            current.append(directory)
            self._save_font_dirs(current)
            self._font_dirs_list.addItem(QListWidgetItem(directory))
            self._reload_custom_fonts()

    def _remove_selected_folder(self) -> None:
        row = self._font_dirs_list.currentRow()
        if row < 0:
            return
        item = self._font_dirs_list.takeItem(row)
        if item is None:
            return
        current = self._font_dirs()
        removed = item.text()
        self._save_font_dirs([path for path in current if path != removed])
        self._set_status_message("Removed custom font folder.")

    def _set_status_message(self, source_text: str, **kwargs: Any) -> None:
        self._status_source = source_text
        self._status_kwargs = dict(kwargs)
        self._refresh_status_message()

    def _refresh_status_message(self) -> None:
        if not self._status_source:
            self._status_label.clear()
            return
        text = self._ts(self._status_source)
        if self._status_kwargs:
            text = text.format(**self._status_kwargs)
        self._status_label.setText(text)

    def _reload_custom_fonts(self) -> None:
        directories = self._font_dirs()
        loaded, failed = load_fonts_from_dirs(directories)
        previous_block = self._block_updates
        self._block_updates = True
        try:
            self._reload_font_families()
            self._set_settings(self._display_settings())
        finally:
            self._block_updates = previous_block
        if loaded or failed:
            self._set_status_message(
                "Loaded fonts: {loaded}, failed: {failed}",
                loaded=loaded,
                failed=failed,
            )
        else:
            self._set_status_message("No custom fonts loaded.")

    def _retranslate_ui(self) -> None:
        self._typography_widget.refresh_translations()
        self._annotations_widget.refresh_translations()
        self._wrap_check.setText(self._ts("Word Wrap"))
        self._label_font_family.setText(self._ts("Font Family"))
        self._label_font_style.setText(self._ts("Font Style"))
        self._label_font_size.setText(self._ts("Font Size (pt)"))
        self._label_paragraph_flow.setText(self._ts("Paragraph Flow"))
        self._label_alignment.setText(self._ts("Alignment"))
        # _label_line_height is updated by _update_vertical_labels below
        self._label_tracking.setText(self._ts("Tracking"))
        self._label_padding.setText(self._ts("Padding"))
        self._label_pad_top.setText(self._ts("Top"))
        self._label_pad_right.setText(self._ts("Right"))
        self._label_pad_bottom.setText(self._ts("Bottom"))
        self._label_pad_left.setText(self._ts("Left"))
        self._label_color.setText(self._ts("Color"))
        self._label_opacity.setText(self._ts("Opacity"))
        self._label_custom_folders.setText(self._ts("Custom Font Folders"))

        self._wrap_check.setText(self._ts("Word Wrap"))
        self._vertical_toggle.setText(self._ts("Vertical Writing"))
        if self._vertical_flow_combo.count() >= 2:
            self._vertical_flow_combo.setItemText(0, self._ts("Leftward"))
            self._vertical_flow_combo.setItemText(1, self._ts("Rightward"))
        is_vertical = self._vertical_toggle.isChecked()
        self._update_vertical_labels(vertical=is_vertical)

        self._add_folder_button.setText(self._ts("Add Folder"))
        self._remove_folder_button.setText(self._ts("Remove"))
        self._reload_fonts_button.setText(self._ts("Reload Fonts"))
        self._refresh_status_message()

    def _ts(self, text: str) -> str:
        return _ts(text)


class TextFontPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="text_font_panel",
        name=_ts("Text Font Panel"),
        version="0.1.0",
        description=_ts("Font and typography settings for Text Tool"),
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self.host_api: HostAPI | None = None
        self._widget: FontPanelWidget | None = None
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="text_font_panel",
            display_name=_ts("Font"),
            category="tools",
            icon_svg=(
                "<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'>"
                "<rect x='3' y='2' width='18' height='20' rx='3' fill='#1e3a8a'/>"
                "<path d='M7 7h10v2h-4v8h-2V9H7z' fill='white'/>"
                "</svg>"
            ),
            default_position="left",
            default_size=(320, 420),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self.host_api = host_api
        self._subscribe_language_events()

    def deactivate(self) -> None:
        self._unsubscribe_language_events()
        self.host_api = None
        self._widget = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self.host_api is None:
            raise RuntimeError("TextFontPanelPlugin is not activated")
        if self._widget is None:
            self._widget = FontPanelWidget(self.host_api, parent=parent)
        return self._widget

    def _subscribe_language_events(self) -> None:
        if self.host_api is None:
            return
        events = getattr(self.host_api, "events", None)
        on = getattr(events, "on", None)
        if not callable(on):
            return
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        token = on(Events.LANGUAGE_CHANGED, handler)
        self._event_tokens.append(token)

    def _unsubscribe_language_events(self) -> None:
        if self.host_api is None:
            self._event_handlers.clear()
            self._event_tokens.clear()
            return
        events = getattr(self.host_api, "events", None)
        off = getattr(events, "off", None)
        if callable(off):
            for token in self._event_tokens:
                with suppress(Exception):
                    off(Events.LANGUAGE_CHANGED, token)
        self._event_handlers.clear()
        self._event_tokens.clear()

    def _on_language_changed(self, _payload: Any) -> None:
        if self._widget is not None:
            self._widget.refresh_translations()


def create_plugin() -> TextFontPanelPlugin:
    return TextFontPanelPlugin()
