"""Manga Preflight dialog (CT-16 Phase B/C, spec 16 §6).

Profile + scope -> Run -> issue table (severity / page / rule / reason /
recommended action) with severity filters, a not-checked list (never
shown as pass) and counts.  Double-clicking an issue navigates through
the injected callback; the dialog itself holds no rule logic (§7).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.preflight.preflight_issue import PreflightIssue
from ...core.preflight.preflight_profile import MangaPreflightProfile
from ...core.preflight.preflight_report import PreflightReport
from ..preflight_issue_presenter import PreflightIssuePresenter
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

__all__ = ["MangaPreflightDialog"]

ScanCallback = Callable[[MangaPreflightProfile, str], PreflightReport]
NavigateCallback = Callable[[PreflightIssue], None]
_SCOPES: tuple[tuple[str, str], ...] = (("current", "Current page"), ("all", "All pages"))
_SEVERITIES: tuple[str, ...] = ("fatal", "warning", "info")


class MangaPreflightDialog(QDialog):
    """Run a scan and browse its issues."""

    def __init__(
        self,
        *,
        profiles: Sequence[MangaPreflightProfile],
        scan: ScanCallback,
        on_navigate: NavigateCallback | None = None,
        page_names: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profiles = tuple(profiles)
        self._scan = scan
        self._on_navigate = on_navigate
        self._page_names = dict(page_names or {})
        self._presenter = PreflightIssuePresenter()
        self._report: PreflightReport | None = None
        self._rows: list[PreflightIssue] = []
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Manga Preflight"))
        self._build_widgets()
        self._build_layout()
        self._refresh()

    # -- API ---------------------------------------------------------------------

    def report(self) -> PreflightReport | None:
        return self._report

    def visible_issues(self) -> tuple[PreflightIssue, ...]:
        return tuple(self._rows)

    def selected_profile(self) -> MangaPreflightProfile:
        index = max(0, self._profile_combo.currentIndex())
        return self._profiles[index]

    def selected_scope(self) -> str:
        return str(self._scope_combo.currentData() or "current")

    def select_profile(self, profile_id: str) -> None:
        for index, profile in enumerate(self._profiles):
            if profile.profile_id == profile_id:
                self._profile_combo.setCurrentIndex(index)
                return

    def select_scope(self, scope: str) -> None:
        index = self._scope_combo.findData(scope)
        if index >= 0:
            self._scope_combo.setCurrentIndex(index)

    def run(self) -> PreflightReport:
        report = self._scan(self.selected_profile(), self.selected_scope())
        self._report = report
        self._refresh()
        return report

    def set_severity_visible(self, severity: str, visible: bool) -> None:
        box = self._filters.get(severity)
        if box is not None:
            box.setChecked(visible)

    # -- widgets -----------------------------------------------------------------

    def _build_widgets(self) -> None:
        self._profile_combo = QComboBox(self)
        for profile in self._profiles:
            self._profile_combo.addItem(self._presenter.profile_label(profile), profile.profile_id)
        self._scope_combo = QComboBox(self)
        for index, (value, label) in enumerate(_SCOPES):
            self._scope_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._scope_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._run_button = QPushButton(self)
        self._translation_binder.register(self._run_button.setText, self._ts("Run Preflight"))
        self._run_button.clicked.connect(self.run)
        self._filters: dict[str, QCheckBox] = {}
        for severity in _SEVERITIES:
            box = QCheckBox(self)
            box.setChecked(True)
            self._translation_binder.register(
                box.setText, self._ts(PreflightIssuePresenter.SEVERITIES[severity])
            )
            box.toggled.connect(lambda _checked: self._refresh())
            self._filters[severity] = box
        self._table = QTableWidget(0, 5, self)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.cellDoubleClicked.connect(self._on_row_activated)
        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        self._not_checked_label = QLabel(self)
        self._not_checked_label.setWordWrap(True)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self._buttons.rejected.connect(self.reject)
        self._buttons.accepted.connect(self.accept)

    def _build_layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self._label("Profile:"))
        top.addWidget(self._profile_combo, 1)
        top.addWidget(self._label("Pages:"))
        top.addWidget(self._scope_combo, 1)
        top.addWidget(self._run_button)
        filters = QHBoxLayout()
        filters.addWidget(self._label("Show:"))
        for box in self._filters.values():
            filters.addWidget(box)
        filters.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._not_checked_label)
        layout.addWidget(self._buttons)
        self.resize(760, 480)

    def _label(self, source: str) -> QLabel:
        label = QLabel(self)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    # -- state -------------------------------------------------------------------

    def _visible_severities(self) -> set[str]:
        return {severity for severity, box in self._filters.items() if box.isChecked()}

    def _refresh(self) -> None:
        self._table.setHorizontalHeaderLabels(
            [
                self._translate_runtime("Severity"),
                self._translate_runtime("Page"),
                self._translate_runtime("Rule"),
                self._translate_runtime("Reason"),
                self._translate_runtime("Recommended action"),
            ]
        )
        report = self._report
        if report is None:
            self._rows = []
            self._table.setRowCount(0)
            self._summary_label.setText(self._translate_runtime("Run the preflight to see issues."))
            self._not_checked_label.setText("")
            return
        visible = self._visible_severities()
        self._rows = [issue for issue in report.issues if issue.severity in visible]
        self._table.setRowCount(len(self._rows))
        for row, issue in enumerate(self._rows):
            cells = (
                self._presenter.severity_label(issue.severity),
                self._page_names.get(issue.page_id or "", issue.page_id or "-"),
                self._presenter.rule_label(issue.rule_id),
                self._presenter.message(issue),
                self._presenter.actions(issue),
            )
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, issue.issue_id)
                self._table.setItem(row, column, item)
        self._table.resizeColumnsToContents()
        self._summary_label.setText(
            self._translate_runtime(
                "{fatal} fatal, {warning} warning(s), {info} info — {checked} rule(s) checked, "
                "{skipped} not checked."
            ).format(
                fatal=report.fatal_count,
                warning=report.warning_count,
                info=report.info_count,
                checked=len(report.checked),
                skipped=len(report.not_checked),
            )
        )
        if report.not_checked:
            lines = [
                self._presenter.not_checked_label(rule_id, reason)
                for rule_id, reason in report.not_checked
            ]
            self._not_checked_label.setText(
                self._translate_runtime("Not checked:") + "\n" + "\n".join(lines)
            )
        else:
            self._not_checked_label.setText("")

    def _on_row_activated(self, row: int, _column: int) -> None:
        if self._on_navigate is None or not (0 <= row < len(self._rows)):
            return
        self._on_navigate(self._rows[row])

    # -- i18n --------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        for index, profile in enumerate(self._profiles):
            self._profile_combo.setItemText(index, self._presenter.profile_label(profile))
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback(PreflightIssuePresenter.DOMAIN, text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
