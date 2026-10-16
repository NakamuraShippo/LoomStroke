"""Compact, translated fidelity control shared by preview and record editing."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QLabel, QSlider, QVBoxLayout, QWidget

from .panel_workflow import _ts


def handles_label(contour):
    return _ts("Show handles (root / middle / tip)" if contour else "Show handles (root / tip)")


def refresh_contour_controls(panel, mode, fidelity, tip_fidelity=0):
    contour = mode == "contour_rebuild"
    panel.rig_fidelity_control.setVisible(contour)
    panel.rig_fidelity_control.setValue(fidelity)
    panel.rig_tip_fidelity_control.setVisible(contour)
    panel.rig_tip_fidelity_control.setValue(tip_fidelity)
    panel.rig_handles_check.setText(handles_label(contour))
    index = panel.rig_mode_combo.findData(mode)
    if index >= 0:
        panel.rig_mode_combo.setCurrentIndex(index)
    panel.rig_advanced_section.setVisible(not contour)
    # Old records retain their renderer; changing it is not a hidden migration.
    item = panel.rig_mode_combo.model().item(panel.rig_mode_combo.findData("contour_rebuild"))
    if item is not None:
        item.setEnabled(contour)


class FidelityControl(QWidget):
    valueChanged = Signal(float)

    def __init__(self, *, tip=False):
        super().__init__()
        self.tip = tip
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        # One undoable parameter change on release, not one per mouse pixel.
        self.slider.setTracking(False)
        layout.addWidget(self.label)
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self._changed)
        self.retranslate()

    def _changed(self, value):
        self.retranslate()
        self.valueChanged.emit(value / 100.0)

    def value(self):
        return self.slider.value() / 100.0

    def setValue(self, value):  # noqa: N802
        self.slider.setValue(round(value * 100))
        self.retranslate()

    def retranslate(self):
        source = "Tail tip fidelity: {value}%" if self.tip else "Body fidelity: {value}%"
        self.label.setText(_ts(source).format(value=self.slider.value()))
        if self.tip:
            self.setToolTip(
                _ts("Retain the source tip contour only. Body and root connection are unchanged.")
            )
            return
        self.setToolTip(
            _ts(
                "0: smooth outline. 100: retain body shape and line-width variation. "
                "Joint stays smooth; texture is not preserved."
            )
        )

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
