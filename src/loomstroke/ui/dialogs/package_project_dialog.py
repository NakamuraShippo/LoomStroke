"""``File > Package Project…``: preflight table, per-resource action, destination, write."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.linked_assets import PackageAction, PackagePlan, PackageResult
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["PackageProjectDialog"]

PlanFactory = Callable[[Path, dict[str, PackageAction]], PackagePlan]
Writer = Callable[[PackagePlan], PackageResult]


def _ts(text: str) -> str:
    return translated_literal_domain("PackageProjectDialog", text, use_fallback=True)


class PackageProjectDialog(QDialog):
    _ACTIONS: tuple[PackageAction, ...] = ("copy", "external")

    def __init__(
        self,
        *,
        plan_factory: PlanFactory,
        writer: Writer,
        default_destination: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan_factory = plan_factory
        self._writer = writer
        self._choices: dict[str, PackageAction] = {}
        self._plan: PackagePlan | None = None
        self._result: PackageResult | None = None
        self.setWindowTitle(_ts("Package Project"))
        self.resize(720, 420)
        self._build(default_destination)
        self.rebuild()

    # -- build -------------------------------------------------------------------------

    def _build(self, default_destination: Path) -> None:
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(_ts("Destination folder:")))
        self.destination_edit = QLineEdit(str(default_destination))
        row.addWidget(self.destination_edit, 1)
        browse = QPushButton(_ts("Browse..."))
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [_ts("Resource"), _ts("State"), _ts("Size"), _ts("Action")]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)
        self.issues_label = QLabel("")
        self.issues_label.setWordWrap(True)
        layout.addWidget(self.issues_label)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.write_button = QPushButton(_ts("Write Package"))
        self.write_button.clicked.connect(self.write)
        buttons.addWidget(self.write_button)
        close = QPushButton(_ts("Close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, _ts("Choose the package folder"))
        if chosen:
            self.destination_edit.setText(chosen)

    # -- plan --------------------------------------------------------------------------

    def destination(self) -> Path:
        return Path(self.destination_edit.text().strip() or ".")

    def rebuild(self) -> PackagePlan:
        self._plan = self._plan_factory(self.destination(), dict(self._choices))
        self._fill(self._plan)
        return self._plan

    def current_plan(self) -> PackagePlan | None:
        return self._plan

    def _fill(self, plan: PackagePlan) -> None:
        self.table.setRowCount(len(plan.entries))
        for row, entry in enumerate(plan.entries):
            self.table.setItem(row, 0, QTableWidgetItem(entry.display_name))
            self.table.setItem(row, 1, QTableWidgetItem(entry.state))
            self.table.setItem(row, 2, QTableWidgetItem(f"{entry.byte_size:,}"))
            combo = QComboBox()
            combo.addItem(_ts("Copy into package"), "copy")
            combo.addItem(_ts("Leave external"), "external")
            if entry.action in self._ACTIONS:
                combo.setCurrentIndex(self._ACTIONS.index(entry.action))
                combo.currentIndexChanged.connect(
                    lambda _index, rid=entry.resource_id, box=combo: self._choose(rid, box)
                )
            else:
                combo.clear()
                combo.addItem(
                    _ts("Already embedded") if entry.action == "embed" else _ts("Missing")
                )
                combo.setEnabled(False)
            self.table.setCellWidget(row, 3, combo)
        counts = plan.counts()
        summary = _ts("Copy {copy} / external {external} / embedded {embed} / missing {missing}")
        text = summary.format(**counts)
        if plan.issues:
            text += "\n" + _ts("Issues: {issues}").format(issues=", ".join(plan.issues))
        self.issues_label.setText(text)
        self.write_button.setEnabled(plan.can_write)

    def _choose(self, resource_id: str, combo: QComboBox) -> None:
        self._choices[resource_id] = combo.currentData()
        self.rebuild()

    # -- write -------------------------------------------------------------------------

    def write(self) -> PackageResult | None:
        plan = self.rebuild()
        if not plan.can_write:
            self.status_label.setText(_ts("Save the document before packaging."))
            return None
        self._result = self._writer(plan)
        if self._result.ok:
            self.status_label.setText(
                _ts("Package written: {path} ({copied} asset(s) copied, {omitted} omitted)").format(
                    path=self._result.destination,
                    copied=self._result.copied_count,
                    omitted=self._result.omitted_count,
                )
            )
            self.write_button.setEnabled(False)
        else:
            self.status_label.setText(
                _ts("Package failed: {code} {detail}")
                .format(code=self._result.error_code, detail=self._result.detail)
                .strip()
            )
        return self._result

    def last_result(self) -> PackageResult | None:
        return self._result
