"""Image Properties dialog (Phase A Wave 1 #15 — DPI UI; CT-09 colour profile)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...io.color_profile_registry import ColorProfileRegistry
from ...io.file_io import ColorProfile
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.spinbox_sizing import fit_spinbox_to_values

#: Stable colour-profile choices returned by :meth:`ImagePropertiesDialog.color_profile_choice`.
PROFILE_KEEP = "keep"
PROFILE_NONE = "none"
PROFILE_SRGB = "srgb"
PROFILE_FILE = "file"


class ImagePropertiesDialog(QDialog):
    """Dialog showing canvas size and allowing DPI / colour profile edits."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        dpi: int,
        color_profile_name: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Image Properties"))

        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._current_profile_name = color_profile_name or None
        self._file_profile: ColorProfile | None = None
        self._registry = ColorProfileRegistry()

        self._dpi_spin = QSpinBox(self)
        self._dpi_spin.setRange(1, 1200)
        self._dpi_spin.setValue(max(1, int(dpi)))
        dpi_suffix = self._ts(" dpi")
        self._dpi_spin.setSuffix(dpi_suffix)
        self._translation_binder.register(self._dpi_spin.setSuffix, dpi_suffix)
        fit_spinbox_to_values(self._dpi_spin, (1, 1200))

        # Live physical-size feedback label — updates when DPI changes.
        self._physical_size_label = QLabel(self)
        self._physical_size_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._profile_combo = QComboBox(self)
        self._profile_combo.addItem("", PROFILE_NONE)
        self._profile_combo.addItem("", PROFILE_SRGB)
        if self._current_profile_name:
            self._profile_combo.addItem("", PROFILE_KEEP)
            self._profile_combo.setCurrentIndex(self._profile_combo.findData(PROFILE_KEEP))
        self._load_profile_button = QPushButton(self)
        self._translation_binder.register(
            self._load_profile_button.setText, self._ts("Load ICC Profile...")
        )

        self._build_layout()
        self._dpi_spin.valueChanged.connect(self._update_physical_size_label)
        self._load_profile_button.clicked.connect(self._load_profile)
        self._update_physical_size_label()
        self._retranslate_profile_items()

    def _update_physical_size_label(self) -> None:
        dpi = max(1, int(self._dpi_spin.value()))
        width_in = self._width / dpi
        height_in = self._height / dpi
        width_mm = width_in * 25.4
        height_mm = height_in * 25.4
        self._physical_size_label.setText(
            f"{width_mm:.2f} x {height_mm:.2f} mm  " f"({width_in:.2f} x {height_in:.2f} in)"
        )

    def dpi(self) -> int:
        return int(self._dpi_spin.value())

    def color_profile_choice(self) -> tuple[str, ColorProfile | None]:
        """``(choice, profile)`` — ``keep`` means unchanged, ``none`` untagged,
        ``srgb`` the built-in sRGB bytes, ``file`` a validated ICC file."""
        choice = str(self._profile_combo.currentData())
        if choice == PROFILE_SRGB:
            info = self._registry.srgb()
            return PROFILE_SRGB, ColorProfile(
                name=info.name,
                data=self._registry.profile_bytes(info.profile_id),
                colorspace=info.colorspace,
            )
        if choice == PROFILE_FILE and self._file_profile is not None:
            return PROFILE_FILE, self._file_profile
        if choice == PROFILE_NONE:
            return PROFILE_NONE, None
        return PROFILE_KEEP, None

    def set_file_profile(self, data: bytes) -> bool:
        """Validate ICC bytes and select them as the new profile (``False`` if invalid)."""
        try:
            info = self._registry.register(data)
        except ValueError:
            return False
        self._file_profile = ColorProfile(
            name=info.name,
            data=self._registry.profile_bytes(info.profile_id),
            colorspace=info.colorspace,
        )
        index = self._profile_combo.findData(PROFILE_FILE)
        if index < 0:
            self._profile_combo.addItem("", PROFILE_FILE)
            index = self._profile_combo.findData(PROFILE_FILE)
        self._retranslate_profile_items()
        self._profile_combo.setCurrentIndex(index)
        return True

    def _load_profile(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            self._translate_runtime("Load ICC Profile"),
            "",
            self._translate_runtime("ICC Profiles (*.icc *.icm);;All Files (*.*)"),
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError:
            data = b""
        if not self.set_file_profile(data):
            QMessageBox.warning(
                self,
                self._translate_runtime("Load ICC Profile"),
                self._translate_runtime("The file is not a valid ICC profile."),
            )

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._build_size_group())
        layout.addWidget(self._build_resolution_group())
        layout.addWidget(self._build_profile_group())
        layout.addWidget(self._build_buttons())

    def _form(self, group: QGroupBox) -> QFormLayout:
        form = QFormLayout(group)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        return form

    def _label(self, source: str, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _build_size_group(self) -> QGroupBox:
        size_group = QGroupBox(self)
        self._translation_binder.register(size_group.setTitle, self._ts("Canvas Size"))
        size_form = self._form(size_group)
        size_form.addRow(self._label("Width:", size_group), QLabel(f"{self._width} px", size_group))
        size_form.addRow(
            self._label("Height:", size_group), QLabel(f"{self._height} px", size_group)
        )
        return size_group

    def _build_resolution_group(self) -> QGroupBox:
        resolution_group = QGroupBox(self)
        self._translation_binder.register(resolution_group.setTitle, self._ts("Resolution"))
        resolution_form = self._form(resolution_group)
        resolution_form.addRow(self._label("DPI:", resolution_group), self._dpi_spin)
        resolution_form.addRow(
            self._label("Physical size:", resolution_group), self._physical_size_label
        )
        return resolution_group

    def _build_profile_group(self) -> QGroupBox:
        profile_group = QGroupBox(self)
        self._translation_binder.register(profile_group.setTitle, self._ts("Colour Profile"))
        profile_form = self._form(profile_group)
        profile_row = QWidget(profile_group)
        row_layout = QHBoxLayout(profile_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(self._profile_combo, 1)
        row_layout.addWidget(self._load_profile_button)
        profile_form.addRow(self._label("Profile:", profile_group), profile_row)
        return profile_group

    def _build_buttons(self) -> QDialogButtonBox:
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            self._translation_binder.register(ok_button.setText, self._ts("OK"))
        if cancel_button is not None:
            self._translation_binder.register(cancel_button.setText, self._ts("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        return buttons

    def _retranslate_profile_items(self) -> None:
        labels: dict[str, str] = {
            PROFILE_NONE: self._translate_runtime("Untagged (treated as sRGB)"),
            PROFILE_SRGB: self._translate_runtime("sRGB (built-in)"),
            PROFILE_KEEP: self._translate_runtime("{name} (document)").format(
                name=self._current_profile_name or ""
            ),
            PROFILE_FILE: self._translate_runtime("{name} (file)").format(
                name=getattr(self._file_profile, "name", "")
            ),
        }
        for index in range(self._profile_combo.count()):
            key = str(self._profile_combo.itemData(index))
            self._profile_combo.setItemText(index, labels.get(key, key))

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._retranslate_profile_items()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ImagePropertiesDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
