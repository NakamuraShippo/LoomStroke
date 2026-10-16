"""Frame-level column and baseline-grid controls."""

from PySide6.QtCore import QEvent, QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from loomstroke.core.japanese_text.frame_flow import flow_values

from .typography_widget import _ts


class FrameFlowWidget(QWidget):
    requested = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.controls, self.labels = {}, []
        for key, source in (
            ("columns", "Columns"),
            ("gap_em", "Column gap"),
            ("grid_em", "Baseline grid (0: off)"),
        ):
            spin = QSpinBox(self) if key == "columns" else QDoubleSpinBox(self)
            spin.setRange(1 if key == "columns" else 0, 32 if key == "columns" else 100)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._request)
            if key != "columns":
                spin.setSuffix(" em")
            label = QLabel(self)
            self.labels.append((label, spin, source))
            self.controls[key] = spin
            form.addRow(label, spin)
        self._values = flow_values({})
        self.balance = QCheckBox(self)
        self.balance.toggled.connect(self._request)
        form.addRow(self.balance)
        self.paragraph_columns = QSpinBox(self)
        self.paragraph_columns.setRange(0, 32)
        self.paragraph_columns.setKeyboardTracking(False)
        self.paragraph_columns.valueChanged.connect(
            lambda value: self.requested.emit("paragraph_columns", value)
        )
        self.paragraph_label = QLabel(self)
        form.addRow(self.paragraph_label, self.paragraph_columns)
        self.targets = QComboBox(self)
        self.pick_button = QPushButton(self)
        self.pick_button.clicked.connect(
            lambda: self.requested.emit("link_frames", {"action": "pick"})
        )
        form.addRow(self.pick_button)
        self.ordered_button = QPushButton(self)
        self.ordered_button.clicked.connect(
            lambda: self.requested.emit("link_frames", {"action": "ordered"})
        )
        form.addRow(self.ordered_button)
        form.addRow(self.targets)
        self.link_button = QPushButton(self)
        self.unlink_button = QPushButton(self)
        self.link_button.clicked.connect(
            lambda: self.requested.emit(
                "link_frames", {"action": "link", "target": self.targets.currentData()}
            )
        )
        self.unlink_button.clicked.connect(
            lambda: self.requested.emit("link_frames", {"action": "unlink"})
        )
        form.addRow(self.link_button, self.unlink_button)
        self.refresh_translations()

    def set_values(self, values):
        self._values = flow_values(values.get("text_flow"))
        for key, spin in self.controls.items():
            blocker = QSignalBlocker(spin)
            spin.setValue(self._values[key])
            del blocker
        self.setEnabled(bool(values.get("text_frame_selected")))
        blocker = QSignalBlocker(self.balance)
        self.balance.setChecked(self._values["balance"])
        del blocker
        blocker = QSignalBlocker(self.paragraph_columns)
        self.paragraph_columns.setValue(values.get("paragraph_columns", 0))
        del blocker
        self.balance.setEnabled(True)
        self.paragraph_columns.setEnabled(bool(values.get("color_range_selected")))
        self.unlink_button.setEnabled(bool(values.get("linked_text_frame")))
        previous = self.targets.currentData()
        self.targets.clear()
        for target, text in values.get("link_targets", []):
            self.targets.addItem(text or _ts("Empty text frame"), target)
        if self.targets.findData(previous) >= 0:
            self.targets.setCurrentIndex(self.targets.findData(previous))
        self.link_button.setEnabled(self.targets.count() > 0)
        self.pick_button.setEnabled(self.targets.count() > 0)
        self.ordered_button.setEnabled(values.get("selected_frame_count", 0) > 1)

    def _request(self, _value):
        self.requested.emit(
            "text_flow",
            {
                **self._values,
                "balance": self.balance.isChecked(),
                **{key: spin.value() for key, spin in self.controls.items()},
            },
        )

    def refresh_translations(self):
        self.pick_button.setText(_ts("Link text frames"))
        self.ordered_button.setText(_ts("Link selected frames"))
        self.balance.setText(_ts("Balance columns"))
        self.paragraph_label.setText(_ts("Paragraph columns (0: frame, 1: span)"))
        self.link_button.setText(_ts("Link to selected frame"))
        self.unlink_button.setText(_ts("Unlink text frames"))
        self.targets.setAccessibleName(_ts("Next text frame"))
        for label, spin, source in self.labels:
            label.setText(_ts(source))
            spin.setAccessibleName(_ts(source))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
