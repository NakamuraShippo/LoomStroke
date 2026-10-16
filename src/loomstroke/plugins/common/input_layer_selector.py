"""Numbered, compacting input layer rows matching the NanoBanana panel."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from loomstroke.ui.translations import translated_literal_domain


class InputLayerSelector(QWidget):
    """Keep ordered layer IDs separate from labels and translated placeholders."""

    def __init__(self, max_inputs: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_inputs = max_inputs
        self._choices: list[tuple[str, str]] = []
        self.combos: list[QComboBox] = []
        self._numbers: list[QLabel] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        for index in range(max_inputs):
            number = QLabel(str(index + 1), self)
            combo = QComboBox(self)
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(6)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.addItem("", None)
            combo.currentIndexChanged.connect(self._normalize)
            grid.addWidget(number, index, 0)
            grid.addWidget(combo, index, 1)
            self._numbers.append(number)
            self.combos.append(combo)
        self.refresh_translations()
        self.set_selected([])

    @staticmethod
    def _text(source: str) -> str:
        # Reuse the existing layer-input catalogue so both panels use the same labels.
        return translated_literal_domain("NanoBananaPanel", source, use_fallback=True)

    def refresh_translations(self) -> None:
        self.label.setText(
            self._text("Input Layers (select up to {count}):").format(count=self._max_inputs)
        )
        for combo in self.combos:
            combo.setItemText(0, self._text("Nothing"))

    def selected(self) -> list[str]:
        values: list[str] = []
        for combo in self.combos:
            value = combo.currentData()
            if isinstance(value, str) and value and value not in values:
                values.append(value)
        return values

    def set_choices(self, choices: list[tuple[str, str]]) -> None:
        previous = self.selected()
        self._choices = list(choices)
        for combo in self.combos:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self._text("Nothing"), None)
            for layer_id, display in choices:
                combo.addItem(display, layer_id)
            combo.blockSignals(False)
        self.set_selected(previous)

    def set_selected(self, layer_ids: list[str]) -> None:
        valid = {layer_id for layer_id, _display in self._choices}
        values = list(dict.fromkeys(layer_id for layer_id in layer_ids if layer_id in valid))
        values = values[: self._max_inputs]
        visible_count = min(len(values) + 1, self._max_inputs)
        for index, (number, combo) in enumerate(zip(self._numbers, self.combos, strict=True)):
            combo.blockSignals(True)
            combo.setCurrentIndex(combo.findData(values[index]) if index < len(values) else 0)
            combo.blockSignals(False)
            number.setVisible(index < visible_count)
            combo.setVisible(index < visible_count)

    def _normalize(self, _index: int) -> None:
        self.set_selected(self.selected())
