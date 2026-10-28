"""New document dialog for StartPage."""

from __future__ import annotations

import logging
from typing import Any, cast
from uuid import uuid4

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ..startpage_models import (
    BackgroundSpec,
    CanvasPreset,
    PresetCategory,
    StartPageCanvasSpec,
)
from ..startpage_presets_store import load_presets, save_presets
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.new_document_color_controls import NewDocumentColorControls
from ..widgets.spinbox_sizing import apply_spinbox_entry_policy

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 72
_DEFAULT_WIDTH = 832
_DEFAULT_HEIGHT = 1216
_WHITE_RGBA = (255, 255, 255, 255)


class _NewDocumentSpinBox(QWidget):
    """Numeric input with external step buttons for the New Document dialog."""

    valueChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spin = QSpinBox(self)
        apply_spinbox_entry_policy(self._spin)
        self._spin.valueChanged.connect(self.valueChanged.emit)

        self._up_button = QToolButton(self)
        self._up_button.setArrowType(Qt.ArrowType.UpArrow)
        self._up_button.setAutoRepeat(True)
        self._up_button.setAutoRepeatDelay(400)
        self._up_button.setAutoRepeatInterval(80)
        self._up_button.clicked.connect(lambda _checked=False: self._spin.stepUp())

        self._down_button = QToolButton(self)
        self._down_button.setArrowType(Qt.ArrowType.DownArrow)
        self._down_button.setAutoRepeat(True)
        self._down_button.setAutoRepeatDelay(400)
        self._down_button.setAutoRepeatInterval(80)
        self._down_button.clicked.connect(lambda _checked=False: self._spin.stepDown())

        for button in (self._up_button, self._down_button):
            button.setFixedSize(20, 11)
            button.setStyleSheet("QToolButton { padding: 0px; }")

        button_layout = QVBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(1)
        button_layout.addWidget(self._up_button)
        button_layout.addWidget(self._down_button)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._spin, 1)
        layout.addLayout(button_layout)
        self.setFocusProxy(self._spin)

    def setRange(self, minimum: int, maximum: int) -> None:
        self._spin.setRange(minimum, maximum)

    def setMinimum(self, minimum: int) -> None:
        self._spin.setMinimum(minimum)

    def setMaximum(self, maximum: int) -> None:
        self._spin.setMaximum(maximum)

    def setSingleStep(self, step: int) -> None:
        self._spin.setSingleStep(step)

    def singleStep(self) -> int:
        return int(self._spin.singleStep())

    def setValue(self, value: int) -> None:
        self._spin.setValue(value)

    def value(self) -> int:
        return int(self._spin.value())

    def stepUp(self) -> None:
        self._spin.stepUp()

    def stepDown(self) -> None:
        self._spin.stepDown()


class NewDocumentDialog(QDialog):
    """Dialog for creating a new document."""

    def __init__(self, settings: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._categories = load_presets()
        self._category_labels: dict[str, str] = {}
        self._custom_preset_label = "Custom"
        self._preset_name_title = self._ts("Preset Name")
        self._preset_name_label = self._ts("Preset name:")
        self._spec: StartPageCanvasSpec | None = None
        self._custom_color = QColor(255, 255, 255, 255)
        self._updating_from_preset = False
        self._register_translation_templates()
        self._build_ui()
        self._load_last_canvas()
        self._subscribe_language_events()

    def spec(self) -> StartPageCanvasSpec | None:
        return self._spec

    def _build_ui(self) -> None:
        self._translation_binder.register(self.setWindowTitle, self._ts("New Document"))
        outer = QVBoxLayout(self)
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        preset_group = self._build_preset_group()
        size_group = self._build_size_group()
        dpi_group = self._build_dpi_group()
        background_group = self._build_background_group()
        self._color_controls = NewDocumentColorControls(self)
        buttons = self._build_dialog_buttons()
        self._color_controls.validity_changed.connect(self._ok_button.setEnabled)
        layout.addWidget(preset_group)
        layout.addWidget(size_group)
        layout.addWidget(dpi_group)
        layout.addWidget(self._color_controls)
        layout.addWidget(background_group)
        layout.addStretch(1)
        self._form_scroll = QScrollArea(self)
        self._form_scroll.setWidgetResizable(True)
        self._form_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._form_scroll.setWidget(content)
        outer.addWidget(self._form_scroll, 1)
        outer.addWidget(buttons)
        # QWidget.screen() attaches PySide's shared screen wrapper to this
        # transient dialog. Deleting it then invalidates that wrapper for the
        # rest of the application, although the native screen is still alive.
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center()))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        available_height = max(300, screen.availableGeometry().height() - 64) if screen else 650
        self.resize(520, min(650, available_height))

        self._populate_category_combo()

    def _build_preset_group(self) -> QGroupBox:
        preset_group = QGroupBox()
        preset_title = self._ts("Canvas Presets")
        self._translation_binder.register(preset_group.setTitle, preset_title)
        preset_layout = QFormLayout(preset_group)
        preset_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        preset_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._category_combo = QComboBox()
        self._preset_combo = QComboBox()
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self._add_labeled_row(preset_layout, self._ts("Category:"), self._category_combo)
        self._add_labeled_row(preset_layout, self._ts("Preset:"), self._preset_combo)
        return preset_group

    def _build_size_group(self) -> QGroupBox:
        size_group = QGroupBox()
        size_title = self._ts("Canvas Size")
        self._translation_binder.register(size_group.setTitle, size_title)
        size_layout = QFormLayout(size_group)
        size_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        size_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._width_spin = _NewDocumentSpinBox()
        self._width_spin.setRange(1, 16384)
        self._width_spin.setSingleStep(8)
        self._height_spin = _NewDocumentSpinBox()
        self._height_spin.setRange(1, 16384)
        self._height_spin.setSingleStep(8)
        self._width_spin.valueChanged.connect(self._on_dimension_changed)
        self._height_spin.valueChanged.connect(self._on_dimension_changed)
        self._add_labeled_row(size_layout, self._ts("Width (px):"), self._width_spin)
        self._add_labeled_row(size_layout, self._ts("Height (px):"), self._height_spin)

        self._add_preset_button = QPushButton()
        add_text = self._ts("Add to Presets")
        self._translation_binder.register(self._add_preset_button.setText, add_text)
        self._add_preset_button.clicked.connect(self._add_current_preset)
        size_layout.addRow("", self._add_preset_button)

        return size_group

    def _build_dpi_group(self) -> QGroupBox:
        dpi_group = QGroupBox()
        dpi_title = self._ts("DPI")
        self._translation_binder.register(dpi_group.setTitle, dpi_title)
        dpi_layout = QFormLayout(dpi_group)
        dpi_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        dpi_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._dpi_spin = _NewDocumentSpinBox()
        self._dpi_spin.setRange(1, 1200)
        self._add_labeled_row(dpi_layout, self._ts("DPI:"), self._dpi_spin)
        return dpi_group

    def _build_background_group(self) -> QGroupBox:
        background_group = QGroupBox()
        background_title = self._ts("Background")
        self._translation_binder.register(background_group.setTitle, background_title)
        background_layout = QVBoxLayout(background_group)
        background_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._background_buttons = QButtonGroup(self)
        self._bg_transparent = QRadioButton()
        transparent_text = self._ts("Transparent")
        self._translation_binder.register(self._bg_transparent.setText, transparent_text)
        self._bg_white = QRadioButton()
        white_text = self._ts("White")
        self._translation_binder.register(self._bg_white.setText, white_text)
        self._bg_custom = QRadioButton()
        custom_color_text = self._ts("Custom Color")
        self._translation_binder.register(self._bg_custom.setText, custom_color_text)

        self._background_buttons.addButton(self._bg_transparent)
        self._background_buttons.addButton(self._bg_white)
        self._background_buttons.addButton(self._bg_custom)

        self._custom_color_button = QPushButton()
        pick_color_text = self._ts("Pick Color...")
        self._translation_binder.register(self._custom_color_button.setText, pick_color_text)
        self._custom_color_button.clicked.connect(self._pick_custom_color)

        self._custom_color_preview = QLabel(" ")
        self._custom_color_preview.setFixedHeight(18)
        self._custom_color_preview.setFixedWidth(80)
        self._custom_color_preview.setStyleSheet("border: 1px solid #999;")

        background_layout.addWidget(self._bg_transparent)
        background_layout.addWidget(self._bg_white)
        background_layout.addWidget(self._bg_custom)
        background_layout.addWidget(self._custom_color_button)
        background_layout.addWidget(self._custom_color_preview)
        return background_group

    def _build_dialog_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if self._ok_button is not None:
            self._translation_binder.register(self._ok_button.setText, self._ts("OK"))
        if self._cancel_button is not None:
            self._translation_binder.register(self._cancel_button.setText, self._ts("Cancel"))
        return buttons

    def _register_translation_templates(self) -> None:
        self._translation_binder.register(self._set_custom_preset_label, self._ts("Custom"))
        self._bind_category_label("ai", self._ts("AI"))
        self._bind_category_label("a", self._ts("A判"))
        self._bind_category_label("b", self._ts("B判"))
        self._bind_category_label("desktop", self._ts("Desktop Screen"))
        self._bind_category_label("mobile", self._ts("Mobile Screen"))
        self._bind_category_label("custom", self._ts("Custom"))

    def _add_labeled_row(self, layout: QFormLayout, text: str, widget: QWidget) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, widget)

    def _bind_category_label(self, category_id: str, text: str) -> None:
        def set_category_label(translated: str, cid: str = category_id) -> None:
            self._set_category_label(cid, translated)

        self._translation_binder.register(
            set_category_label,
            text,
        )

    def _set_category_label(self, category_id: str, text: str) -> None:
        self._category_labels[category_id] = text

    def _set_custom_preset_label(self, text: str) -> None:
        self._custom_preset_label = text

    def _category_label(self, category: PresetCategory) -> str:
        return self._category_labels.get(category.id, category.label)

    def _populate_category_combo(self) -> None:
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        for category in self._categories:
            self._category_combo.addItem(self._category_label(category), category)
        self._category_combo.blockSignals(False)
        if self._categories:
            self._category_combo.setCurrentIndex(0)
            self._populate_preset_combo(self._categories[0])

    def _load_last_canvas(self) -> None:
        default_payload = {
            "width_px": _DEFAULT_WIDTH,
            "height_px": _DEFAULT_HEIGHT,
            "dpi": _DEFAULT_DPI,
            "background": {"kind": "solid", "color_rgba": list(_WHITE_RGBA)},
            "auto_fix_multiple_of_64": False,
        }
        payload = self._settings.get("plugins/ui_startpage/last_canvas", default_payload)
        if not isinstance(payload, dict):
            payload = default_payload
        try:
            spec = StartPageCanvasSpec.from_payload(payload)
        except Exception:
            spec = StartPageCanvasSpec.from_payload(default_payload)

        self._width_spin.setValue(max(1, int(spec.width_px)))
        self._height_spin.setValue(max(1, int(spec.height_px)))
        self._dpi_spin.setValue(max(1, int(spec.dpi)))

        bg = spec.background
        if bg.kind == "transparent":
            self._bg_transparent.setChecked(True)
        elif bg.color_rgba == _WHITE_RGBA:
            self._bg_white.setChecked(True)
        else:
            self._bg_custom.setChecked(True)
            if bg.color_rgba:
                self._custom_color = QColor(*bg.color_rgba)
        self._update_custom_color_preview()

    def _populate_preset_combo(self, category: PresetCategory) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem(self._custom_preset_label, None)
        for preset in category.presets:
            self._preset_combo.addItem(self._translate_preset_label(preset.label), preset)
        self._preset_combo.setCurrentIndex(1 if category.presets else 0)
        self._preset_combo.blockSignals(False)
        if category.presets:
            self._apply_preset(category.presets[0])

    def _on_category_changed(self, index: int) -> None:
        category = self._category_combo.itemData(index)
        if isinstance(category, PresetCategory):
            self._populate_preset_combo(category)

    def _on_preset_changed(self, index: int) -> None:
        preset = self._preset_combo.itemData(index)
        if isinstance(preset, CanvasPreset):
            self._apply_preset(preset)

    def _apply_preset(self, preset: CanvasPreset) -> None:
        self._updating_from_preset = True
        try:
            self._width_spin.setValue(int(preset.width_px))
            self._height_spin.setValue(int(preset.height_px))
        finally:
            self._updating_from_preset = False

    def _on_dimension_changed(self, _: int) -> None:
        if self._updating_from_preset:
            return
        if self._preset_combo.currentIndex() != 0:
            self._preset_combo.setCurrentIndex(0)

    def _pick_custom_color(self) -> None:
        dialog = QColorDialog(self._custom_color, self)
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            color = dialog.currentColor()
            if color.isValid():
                self._custom_color = color
                self._update_custom_color_preview()

    def _update_custom_color_preview(self) -> None:
        rgba = cast(tuple[int, int, int, int], self._custom_color.getRgb())
        self._custom_color_preview.setStyleSheet(
            "border: 1px solid #999; "
            f"background-color: rgba({rgba[0]}, {rgba[1]}, {rgba[2]}, {rgba[3]});"
        )

    def _add_current_preset(self) -> None:
        category = self._category_combo.itemData(self._category_combo.currentIndex())
        if not isinstance(category, PresetCategory):
            return
        width = int(self._width_spin.value())
        height = int(self._height_spin.value())

        default_label = f"{width} x {height}"
        title_text = self._translate_runtime("Preset Name")
        prompt_text = self._translate_runtime("Preset name:")
        label, ok = QInputDialog.getText(
            self,
            title_text,
            prompt_text,
            text=default_label,
        )
        if not ok:
            return
        label = label.strip() or default_label
        existing_labels = {preset.label for preset in category.presets}
        label = self._unique_preset_label(label, existing_labels)
        new_preset = CanvasPreset(
            id=f"user_{uuid4().hex[:8]}",
            label=label,
            width_px=width,
            height_px=height,
        )

        updated_categories: list[PresetCategory] = []
        selected_category: PresetCategory | None = None
        for entry in self._categories:
            if entry.id == category.id:
                presets = list(entry.presets)
                presets.append(new_preset)
                selected_category = PresetCategory(
                    id=entry.id,
                    label=entry.label,
                    presets=tuple(presets),
                )
                updated_categories.append(selected_category)
            else:
                updated_categories.append(entry)

        if selected_category is None:
            return

        save_presets(updated_categories)
        self._categories = updated_categories
        self._reload_category_combo(selected_category.id, new_preset.id)

    def _reload_category_combo(self, category_id: str, preset_id: str | None) -> None:
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        for entry in self._categories:
            self._category_combo.addItem(self._category_label(entry), entry)
        self._category_combo.blockSignals(False)

        target_index = 0
        for idx in range(self._category_combo.count()):
            entry = self._category_combo.itemData(idx)
            if isinstance(entry, PresetCategory) and entry.id == category_id:
                target_index = idx
                break
        self._category_combo.setCurrentIndex(target_index)

        category = self._category_combo.itemData(target_index)
        if isinstance(category, PresetCategory):
            self._populate_preset_combo(category)
            if preset_id:
                for idx in range(self._preset_combo.count()):
                    item = self._preset_combo.itemData(idx)
                    if isinstance(item, CanvasPreset) and item.id == preset_id:
                        self._preset_combo.setCurrentIndex(idx)
                        break

    def _update_category_combo_labels(self) -> None:
        for idx in range(self._category_combo.count()):
            entry = self._category_combo.itemData(idx)
            if isinstance(entry, PresetCategory):
                self._category_combo.setItemText(idx, self._category_label(entry))

    def _update_preset_combo_labels(self) -> None:
        if self._preset_combo.count() > 0:
            self._preset_combo.setItemText(0, self._custom_preset_label)
        for idx in range(1, self._preset_combo.count()):
            preset = self._preset_combo.itemData(idx)
            if isinstance(preset, CanvasPreset):
                self._preset_combo.setItemText(idx, self._translate_preset_label(preset.label))

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus, "subscribe", None
        )
        if callable(subscribe):
            token = subscribe(Events.LANGUAGE_CHANGED, handler)
            self._event_tokens.append(token)

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._update_category_combo_labels()
        self._update_preset_combo_labels()
        self._color_controls.refresh_translations()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("NewDocumentDialog", text)

    def _translate_preset_label(self, text: str) -> str:
        return self._translate_runtime(text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    @staticmethod
    def _unique_preset_label(base: str, existing: set[str]) -> str:
        if base not in existing:
            return base
        suffix = 2
        while True:
            candidate = f"{base} ({suffix})"
            if candidate not in existing:
                return candidate
            suffix += 1

    def _background_spec(self) -> BackgroundSpec:
        if self._bg_transparent.isChecked():
            return BackgroundSpec(kind="transparent", color_rgba=None)
        if self._bg_white.isChecked():
            return BackgroundSpec(kind="solid", color_rgba=_WHITE_RGBA)
        color = cast(tuple[int, int, int, int], self._custom_color.getRgb())
        return BackgroundSpec(kind="solid", color_rgba=(color[0], color[1], color[2], color[3]))

    def _accept(self) -> None:
        if not self._color_controls.is_valid():
            return
        width = int(self._width_spin.value())
        height = int(self._height_spin.value())
        dpi = int(self._dpi_spin.value())
        spec = StartPageCanvasSpec(
            width_px=width,
            height_px=height,
            dpi=dpi,
            background=self._background_spec(),
            auto_fix_multiple_of_64=False,
            color=self._color_controls.color_spec(),
        )
        self._spec = spec
        try:
            defaults = spec.to_payload()
            defaults.pop("color", None)
            self._settings.set("plugins/ui_startpage/last_canvas", defaults)
        except Exception as exc:
            logger.debug("Failed to persist StartPage settings: %s", exc)
        self.accept()


__all__ = ["NewDocumentDialog"]
