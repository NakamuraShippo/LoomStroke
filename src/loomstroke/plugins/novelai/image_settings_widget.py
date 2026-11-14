"""NovelAI image resolution and batch controls."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ImageSettingsWidget(QGroupBox):
    """Keep NovelAI's size tier, orientation, and output count together."""

    SIZES: ClassVar[dict[str, dict[str, tuple[int, int]]]] = {
        "Small": {"landscape": (768, 512), "portrait": (512, 768), "square": (640, 640)},
        "Normal": {
            "landscape": (1216, 832),
            "portrait": (832, 1216),
            "square": (1024, 1024),
        },
        "Large": {
            "landscape": (1536, 1024),
            "portrait": (1024, 1536),
            "square": (1472, 1472),
        },
        "Wallpaper": {"landscape": (1920, 1088), "portrait": (1088, 1920)},
    }
    ORIENTATIONS = ("landscape", "portrait", "square")

    def __init__(  # noqa: PLR0915
        self, translate: Callable[[str], str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._tr = translate
        self.setObjectName("novelaiImageSettings")
        layout = QVBoxLayout(self)

        size_row = QHBoxLayout()
        self.size_label = QLabel(self)
        self.width = QSpinBox(self)
        self.height = QSpinBox(self)
        for spin, value in ((self.width, 832), (self.height, 1216)):
            spin.setRange(64, 4096)
            spin.setSingleStep(64)
            spin.setValue(value)
            spin.setFixedWidth(64)
            spin.valueChanged.connect(self._sync_from_numbers)
        self.swap_resolution = QPushButton("x", self)
        self.swap_resolution.setFixedWidth(24)
        self.swap_resolution.clicked.connect(self.swap_size)
        size_row.addWidget(self.size_label)
        size_row.addStretch()
        size_row.addWidget(self.width)
        size_row.addWidget(self.swap_resolution)
        size_row.addWidget(self.height)
        layout.addLayout(size_row)

        preset_row = QHBoxLayout()
        self.resolution = QComboBox(self)
        for tier in (*self.SIZES, "Custom"):
            self.resolution.addItem(tier, tier)
        self.resolution.setCurrentIndex(self.resolution.findData("Normal"))
        self.resolution.currentIndexChanged.connect(self._apply_preset)
        preset_row.addWidget(self.resolution, 2)
        self.orientation_buttons = QButtonGroup(self)
        self.orientation_buttons.setExclusive(True)
        self.orientation_controls = {}
        for orientation in self.ORIENTATIONS:
            button = QToolButton(self)
            button.setCheckable(True)
            button.setObjectName(f"novelaiOrientation_{orientation}")
            button.clicked.connect(lambda _checked, value=orientation: self.set_orientation(value))
            self.orientation_buttons.addButton(button)
            self.orientation_controls[orientation] = button
            preset_row.addWidget(button, 1)
        self.orientation_controls["portrait"].setChecked(True)
        layout.addLayout(preset_row)

        self.count_label = QLabel(self)
        layout.addWidget(self.count_label)
        count_row = QHBoxLayout()
        self.count_buttons = QButtonGroup(self)
        self.count_buttons.setExclusive(True)
        self.count_controls = {}
        for count in range(1, 5):
            button = QToolButton(self)
            button.setText(str(count))
            button.setCheckable(True)
            button.setObjectName(f"novelaiCount_{count}")
            self.count_buttons.addButton(button, count)
            self.count_controls[count] = button
            count_row.addWidget(button)
        count_row.addStretch()
        self.count_controls[1].setChecked(True)
        layout.addLayout(count_row)
        self.retranslate()

    def current_size(self) -> tuple[int, int]:
        return self.width.value(), self.height.value()

    def count(self) -> int:
        return self.count_buttons.checkedId()

    def set_count(self, count: int) -> None:
        self.count_controls[count].setChecked(True)

    def set_tier(self, tier: str) -> None:
        self.resolution.setCurrentIndex(self.resolution.findData(tier))
        self._apply_preset()

    def set_orientation(self, orientation: str) -> None:
        self.orientation_controls[orientation].setChecked(True)
        self._apply_preset()

    def _apply_preset(self) -> None:
        tier = self.resolution.currentData()
        orientation = self._orientation()
        self.orientation_controls["square"].setEnabled(tier != "Wallpaper")
        if tier == "Wallpaper" and orientation == "square":
            orientation = "portrait"
            self.orientation_controls[orientation].setChecked(True)
        size = self.SIZES.get(tier, {}).get(orientation)
        if size is None:
            return
        for spin, value in zip((self.width, self.height), size, strict=True):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _orientation(self) -> str:
        for orientation, button in self.orientation_controls.items():
            if button.isChecked():
                return orientation
        return "square"

    def _sync_from_numbers(self) -> None:
        size = self.current_size()
        for tier, orientations in self.SIZES.items():
            for orientation, preset_size in orientations.items():
                if size == preset_size:
                    self.resolution.blockSignals(True)
                    self.resolution.setCurrentIndex(self.resolution.findData(tier))
                    self.resolution.blockSignals(False)
                    self.orientation_controls[orientation].setChecked(True)
                    return
        self.resolution.blockSignals(True)
        self.resolution.setCurrentIndex(self.resolution.findData("Custom"))
        self.resolution.blockSignals(False)

    def swap_size(self) -> None:
        width, height = self.current_size()
        self.width.blockSignals(True)
        self.height.blockSignals(True)
        self.width.setValue(height)
        self.height.setValue(width)
        self.width.blockSignals(False)
        self.height.blockSignals(False)
        self._sync_from_numbers()

    def retranslate(self) -> None:
        self.setTitle(self._tr("Image Settings"))
        self.size_label.setText(self._tr("Image Resolution"))
        self.swap_resolution.setToolTip(self._tr("Swap width and height"))
        self.count_label.setText(self._tr("Number of Images"))
        for index, tier in enumerate((*self.SIZES, "Custom")):
            self.resolution.setItemText(index, self._tr(tier))
        for orientation, button in self.orientation_controls.items():
            symbol = {"landscape": "\u25ad", "portrait": "\u25af", "square": "\u25a1"}[orientation]
            button.setText(symbol)
            button.setToolTip(self._tr(orientation.capitalize()))
            button.setAccessibleName(self._tr(orientation.capitalize()))
