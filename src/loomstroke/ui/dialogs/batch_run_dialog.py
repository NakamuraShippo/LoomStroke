"""Batch scope / failure policy / dry-run report before running a macro (CT-20 §6.2-6.3)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.actions import BatchPolicy
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["BatchRunDialog"]

DryRunProvider = Callable[[str, str], str]
"""``(scope, range_spec) -> report text``."""


def _ts(text: str) -> str:
    return translated_literal_domain("ActionsPanel", text, use_fallback=True)


class BatchRunDialog(QDialog):
    _POLICIES: tuple[BatchPolicy, ...] = ("stop", "continue", "skip_blocked")

    def __init__(
        self,
        macro_name: str,
        page_count: int,
        *,
        dry_run: DryRunProvider,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dry_run = dry_run
        self.setWindowTitle(_ts("Batch: {name}").format(name=macro_name))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_ts("Targets ({count} page(s))").format(count=page_count)))
        self.current_radio = QRadioButton(_ts("Current page"))
        self.all_radio = QRadioButton(_ts("All pages"))
        self.range_radio = QRadioButton(_ts("Page range (e.g. 1-3,5)"))
        self.current_radio.setChecked(True)
        self.range_edit = QLineEdit()
        row = QHBoxLayout()
        row.addWidget(self.range_radio)
        row.addWidget(self.range_edit, 1)
        layout.addWidget(self.current_radio)
        layout.addWidget(self.all_radio)
        layout.addLayout(row)
        policy_row = QHBoxLayout()
        policy_row.addWidget(QLabel(_ts("On failure:")))
        self.policy_combo = QComboBox()
        for policy in self._POLICIES:
            self.policy_combo.addItem(_ts(policy), policy)
        policy_row.addWidget(self.policy_combo, 1)
        layout.addLayout(policy_row)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        buttons = QHBoxLayout()
        self.dry_run_button = QPushButton(_ts("Dry Run"))
        self.dry_run_button.clicked.connect(self.refresh_report)
        buttons.addWidget(self.dry_run_button)
        buttons.addStretch(1)
        self.run_button = QPushButton(_ts("Run"))
        self.run_button.clicked.connect(self.accept)
        buttons.addWidget(self.run_button)
        cancel = QPushButton(_ts("Cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def scope(self) -> str:
        if self.all_radio.isChecked():
            return "all"
        if self.range_radio.isChecked():
            return "range"
        return "current"

    def range_spec(self) -> str:
        return self.range_edit.text().strip()

    def policy(self) -> BatchPolicy:
        return self.policy_combo.currentData()  # type: ignore[no-any-return]

    def refresh_report(self) -> str:
        text = self._dry_run(self.scope(), self.range_spec())
        self.report.setPlainText(text)
        return text
