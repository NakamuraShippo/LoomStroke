"""RGB/CMYK input tabs and non-destructive palette display projection."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from loomstroke.core.color_state import ColorDescriptor
from loomstroke.core.native_cmyk.color_descriptor import (
    for_cmyk_profile,
    from_ink_percentages,
    ink_percentages,
)
from loomstroke.plugins.color_manager.color_models import ColorPalette


class ColorModeEditor(QWidget):
    colorSelected = Signal(ColorDescriptor)

    def __init__(self, rgb_controls: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profile: bytes | None = None
        self._descriptor = ColorDescriptor((1, 1, 1, 1))
        self.tabs = QTabWidget(self)
        self.tabs.addTab(rgb_controls, self.tr("RGB"))
        cmyk = QWidget(self.tabs)
        grid = QGridLayout(cmyk)
        self.inputs: dict[str, QDoubleSpinBox] = {}
        for index, channel in enumerate("CMYK"):
            spin = QDoubleSpinBox(cmyk)
            spin.setRange(0, 100)
            spin.setDecimals(2)
            spin.setSuffix(" %")
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setAccessibleName(channel)
            spin.valueChanged.connect(self._on_ink_changed)
            self.inputs[channel] = spin
            grid.addWidget(QLabel(channel, cmyk), index // 2, 2 * (index % 2))
            grid.addWidget(spin, index // 2, 2 * (index % 2) + 1)
        grid.setRowStretch(2, 1)
        self.tabs.addTab(cmyk, self.tr("CMYK"))
        self.tabs.setTabEnabled(1, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

    def set_profile(self, profile: bytes | None, *, reset_mode: bool = False) -> bool:
        if profile == self.profile and not reset_mode:
            return False
        self.profile = profile
        self.tabs.setTabEnabled(1, profile is not None)
        self.tabs.setCurrentIndex(1 if profile is not None else 0)
        return True

    def adapt(self, descriptor: ColorDescriptor) -> ColorDescriptor:
        return for_cmyk_profile(descriptor, self.profile)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.tabs.setTabText(0, self.tr("RGB"))
            self.tabs.setTabText(1, self.tr("CMYK"))

    def set_descriptor(self, descriptor: ColorDescriptor) -> None:
        self._descriptor = descriptor
        percentages = (
            ink_percentages(descriptor, self.profile) if self.profile is not None else None
        )
        for channel, value in zip("CMYK", percentages or (0, 0, 0, 0), strict=True):
            with QSignalBlocker(self.inputs[channel]):
                self.inputs[channel].setValue(value)

    def project_palettes(self, palettes: list[ColorPalette]) -> list[ColorPalette]:
        if self.profile is None:
            return palettes
        return [
            replace(
                palette,
                swatches=[
                    replace(swatch, color=self.adapt(swatch.color)) for swatch in palette.swatches
                ],
            )
            for palette in palettes
        ]

    def _on_ink_changed(self, _value: float) -> None:
        if self.profile is not None:
            percentages = list(ink_percentages(self._descriptor, self.profile) or (0, 0, 0, 0))
            for index, channel in enumerate("CMYK"):
                if self.sender() is self.inputs[channel]:
                    percentages[index] = self.inputs[channel].value()
            self.colorSelected.emit(
                from_ink_percentages(percentages, self.profile, self._descriptor)
            )
