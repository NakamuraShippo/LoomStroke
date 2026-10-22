"""Non-modal controls for an explicitly selected path text object."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from .path_text_body import PathTextBodyEditor


def ts(source):
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("PathText", source, use_fallback=True)


class PathTextControls(QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent, Qt.WindowType.Tool)
        self.controller = controller
        self.controls = {}
        self.labels = {}
        form = QFormLayout(self)
        for key, source in (
            ("start", "Start (%)"),
            ("end", "End (%)"),
            ("offset", "Distance from path (px)"),
        ):
            spin = QDoubleSpinBox(self)
            spin.setRange(-10000, 10000) if key == "offset" else spin.setRange(0, 100)
            spin.setDecimals(2)
            spin.setSingleStep(1)
            spin.setKeyboardTracking(False)
            spin.editingFinished.connect(
                lambda k=key, s=spin: controller.change(
                    {k: s.value() if k == "offset" else s.value() / 100}
                )
            )
            self.controls[key] = spin
            self.labels[key] = QLabel(self)
            self.labels[key].setProperty("source", source)
            form.addRow(self.labels[key], spin)
        self.swap_button = QPushButton(self)
        self.swap_button.clicked.connect(controller.swap_endpoints)
        form.addRow(self.swap_button)
        self._add_smoothing_control(form)
        for key, source in (
            ("reverse", "Reverse direction"),
            ("full_loop", "Use full loop"),
            ("rotate", "Rotate letters along path"),
            ("avoid_overlap", "Avoid overlapping letters"),
        ):
            check = QCheckBox(self)
            check.setProperty("source", source)
            check.toggled.connect(lambda checked, k=key: controller.change({k: checked}))
            self.controls[key] = check
            form.addRow(check)
        combo = QComboBox(self)
        for source, value in (
            ("Start", "left"),
            ("Center", "center"),
            ("End", "right"),
            ("Distribute", "justify"),
        ):
            combo.addItem(source, value)
        combo.activated.connect(lambda i: controller.change({"align": combo.itemData(i)}))
        self.controls["align"] = combo
        self.labels["align"] = QLabel(self)
        self.labels["align"].setProperty("source", "Alignment")
        form.addRow(self.labels["align"], combo)
        self.contour = QComboBox(self)
        self.contour.activated.connect(controller.choose_contour)
        self.labels["contour"] = QLabel(self)
        self.labels["contour"].setProperty("source", "Contour")
        form.addRow(self.labels["contour"], self.contour)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        form.addRow(self.status)
        self.labels["body"] = QLabel(self)
        self.labels["body"].setProperty("source", "Text")
        self.body = PathTextBodyEditor(controller, self)
        form.addRow(self.labels["body"], self.body)
        self.edit_button = QPushButton(self)
        self.edit_button.clicked.connect(controller.edit_body)
        form.addRow(self.edit_button)
        self.retranslate()

    def _add_smoothing_control(self, form):
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(0, 100)
        slider.setProperty(
            "tooltip_source", "0 keeps the original contour; higher values ignore smaller bumps."
        )
        slider.sliderPressed.connect(self.controller.smoothing.begin)
        slider.valueChanged.connect(self.controller.smoothing.preview)
        slider.sliderReleased.connect(self.controller.smoothing.finish)
        self.smoothing_value = QLabel("0", self)
        self.smoothing_value.setMinimumWidth(25)
        slider.valueChanged.connect(self.smoothing_value.setNum)
        layout.addWidget(slider)
        layout.addWidget(self.smoothing_value)
        self.controls["smoothing"] = slider
        self.labels["smoothing"] = QLabel(self)
        self.labels["smoothing"].setProperty("source", "Contour smoothing")
        form.addRow(self.labels["smoothing"], row)

    def refresh(self, settings, overflow):
        for key, control in self.controls.items():
            control.blockSignals(True)
            value = settings.get(
                key,
                {"start": 0, "end": 1, "offset": 0, "align": "left", "rotate": True}.get(
                    key, False
                ),
            )
            if isinstance(control, QSlider):
                if not control.isSliderDown():
                    control.setValue(round(value))
                self.smoothing_value.setNum(round(value))
            elif isinstance(control, QDoubleSpinBox):
                if not control.hasFocus():
                    control.setValue(value if key == "offset" else value * 100)
            elif isinstance(control, QComboBox):
                control.setCurrentIndex(max(0, control.findData(value)))
            else:
                control.setChecked(value)
            control.blockSignals(False)
        closed = bool(settings["path"].get("closed"))
        self.controls["full_loop"].setEnabled(closed)
        self.status.setText(
            ts("Text exceeds the selected interval. The full text is retained.")
            if overflow
            else ts("Drag the start and end handles along the contour.")
        )

    def retranslate(self):
        self.setWindowTitle(ts("Text Along Path"))
        for label in self.labels.values():
            label.setText(ts(label.property("source")))
        for control in self.controls.values():
            if isinstance(control, QCheckBox):
                control.setText(ts(control.property("source")))
            elif isinstance(control, QSlider):
                control.setToolTip(ts(control.property("tooltip_source")))
        for i, source in enumerate(("Start", "Center", "End", "Distribute")):
            self.controls["align"].setItemText(i, ts(source))
        self.edit_button.setText(ts("Edit text"))
        self.swap_button.setText(ts("Swap start and end"))
        self.body.setPlaceholderText(ts("Edit text here. Ctrl+Enter to finish."))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "controls"):
            self.retranslate()

    def reject(self):
        self.controller.close()
