"""Layer-based Vibe Transfer inputs for the NovelAI panel."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class VibeReferencesWidget(QWidget):
    """Keep per-image Vibe controls together without changing reference IDs."""

    changed = Signal()

    def __init__(self, translate: Callable[[str], str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tr = translate
        self._layers: list[tuple[str, str]] = []
        self._rows: list[tuple[QWidget, QComboBox, QDoubleSpinBox, QDoubleSpinBox]] = []
        self._layout = QVBoxLayout(self)
        self.title = QLabel(self._tr("NovelAI Potion (Vibe Transfer)"), self)
        self._layout.addWidget(self.title)
        self.add_button = QPushButton(self._tr("Add potion"), self)
        self.add_button.clicked.connect(self.add_row)
        self._layout.addWidget(self.add_button)
        self.add_row()

    def set_layers(self, layers: list[tuple[str, str]]) -> None:
        self._layers = layers
        for _, combo, _, _ in self._rows:
            selected = combo.currentData()
            combo.clear()
            combo.addItem(self._tr("Nothing"), None)
            for label, layer_id in layers:
                combo.addItem(label, layer_id)
            combo.setCurrentIndex(max(0, combo.findData(selected)))

    def add_row(self) -> None:
        if len(self._rows) >= 16:
            return
        row = QWidget(self)
        layout = QFormLayout(row)
        combo = QComboBox(row)
        combo.setObjectName("novelaiPotionLayer")
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.currentIndexChanged.connect(lambda _index: self.changed.emit())
        combo.addItem(self._tr("Nothing"), None)
        for label, layer_id in self._layers:
            combo.addItem(label, layer_id)
        strength = QDoubleSpinBox(row)
        strength.setObjectName("novelaiPotionStrength")
        strength.setRange(0, 1)
        strength.setSingleStep(0.05)
        strength.setValue(0.5)
        extracted = QDoubleSpinBox(row)
        extracted.setObjectName("novelaiPotionInformationExtracted")
        extracted.setRange(0, 1)
        extracted.setSingleStep(0.05)
        extracted.setValue(1.0)
        remove = QPushButton(self._tr("Remove"), row)
        remove.clicked.connect(lambda: self._remove_row(row))
        layout.addRow(combo)
        layout.addRow(QLabel(self._tr("Strength"), row), strength)
        layout.addRow(QLabel(self._tr("Information Extracted"), row), extracted)
        layout.addRow(remove)
        self._layout.insertWidget(len(self._rows) + 1, row)
        self._rows.append((row, combo, strength, extracted))
        self.add_button.setEnabled(len(self._rows) < 16)
        self.changed.emit()

    def _remove_row(self, row: QWidget) -> None:
        if len(self._rows) == 1:
            return
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        row.deleteLater()
        self.add_button.setEnabled(True)
        self.changed.emit()

    def values(self) -> list[dict[str, str | float]]:
        return [
            {
                "layer_id": str(combo.currentData()),
                "strength": strength.value(),
                "information_extracted": extracted.value(),
            }
            for _, combo, strength, extracted in self._rows
            if combo.currentData() is not None
        ]

    def retranslate(self) -> None:
        self.title.setText(self._tr("NovelAI Potion (Vibe Transfer)"))
        self.add_button.setText(self._tr("Add potion"))
        for row, combo, _, _ in self._rows:
            combo.setItemText(0, self._tr("Nothing"))
            labels = row.findChildren(QLabel)
            labels[0].setText(self._tr("Strength"))
            labels[1].setText(self._tr("Information Extracted"))
            row.findChild(QPushButton).setText(self._tr("Remove"))
