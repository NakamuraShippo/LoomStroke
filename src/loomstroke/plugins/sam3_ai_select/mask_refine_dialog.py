"""Modal dialog collecting SAM3 mask-refine morphology options."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...ai_selection import MAX_MASK_REFINE_RADIUS_PX, MaskRefineOptions
from ...ui.translations import translated_literal_domain

_TRANSLATION_DOMAIN = "SAM3AISelect"
# UI convenience cap; the module itself accepts any non-negative island area.
_MAX_REMOVE_ISLANDS_PX = 100_000


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)


class Sam3MaskRefineDialog(QDialog):
    """Collect ``MaskRefineOptions`` values; OK stays disabled while no-op."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(_ts("Refine Mask"))

        self._feather = QDoubleSpinBox(self)
        self._feather.setRange(0.0, float(MAX_MASK_REFINE_RADIUS_PX))
        self._feather.setSingleStep(0.5)
        self._feather.setSuffix(" px")

        self._expand = QSpinBox(self)
        self._expand.setRange(0, MAX_MASK_REFINE_RADIUS_PX)
        self._expand.setSuffix(" px")

        self._contract = QSpinBox(self)
        self._contract.setRange(0, MAX_MASK_REFINE_RADIUS_PX)
        self._contract.setSuffix(" px")

        self._smooth = QDoubleSpinBox(self)
        self._smooth.setRange(0.0, float(MAX_MASK_REFINE_RADIUS_PX))
        self._smooth.setSingleStep(0.5)
        self._smooth.setSuffix(" px")

        self._fill_holes = QCheckBox(self)

        self._remove_islands = QSpinBox(self)
        self._remove_islands.setRange(0, _MAX_REMOVE_ISLANDS_PX)
        self._remove_islands.setSuffix(" px²")

        self._invert = QCheckBox(self)

        form = QFormLayout()
        form.addRow(_ts("Feather"), self._feather)
        form.addRow(_ts("Expand"), self._expand)
        form.addRow(_ts("Contract"), self._contract)
        form.addRow(_ts("Smooth"), self._smooth)
        form.addRow(_ts("Fill holes"), self._fill_holes)
        form.addRow(_ts("Remove small islands"), self._remove_islands)
        form.addRow(_ts("Invert"), self._invert)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._buttons)

        for spin in (self._feather, self._expand, self._contract, self._smooth):
            spin.valueChanged.connect(self._sync_ok_enabled)
        self._remove_islands.valueChanged.connect(self._sync_ok_enabled)
        self._fill_holes.toggled.connect(self._sync_ok_enabled)
        self._invert.toggled.connect(self._sync_ok_enabled)
        self._sync_ok_enabled()

    def refine_options(self) -> MaskRefineOptions:
        return MaskRefineOptions(
            feather_px=float(self._feather.value()),
            expand_px=int(self._expand.value()),
            contract_px=int(self._contract.value()),
            smooth_radius_px=float(self._smooth.value()),
            fill_holes=self._fill_holes.isChecked(),
            remove_islands_below_px=int(self._remove_islands.value()),
            invert=self._invert.isChecked(),
        ).normalized()

    def refine_options_payload(self) -> dict[str, Any]:
        return self.refine_options().to_dict()

    def _sync_ok_enabled(self) -> None:
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(not self.refine_options().is_noop())


def show_mask_refine_dialog(parent: QWidget | None = None) -> dict[str, Any] | None:
    """Run the dialog modally; return the options payload or None on cancel."""

    dialog = Sam3MaskRefineDialog(parent)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.refine_options_payload()
    finally:
        dialog.deleteLater()


__all__ = ["Sam3MaskRefineDialog", "show_mask_refine_dialog"]
