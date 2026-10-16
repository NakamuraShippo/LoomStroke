"""Edit explicit prohibitions and class-pair spacing without exposing metadata."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
)

from loomstroke.core.japanese_text.custom_rules import (
    CLASSES,
    PROHIBITIONS,
    custom_policy,
    normalize_rules,
)
from loomstroke.ui.translations import translated_literal_domain


def _ts(source):
    return translated_literal_domain("TextTypography", source, use_fallback=True)


class CustomRulesDialog(QDialog):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ts("Custom line breaking and spacing"))
        self._original = normalize_rules(options.get("custom_rules"))
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        policy = custom_policy(options)
        self.characters = {}
        labels = (
            "Prohibited at line start",
            "Prohibited at line end",
            "Inseparable repeated characters",
            "Hanging characters",
        )
        for key, label in zip(PROHIBITIONS, labels, strict=True):
            control = QLineEdit("".join(sorted(getattr(policy, key))), self)
            self.characters[key] = control
            form.addRow(_ts(label), control)
        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            [
                _ts(s)
                for s in (
                    "Left class",
                    "Right class",
                    "Minimum (em)",
                    "Preferred (em)",
                    "Maximum (em)",
                    "Priority",
                )
            ]
        )
        layout.addWidget(self.table)
        for rule in self._original.get("spacing", []):
            self.add_rule(rule)
        add = QPushButton(_ts("Add spacing rule"), self)
        add.clicked.connect(lambda: self.add_rule({}))
        layout.addWidget(add)
        remove = QPushButton(_ts("Remove selected rule"), self)
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        layout.addWidget(remove)
        self.validation_message = QLabel(self)
        self.validation_message.setWordWrap(True)
        layout.addWidget(self.validation_message)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(760, 420)

    def add_rule(self, rule):
        row = self.table.rowCount()
        self.table.insertRow(row)
        labels = (
            "Opening punctuation",
            "Closing punctuation",
            "Stops",
            "Middle punctuation",
            "Japanese",
            "Latin",
            "Space",
            "Other",
        )
        for column, key in enumerate(("left", "right")):
            combo = QComboBox(self.table)
            for value, label in zip(CLASSES, labels, strict=True):
                combo.addItem(_ts(label), value)
            combo.setCurrentIndex(combo.findData(rule.get(key, "japanese")))
            self.table.setCellWidget(row, column, combo)
        for column, key in enumerate(("minimum", "optimum", "maximum"), 2):
            spin = QDoubleSpinBox(self.table)
            spin.setRange(0, 2)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
            spin.setValue(rule.get(key, 0))
            self.table.setCellWidget(row, column, spin)
        priority = QSpinBox(self.table)
        priority.setRange(0, 9)
        priority.setValue(rule.get("priority", 0))
        self.table.setCellWidget(row, 5, priority)

    def _spacing_records(self):
        spacing = []
        for row in range(self.table.rowCount()):
            values = [self.table.cellWidget(row, col) for col in range(6)]
            spacing.append(
                dict(
                    left=values[0].currentData(),
                    right=values[1].currentData(),
                    minimum=values[2].value(),
                    optimum=values[3].value(),
                    maximum=values[4].value(),
                    priority=values[5].value(),
                )
            )
        return spacing

    def accept(self):
        spacing = self._spacing_records()
        pairs = {(rule["left"], rule["right"]) for rule in spacing}
        if len(pairs) != len(spacing):
            self.validation_message.setText(_ts("Each spacing class pair must be unique."))
            return
        if any(not rule["minimum"] <= rule["optimum"] <= rule["maximum"] for rule in spacing):
            self.validation_message.setText(
                _ts("Spacing must satisfy minimum ≤ preferred ≤ maximum.")
            )
            return
        super().accept()

    def rules(self):
        return normalize_rules(
            {
                **self._original,
                "spacing": self._spacing_records(),
                **{key: control.text() for key, control in self.characters.items()},
            }
        )
