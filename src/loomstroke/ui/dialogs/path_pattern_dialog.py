"""Configure and commit a generated vector pattern on the selected path."""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
)

from ...core.path_pattern_service import PathPatternService
from ..main_window_edit_shape_actions import MainWindowEditShapeActionsCoordinator
from ..translations import translate_with_fallback


class PathPatternDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        form = QFormLayout(self)
        self.labels = []
        self.shape = QComboBox(self)
        for key in ("ellipse", "rounded_rect", "polygon_3"):
            self.shape.addItem("", key)
        self._row(form, "Shape", self.shape)
        self.fields = {}
        for key, source, low, high, default in (
            ("size", "Size (px)", 0.01, 100000, 12),
            ("spacing", "Spacing (px)", 0.01, 100000, 32),
            ("variation", "Variation", 0, 1, 0),
            ("seed", "Random seed", 0, 2147483647, 20261028),
        ):
            spin = QSpinBox(self) if key == "seed" else QDoubleSpinBox(self)
            spin.setRange(low, high)
            if isinstance(spin, QDoubleSpinBox):
                spin.setDecimals(4)
            spin.setValue(default)
            self.fields[key] = spin
            self._row(form, source, spin)
        self.follow = QCheckBox(self)
        self.follow.setChecked(True)
        form.addRow(self.follow)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._retranslate()

    def _row(self, form, source, widget):
        label = QLabel(self)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def accept(self):
        try:
            command = self.service.prepare(
                shape=self.shape.currentData(),
                follow=self.follow.isChecked(),
                **{k: s.value() for k, s in self.fields.items()},
            )
            if not self.service.canvas.submit(command):
                raise ValueError("Command rejected")
        except (ValueError, RuntimeError):
            self.error.setText(
                self._ts("Could not place the pattern. Check the path, spacing and editable layer.")
            )
            return
        super().accept()

    @staticmethod
    def _ts(source):
        return translate_with_fallback("PathPatternDialog", source)

    def _retranslate(self):
        self.setWindowTitle(self._ts("Pattern Along Path"))
        for label, source in self.labels:
            label.setText(self._ts(source))
        for index, source in enumerate(("Ellipse", "Rounded Rectangle", "Triangle")):
            self.shape.setItemText(index, self._ts(source))
        self.follow.setText(self._ts("Follow path rotation"))
        if self.error.text():
            self.error.setText(
                self._ts("Could not place the pattern. Check the path, spacing and editable layer.")
            )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def selected_path_service(window):
    canvas = getattr(getattr(window, "_host_api", None), "canvas", None)
    selection = getattr(canvas, "_vector_selection", None)
    if not isinstance(selection, dict):
        raise ValueError("No selected path")
    object_id = MainWindowEditShapeActionsCoordinator.get_first_selected_vector_id(selection)
    return PathPatternService(canvas, selection.get("layer_id"), object_id)


def can_place_pattern(window):
    try:
        selected_path_service(window)
    except (ValueError, AttributeError):
        return False
    return True


def open_path_pattern(window):
    try:
        service = selected_path_service(window)
    except (ValueError, AttributeError):
        QMessageBox.warning(
            window,
            PathPatternDialog._ts("Pattern Along Path"),
            PathPatternDialog._ts("Select one path on an editable, untransformed vector layer."),
        )
        return
    PathPatternDialog(service, window).exec()
