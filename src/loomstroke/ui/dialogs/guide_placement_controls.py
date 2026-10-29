"""Shared guide placement fields, independent of persistence."""

import math

from PySide6.QtWidgets import QDoubleSpinBox, QLabel

from ..translations import translate_with_fallback


def safe_offset(value):
    try:
        result = tuple(float(v) for v in value)
        if len(result) == 2 and all(math.isfinite(v) for v in result):
            return result
    except (TypeError, ValueError):
        return (0.0, 0.0)
    return (0.0, 0.0)


class GuidePlacementControls:
    def __init__(self, form, parent, *, angle=0.0, offset=(0.0, 0.0)):
        try:
            angle = float(angle)
            angle = angle if math.isfinite(angle) else 0.0
        except (TypeError, ValueError):
            angle = 0.0
        offset = safe_offset(offset)
        self.angle = QDoubleSpinBox(parent)
        self.angle.setRange(-360, 360)
        self.angle.setDecimals(2)
        self.angle.setSuffix(" °")
        self.angle.setValue(angle)
        self.x, self.y = QDoubleSpinBox(parent), QDoubleSpinBox(parent)
        for spin, value in zip((self.x, self.y), offset, strict=True):
            spin.setRange(-1_000_000, 1_000_000)
            spin.setDecimals(3)
            spin.setSuffix(" px")
            spin.setValue(value)
        self.labels = []
        for source, spin in (("Rotation", self.angle), ("Offset X", self.x), ("Offset Y", self.y)):
            label = QLabel(parent)
            self.labels.append((label, source))
            form.addRow(label, spin)
        self.hint = QLabel(parent)
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        self.retranslate()

    def retranslate(self):
        for label, source in self.labels:
            label.setText(translate_with_fallback("GuidePlacement", source))
        self.hint.setText(
            translate_with_fallback(
                "GuidePlacement", "Drag guides to move them; Shift-drag to rotate."
            )
        )

    def offset(self):
        return self.x.value(), self.y.value()
