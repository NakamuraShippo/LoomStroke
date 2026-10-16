"""Compact sizing helpers for numeric spin boxes."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSizePolicy, QSpinBox

_MIN_SPIN_WIDTH = 52
_MAX_SPIN_WIDTH = 132
_SPIN_CHROME_WIDTH = 18
_SPIN_HEIGHT = 22
_DIMENSION_SPIN_STEP = 8


def apply_spinbox_entry_policy(spin: QAbstractSpinBox) -> None:
    """Apply the shared text-entry spin box policy."""
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setFixedHeight(_SPIN_HEIGHT)


def apply_dimension_spinbox_policy(
    spin: QSpinBox,
    values: Iterable[int | float] = (),
    *,
    extra_digits: int = 0,
) -> None:
    """Match New Document width/height stepping while preserving exact typed values."""
    spin.setSingleStep(_DIMENSION_SPIN_STEP)
    fit_spinbox_to_values(spin, values, extra_digits=extra_digits)


def fit_spinbox_to_values(
    spin: QSpinBox | QDoubleSpinBox,
    values: Iterable[int | float] = (),
    *,
    extra_digits: int = 0,
) -> None:
    """Size ``spin`` to displayed digits and keep it in sync as values change."""
    texts = {_spinbox_text(spin, spin.value())}
    texts.update(_spinbox_text(spin, value) for value in values)
    text_width = max(spin.fontMetrics().horizontalAdvance(text) for text in texts)
    extra_width = max(0, int(extra_digits)) * _digit_width(spin)
    base_width = max(_MIN_SPIN_WIDTH, text_width + _SPIN_CHROME_WIDTH)
    previous_width = int(spin.property("_loomstroke_compact_spinbox_min_width_px") or 0)
    width = min(_MAX_SPIN_WIDTH, max(previous_width, base_width + extra_width))
    apply_spinbox_entry_policy(spin)
    spin.setFixedWidth(width)
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    spin.setProperty("_loomstroke_compact_spinbox_min_width_px", width)
    spin.setProperty("_loomstroke_compact_spinbox_extra_digits", extra_digits)
    if not spin.property("_loomstroke_compact_spinbox_width"):
        spin.setProperty("_loomstroke_compact_spinbox_width", True)
        spin.valueChanged.connect(
            lambda _value: fit_spinbox_to_values(
                spin,
                extra_digits=int(spin.property("_loomstroke_compact_spinbox_extra_digits") or 0),
            )
        )


def _spinbox_text(spin: QSpinBox | QDoubleSpinBox, value: int | float) -> str:
    if isinstance(spin, QDoubleSpinBox):
        text = spin.textFromValue(float(value))
    else:
        text = spin.textFromValue(int(value))
    return f"{spin.prefix()}{text}{spin.suffix()}"


def _digit_width(spin: QSpinBox | QDoubleSpinBox) -> int:
    return int(max(spin.fontMetrics().horizontalAdvance(str(digit)) for digit in range(10)))


__all__ = [
    "apply_dimension_spinbox_policy",
    "apply_spinbox_entry_policy",
    "fit_spinbox_to_values",
]
