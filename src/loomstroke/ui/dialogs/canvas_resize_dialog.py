"""Canvas resize dialog with aspect lock and anchor selection."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..startpage_models import CanvasPreset, PresetCategory
from ..startpage_presets_store import load_presets
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.spinbox_sizing import apply_dimension_spinbox_policy

AnchorPos = tuple[float, float]

_ANCHOR_POSITIONS: tuple[tuple[str, AnchorPos], ...] = (
    ("top_left", (0.0, 0.0)),
    ("top", (0.5, 0.0)),
    ("top_right", (1.0, 0.0)),
    ("left", (0.0, 0.5)),
    ("center", (0.5, 0.5)),
    ("right", (1.0, 0.5)),
    ("bottom_left", (0.0, 1.0)),
    ("bottom", (0.5, 1.0)),
    ("bottom_right", (1.0, 1.0)),
)


class CanvasResizeDialog(QDialog):
    """Dialog for resizing the canvas with aspect lock and anchor selection."""

    def __init__(self, width: int, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._categories = load_presets()
        self._category_labels: dict[str, str] = {}
        self._custom_preset_label = "Custom"
        self._updating_from_preset = False
        self._register_translation_templates()
        self._translation_binder.register(self.setWindowTitle, self._ts("Canvas Size"))

        safe_width = max(1, int(width))
        safe_height = max(1, int(height))

        self._aspect_ratio = safe_width / safe_height if safe_height else 1.0
        self._updating = False

        self._width_spin = QSpinBox(self)
        self._width_spin.setRange(1, 16384)
        self._width_spin.setValue(safe_width)
        width_suffix = self._ts(" px")
        self._width_spin.setSuffix(width_suffix)
        self._translation_binder.register(self._width_spin.setSuffix, width_suffix)
        apply_dimension_spinbox_policy(self._width_spin, (1, 16384, safe_width))

        self._height_spin = QSpinBox(self)
        self._height_spin.setRange(1, 16384)
        self._height_spin.setValue(safe_height)
        height_suffix = self._ts(" px")
        self._height_spin.setSuffix(height_suffix)
        self._translation_binder.register(self._height_spin.setSuffix, height_suffix)
        apply_dimension_spinbox_policy(self._height_spin, (1, 16384, safe_height))

        self._lock_button = QToolButton(self)
        self._lock_button.setCheckable(True)
        lock_text = self._ts("Lock")
        self._lock_button.setText(lock_text)
        self._translation_binder.register(self._lock_button.setText, lock_text)
        lock_tip = self._ts("Lock aspect ratio")
        self._lock_button.setToolTip(lock_tip)
        self._translation_binder.register(self._lock_button.setToolTip, lock_tip)
        self._lock_button.setAutoRaise(True)

        self._anchor_group = QButtonGroup(self)
        self._anchor_group.setExclusive(True)
        self._anchor_buttons: dict[str, QToolButton] = {}

        self._build_layout()
        self._connect_signals()

    def canvas_size(self) -> tuple[int, int]:
        return self._width_spin.value(), self._height_spin.value()

    def anchor(self) -> AnchorPos:
        button = self._anchor_group.checkedButton()
        if button is None:
            return (0.5, 0.5)
        return cast(AnchorPos, button.property("anchor_pos"))

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._build_preset_controls(layout)
        self._build_size_controls(layout)
        self._build_anchor_controls(layout)
        self._build_dialog_buttons(layout)
        self._populate_category_combo()

    def _build_preset_controls(self, layout: QVBoxLayout) -> None:
        preset_group = QGroupBox(self)
        preset_title = self._ts("Canvas Presets")
        self._translation_binder.register(preset_group.setTitle, preset_title)
        preset_layout = QFormLayout(preset_group)
        preset_layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        preset_layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._category_combo = QComboBox(preset_group)
        self._preset_combo = QComboBox(preset_group)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        category_label = QLabel(preset_group)
        self._translation_binder.register(category_label.setText, self._ts("Category:"))
        preset_label = QLabel(preset_group)
        self._translation_binder.register(preset_label.setText, self._ts("Preset:"))
        preset_layout.addRow(category_label, self._category_combo)
        preset_layout.addRow(preset_label, self._preset_combo)
        layout.addWidget(preset_group)

    def _build_size_controls(self, layout: QVBoxLayout) -> None:
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        width_label = QLabel(self)
        self._translation_binder.register(width_label.setText, self._ts("Width"))
        size_row.addWidget(width_label)
        size_row.addWidget(self._width_spin)
        size_row.addWidget(self._lock_button)
        height_label = QLabel(self)
        self._translation_binder.register(height_label.setText, self._ts("Height"))
        size_row.addWidget(height_label)
        size_row.addWidget(self._height_spin)
        size_row.addStretch(1)
        layout.addLayout(size_row)

    def _build_anchor_controls(self, layout: QVBoxLayout) -> None:
        anchor_label = QLabel(self)
        self._translation_binder.register(anchor_label.setText, self._ts("Anchor"))
        layout.addWidget(anchor_label)

        anchor_widget = QWidget(self)
        anchor_grid = QGridLayout(anchor_widget)
        anchor_grid.setSpacing(4)
        anchor_grid.setContentsMargins(0, 0, 0, 0)
        anchor_grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        for idx, (anchor_id, anchor_pos) in enumerate(_ANCHOR_POSITIONS):
            row = idx // 3
            col = idx % 3
            button = QToolButton(anchor_widget)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(22, 22)
            button.setProperty("anchor_pos", anchor_pos)
            button.setProperty("anchor_id", anchor_id)
            self._anchor_group.addButton(button)
            self._anchor_buttons[anchor_id] = button
            anchor_grid.addWidget(button, row, col)

        anchor_widget.setStyleSheet(
            "QToolButton {"
            "border: 1px solid #888888;"
            "background: transparent;"
            "}"
            "QToolButton:checked {"
            "border: 2px solid #2b6cb0;"
            "background: #cfe2ff;"
            "}"
        )
        layout.addWidget(anchor_widget)

        center_button = self._anchor_buttons.get("center")
        if center_button is not None:
            center_button.setChecked(True)

    def _build_dialog_buttons(self, layout: QVBoxLayout) -> None:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if self._ok_button is not None:
            self._translation_binder.register(self._ok_button.setText, self._ts("OK"))
        if self._cancel_button is not None:
            self._translation_binder.register(self._cancel_button.setText, self._ts("Cancel"))
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _connect_signals(self) -> None:
        self._width_spin.valueChanged.connect(self._on_width_changed)
        self._height_spin.valueChanged.connect(self._on_height_changed)
        self._width_spin.valueChanged.connect(self._on_dimension_changed)
        self._height_spin.valueChanged.connect(self._on_dimension_changed)
        self._lock_button.toggled.connect(self._on_lock_toggled)

    def _register_translation_templates(self) -> None:
        self._translation_binder.register(self._set_custom_preset_label, self._ts("Custom"))
        self._bind_category_label("ai", self._ts("AI"))
        self._bind_category_label("a", self._ts("A判"))
        self._bind_category_label("b", self._ts("B判"))
        self._bind_category_label("desktop", self._ts("Desktop Screen"))
        self._bind_category_label("mobile", self._ts("Mobile Screen"))
        self._bind_category_label("custom", self._ts("Custom"))

    def _bind_category_label(self, category_id: str, text: str) -> None:
        def setter(translated: str, cid: str = category_id) -> None:
            self._set_category_label(cid, translated)

        self._translation_binder.register(
            setter,
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

    def _populate_preset_combo(self, category: PresetCategory) -> None:
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem(self._custom_preset_label, None)
        for preset in category.presets:
            self._preset_combo.addItem(self._translate_preset_label(preset.label), preset)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

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
        self._updating = True
        try:
            self._width_spin.setValue(int(preset.width_px))
            self._height_spin.setValue(int(preset.height_px))
        finally:
            self._updating = False
            self._updating_from_preset = False
        if self._height_spin.value() > 0:
            self._aspect_ratio = self._width_spin.value() / self._height_spin.value()

    def _on_dimension_changed(self, _: int) -> None:
        if self._updating_from_preset:
            return
        if self._preset_combo.currentIndex() != 0:
            self._preset_combo.setCurrentIndex(0)

    def _on_lock_toggled(self, checked: bool) -> None:
        if not checked:
            return
        width = self._width_spin.value()
        height = self._height_spin.value()
        if height <= 0:
            self._aspect_ratio = 1.0
        else:
            self._aspect_ratio = width / height

    def _on_width_changed(self, value: int) -> None:
        if not self._lock_button.isChecked() or self._updating:
            return
        ratio = self._aspect_ratio or 1.0
        new_height = max(1, round(value / ratio))
        if new_height == self._height_spin.value():
            return
        self._updating = True
        self._height_spin.setValue(new_height)
        self._updating = False

    def _on_height_changed(self, value: int) -> None:
        if not self._lock_button.isChecked() or self._updating:
            return
        ratio = self._aspect_ratio or 1.0
        new_width = max(1, round(value * ratio))
        if new_width == self._width_spin.value():
            return
        self._updating = True
        self._width_spin.setValue(new_width)
        self._updating = False

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        for idx in range(self._category_combo.count()):
            entry = self._category_combo.itemData(idx)
            if isinstance(entry, PresetCategory):
                self._category_combo.setItemText(idx, self._category_label(entry))
        if self._preset_combo.count() > 0:
            self._preset_combo.setItemText(0, self._custom_preset_label)
        for idx in range(1, self._preset_combo.count()):
            preset = self._preset_combo.itemData(idx)
            if isinstance(preset, CanvasPreset):
                self._preset_combo.setItemText(idx, self._translate_preset_label(preset.label))

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("CanvasResizeDialog", text)

    def _translate_preset_label(self, text: str) -> str:
        return self._translate_runtime(text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
