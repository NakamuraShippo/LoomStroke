"""Comic Production Workspace (read-only plan projection + explicit per-page actions)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.comic_workflow import ComicApplyPlan
from loomstroke.ui.comic_workflow_service import ComicWorkflowService
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["ComicWorkflowPanel"]

_ROLE = 0x0100


def _ts(text: str) -> str:
    return translated_literal_domain("ComicWorkflowPanel", text, use_fallback=True)


class ComicWorkflowPanel(QWidget):
    def __init__(
        self,
        service: ComicWorkflowService,
        parent: QWidget | None = None,
        *,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._confirm = confirm or self._ask
        self._page_plans: dict[str, dict[str, Any]] = {}
        self._apply_plans: dict[str, ComicApplyPlan] = {}
        self._build()
        self.refresh_projects()

    # -- build -------------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        row.addWidget(QLabel(_ts("Project:")))
        self.project_combo = QComboBox()
        row.addWidget(self.project_combo, 1)
        self.plan_button = self._button(row, "Plan Book", self.plan_book)
        self.validate_button = self._button(row, "Validate", self.validate)
        layout.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_ts("Page / Panel"), _ts("Status"), _ts("Dialogues")])
        self.tree.itemSelectionChanged.connect(self._sync_buttons)
        layout.addWidget(self.tree, 1)
        row2 = QHBoxLayout()
        self.plan_page_button = self._button(row2, "Plan Page", self.plan_selected_page)
        self.preview_apply_button = self._button(row2, "Preview Apply", self.preview_selected_apply)
        self.apply_button = self._button(row2, "Apply Page", self.apply_selected_page)
        row2.addStretch(1)
        layout.addLayout(row2)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        layout.addWidget(self.report, 1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self._sync_buttons()

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # -- data --------------------------------------------------------------------------

    def refresh_projects(self) -> None:
        current = self.selected_project_id()
        self.project_combo.clear()
        for project_id, title in self._service.list_projects():
            self.project_combo.addItem(title, project_id)
        if current:
            index = self.project_combo.findData(current)
            if index >= 0:
                self.project_combo.setCurrentIndex(index)

    def selected_project_id(self) -> str | None:
        value = self.project_combo.currentData()
        return str(value) if value else None

    def selected_entry_id(self) -> str | None:
        item = self.tree.currentItem()
        while item is not None and item.data(0, _ROLE + 1) != "page":
            item = item.parent()
        return str(item.data(0, _ROLE)) if item is not None else None

    def plan_book(self) -> None:
        project_id = self.selected_project_id()
        plan = self._service.plan_book(project_id) if project_id else None
        if plan is None:
            self._report(_ts("Select a comic project first."))
            return
        self._fill_tree(plan)
        counts = plan.counts()
        self._report(
            _ts("Planned {pages} page(s), {panels} panel(s); revision {revision}").format(
                pages=counts["pages"], panels=counts["panels"], revision=plan.revision
            )
        )
        self._show_checkpoint(project_id)

    def validate(self) -> None:
        project_id = self.selected_project_id()
        if not project_id:
            return
        issues = self._service.validate(project_id)
        lines = [f"{issue.severity}: {issue.code} ({issue.path})" for issue in issues]
        self.report.setPlainText("\n".join(lines) or _ts("No issues."))
        self._show_checkpoint(project_id)

    def _fill_tree(self, plan: Any) -> None:
        self.tree.clear()
        for page in plan.pages:
            label = _ts("Page {number}").format(number=page.page_number)
            if not page.is_linked:
                label += "  " + _ts("[unlinked]")
            page_item = QTreeWidgetItem(
                [label, _ts(page.status), str(sum(len(p.dialogue_refs) for p in page.panels))]
            )
            page_item.setData(0, _ROLE, page.entry_id)
            page_item.setData(0, _ROLE + 1, "page")
            self.tree.addTopLevelItem(page_item)
            for panel in page.panels:
                panel_item = QTreeWidgetItem(
                    [panel.panel_id, _ts(panel.status), str(len(panel.dialogue_refs))]
                )
                panel_item.setData(0, _ROLE, panel.panel_id)
                panel_item.setData(0, _ROLE + 1, "panel")
                page_item.addChild(panel_item)
            page_item.setExpanded(True)
        self._sync_buttons()

    # -- per page ----------------------------------------------------------------------

    def plan_selected_page(self) -> dict[str, Any] | None:
        project_id, entry_id = self.selected_project_id(), self.selected_entry_id()
        if not project_id or not entry_id:
            return None
        result = self._service.plan_page(project_id, entry_id)
        if result.get("plan") is not None:
            self._page_plans[entry_id] = result
            panels = len(result["plan"].get("panels") or [])
            self._report(_ts("Page plan ready: {count} panel(s)").format(count=panels))
        else:
            self._report(
                _ts("Page plan blocked: {codes}").format(
                    codes=", ".join(result.get("blockedReasonCodes") or [])
                )
            )
        self.report.setPlainText(self._page_summary(result))
        self._sync_buttons()
        return result

    def preview_selected_apply(self) -> ComicApplyPlan | None:
        project_id, entry_id = self.selected_project_id(), self.selected_entry_id()
        if not project_id or not entry_id:
            return None
        page_plan = (self._page_plans.get(entry_id) or {}).get("plan")
        plan = self._service.plan_apply(project_id, entry_id, page_plan)
        self._apply_plans[entry_id] = plan
        lines = [f"changeset {plan.changeset_id or '-'} @ revision {plan.base_document_revision}"]
        lines += [f"  {key}: {value}" for key, value in sorted(plan.operation_summary.items())]
        if plan.blocked_reason_codes:
            lines.append("  blocked: " + ", ".join(plan.blocked_reason_codes))
        self.report.setPlainText("\n".join(lines))
        self._report(_ts("Ready to apply.") if plan.is_ready else _ts("Apply is blocked."))
        self._sync_buttons()
        return plan

    def apply_selected_page(self) -> dict[str, Any] | None:
        project_id, entry_id = self.selected_project_id(), self.selected_entry_id()
        if not project_id or not entry_id:
            return None
        plan = self._apply_plans.get(entry_id) or self.preview_selected_apply()
        page_plan = (self._page_plans.get(entry_id) or {}).get("plan")
        if plan is None or page_plan is None:
            return None
        confirmed = self._confirm(
            _ts("Apply {count} operation(s) to the current page as one undo step?").format(
                count=plan.operation_count
            )
        )
        result = self._service.apply_page(project_id, plan, page_plan, confirmed=confirmed)
        status = str(result.get("status"))
        codes = ", ".join(result.get("blockedReasonCodes") or [])
        self._report(f"{status}{' (' + codes + ')' if codes else ''}")
        if status == "applied":
            book = self._service.current_plan(project_id)
            if book is not None:
                self._fill_tree(book)
        return result

    # -- helpers -----------------------------------------------------------------------

    def _show_checkpoint(self, project_id: str) -> None:
        checkpoint = self._service.checkpoint(project_id)
        if checkpoint is not None:
            self.status_label.setText(
                self.status_label.text()
                + "  |  "
                + _ts("Next step: {step}").format(step=checkpoint.resume_point() or "-")
            )

    @staticmethod
    def _page_summary(result: dict[str, Any]) -> str:
        plan = result.get("plan") or {}
        lines = [f"status: {result.get('status')}"]
        for panel in plan.get("panels") or []:
            lines.append(f"  panel {panel.get('id')}: balloons={len(panel.get('balloons') or [])}")
        for warning in result.get("warnings") or []:
            lines.append(f"  warning {warning}")
        return "\n".join(lines)

    def _sync_buttons(self) -> None:
        entry = self.selected_entry_id()
        self.plan_page_button.setEnabled(bool(entry))
        self.preview_apply_button.setEnabled(bool(entry and entry in self._page_plans))
        self.apply_button.setEnabled(bool(entry and entry in self._page_plans))

    def _report(self, text: str) -> None:
        self.status_label.setText(text)

    def _ask(self, question: str) -> bool:
        answer = QMessageBox.question(self, _ts("Comic Workflow"), question)
        return answer == QMessageBox.StandardButton.Yes
