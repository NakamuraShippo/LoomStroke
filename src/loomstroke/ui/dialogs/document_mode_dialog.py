"""``File > Document Mode…`` (CT-24 D1): capability matrix, conversion dry run, ink coverage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.print_modes import (
    PROCESS_PLATES,
    DocumentColorMode,
    DocumentModePlanner,
    InkCoverageCalculator,
    ModeCapabilityMatrix,
    ModeConversionPlan,
    SeparationSnapshot,
)
from loomstroke.ui.translations import translated_literal_domain
from loomstroke.ui.widgets.native_cmyk_conversion_controls import NativeCmykConversionControls

__all__ = ["DocumentModeDialog"]

CompositeProvider = Callable[[], np.ndarray | None]


def _ts(text: str) -> str:
    return translated_literal_domain("DocumentModeDialog", text, use_fallback=True)


class DocumentModeDialog(QDialog):
    def __init__(
        self,
        *,
        current_mode: DocumentColorMode,
        canvas_size: tuple[int, int],
        layer_count: int,
        composite_provider: CompositeProvider,
        page_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = current_mode
        self._size = canvas_size
        self._layers = max(1, int(layer_count))
        self._composite = composite_provider
        self._page_id = page_id
        self._matrix = ModeCapabilityMatrix()
        self._planner = DocumentModePlanner(self._matrix)
        self._coverage = InkCoverageCalculator()
        self.last_plan: ModeConversionPlan | None = None
        self.last_snapshot: SeparationSnapshot | None = None
        self.setWindowTitle(_ts("Document Mode"))
        self.resize(720, 520)
        self._build()
        self.refresh_plan()

    # -- build -------------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        self.current_mode_label = QLabel()
        self._refresh_current_mode_label()
        layout.addWidget(self.current_mode_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [_ts("Mode"), _ts("Bits"), _ts("Channels"), _ts("Storage"), _ts("Brush"), _ts("Export")]
        )
        for row, capability in enumerate(self._matrix.rows()):
            self.table.insertRow(row)
            cells = (
                capability.mode_id,
                str(capability.bits_per_channel),
                "/".join(capability.channels),
                _ts("yes") if capability.storage_supported else _ts("no"),
                _ts("yes") if capability.brush_supported else _ts("no"),
                _ts("yes") if capability.export_supported else _ts("no"),
            )
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(_ts("Convert to:")))
        self.target_combo = QComboBox()
        for capability in self._matrix.rows():
            self.target_combo.addItem(capability.mode_id, capability.mode_id)
        self.target_combo.currentIndexChanged.connect(lambda _i: self.refresh_plan())
        row.addWidget(self.target_combo, 1)
        self.alpha_box = QCheckBox(_ts("Keep alpha"))
        self.alpha_box.setChecked(self._mode.alpha_enabled)
        self.alpha_box.toggled.connect(lambda _c: self.refresh_plan())
        row.addWidget(self.alpha_box)
        layout.addLayout(row)
        plates = QHBoxLayout()
        plates.addWidget(QLabel(_ts("Plates:")))
        self.plate_boxes: dict[str, QCheckBox] = {}
        for plate in PROCESS_PLATES:
            box = QCheckBox(plate.upper())
            box.setChecked(True)
            plates.addWidget(box)
            self.plate_boxes[plate] = box
        self.coverage_button = QPushButton(_ts("Estimate Ink Coverage"))
        self.coverage_button.clicked.connect(self.estimate_coverage)
        plates.addWidget(self.coverage_button)
        plates.addStretch(1)
        layout.addLayout(plates)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        close = QPushButton(_ts("Close"))
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    # -- actions -----------------------------------------------------------------------

    def attach_native_controls(self, canvas, pages, registry=None, source_profile=None):
        self.native_controls = NativeCmykConversionControls(
            canvas, pages, registry=registry, source_profile=source_profile, parent=self
        )
        self.layout().insertWidget(2, self.native_controls)
        self.native_controls.converted.connect(self._native_converted)

    def _native_converted(self, bits, fingerprint):
        self._mode = DocumentColorMode(f"cmyk{bits}", fingerprint, compositing_policy="native_cmyk")
        self._refresh_current_mode_label()
        self.target_combo.setCurrentIndex(self.target_combo.findData(self._mode.mode_id))
        self.refresh_plan()

    def _refresh_current_mode_label(self):
        self.current_mode_label.setText(
            _ts("Current mode: {mode} ({width} x {height}, {layers} layer(s))").format(
                mode=self._mode.mode_id,
                width=self._size[0],
                height=self._size[1],
                layers=self._layers,
            )
        )

    def target_mode(self) -> DocumentColorMode:
        return DocumentColorMode(
            str(self.target_combo.currentData()),
            self._mode.profile_ref,
            self.alpha_box.isChecked(),
            self._mode.compositing_policy,
        )

    def refresh_plan(self) -> ModeConversionPlan:
        plan = self._planner.plan(
            self._mode,
            self.target_mode(),
            width=self._size[0],
            height=self._size[1],
            layer_count=self._layers,
        )
        self.last_plan = plan
        lines = [
            _ts("Conversion {src} -> {dst}: {state}").format(
                src=plan.source.mode_id,
                dst=plan.target.mode_id,
                state=_ts("ready (dry run only)") if plan.is_ready else _ts("blocked"),
            ),
            _ts("Estimated pixel memory: {mib:.1f} MiB").format(
                mib=plan.estimated_bytes / (1024 * 1024)
            ),
        ]
        if plan.blocked_reason_codes:
            lines.append(_ts("Blocked: {codes}").format(codes=", ".join(plan.blocked_reason_codes)))
        if plan.warnings:
            lines.append(_ts("Warnings: {codes}").format(codes=", ".join(plan.warnings)))
        self.report.setPlainText("\n".join(lines))
        return plan

    def plates_on(self) -> tuple[str, ...]:
        return tuple(p for p, box in self.plate_boxes.items() if box.isChecked())

    def estimate_coverage(self) -> SeparationSnapshot | None:
        rgba = self._composite()
        if rgba is None:
            self.report.appendPlainText(_ts("No composite is available."))
            return None
        cmyk = self._coverage.separate_rgba(rgba)
        snapshot = self._coverage.snapshot(
            cmyk, page_id=self._page_id, plates_on=self.plates_on(), source_mode="rgba8"
        )
        self.last_snapshot = snapshot
        payload: dict[str, Any] = snapshot.to_dict()
        coverage = ", ".join(f"{k.upper()} {v:.1f}%" for k, v in payload["coveragePercent"].items())
        lines = [
            _ts("Ink coverage (estimate, no output profile): {coverage}").format(coverage=coverage),
            _ts(
                "TAC max {max:.1f}% / mean {mean:.1f}% / over {limit:.0f}%: {over:.2f}% of pixels"
            ).format(
                max=payload["maxTacPercent"],
                mean=payload["meanTacPercent"],
                limit=payload["tacLimitPercent"],
                over=payload["overLimitPercent"],
            ),
            _ts("Plates on: {plates}").format(
                plates=", ".join(p.upper() for p in snapshot.plates_on)
            ),
        ]
        self.report.appendPlainText("\n".join(lines))
        return snapshot
