"""Actions panel: catalog actions (Run / Record), macro library (Play / Batch / Dry Run)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.actions import MacroDefinition, MacroPlan
from loomstroke.ui.action_service import ActionService
from loomstroke.ui.dialogs.action_parameters_dialog import ActionParametersDialog
from loomstroke.ui.dialogs.batch_run_dialog import BatchRunDialog
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["ActionsPanel"]

_ROLE = 0x0100  # Qt.UserRole


def _ts(text: str) -> str:
    return translated_literal_domain("ActionsPanel", text, use_fallback=True)


class ActionsPanel(QWidget):
    def __init__(
        self,
        service: ActionService,
        parent: QWidget | None = None,
        *,
        confirm: Callable[[str], bool] | None = None,
        ask_name: Callable[[str], str | None] | None = None,
        parameters_dialog: Callable[..., Any] = ActionParametersDialog.ask,
        batch_dialog: Callable[..., Any] = BatchRunDialog,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._confirm = confirm or self._ask_yes_no
        self._ask_name = ask_name or self._prompt_name
        self._parameters_dialog = parameters_dialog
        self._batch_dialog = batch_dialog
        self._build()
        self.refresh()

    # -- build -------------------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel(_ts("Actions")))
        self.action_list = QListWidget()
        layout.addWidget(self.action_list, 1)
        row = QHBoxLayout()
        self.run_action_button = self._button(row, "Run Action...", self.run_selected_action)
        self.record_button = self._button(row, "Record", self.toggle_recording)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(QLabel(_ts("Macros")))
        self.macro_list = QListWidget()
        layout.addWidget(self.macro_list, 1)
        row2 = QHBoxLayout()
        self.play_button = self._button(row2, "Play", self.play_selected)
        self.batch_button = self._button(row2, "Batch...", self.batch_selected)
        self.dry_run_button = self._button(row2, "Dry Run", self.dry_run_selected)
        self.delete_button = self._button(row2, "Delete", self.delete_selected)
        self.import_button = self._button(row2, "Import...", self.import_macro)
        self.export_button = self._button(row2, "Export...", self.export_selected)
        row2.addStretch(1)
        layout.addLayout(row2)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumBlockCount(400)
        layout.addWidget(self.report, 1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

    def _button(self, row: QHBoxLayout, text: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(_ts(text))
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # -- data --------------------------------------------------------------------------

    def refresh(self) -> None:
        selected_action = self.selected_action_id()
        selected_item = self.macro_list.currentItem()
        selected_macro = str(selected_item.data(_ROLE)) if selected_item is not None else None
        self.action_list.clear()
        for descriptor in self._service.catalog.descriptors():
            item = QListWidgetItem(f"{descriptor.title}  ({descriptor.action_id})")
            item.setData(_ROLE, descriptor.action_id)
            self.action_list.addItem(item)
        self.macro_list.clear()
        for definition in self._service.store.list_definitions():
            item = QListWidgetItem(f"{definition.name}  [{len(definition.steps)} steps]")
            item.setData(_ROLE, definition.macro_id)
            self.macro_list.addItem(item)
        self._restore_selection(self.action_list, selected_action)
        self._restore_selection(self.macro_list, selected_macro)
        recording = self._service.recorder.is_recording
        self.record_button.setText(_ts("Stop Recording") if recording else _ts("Record"))
        if recording:
            self._report(
                _ts("Recording: {count} step(s)").format(count=self._service.recorder.step_count)
            )

    @staticmethod
    def _restore_selection(widget: QListWidget, wanted: str | None) -> None:
        if not wanted:
            return
        for row in range(widget.count()):
            if widget.item(row).data(_ROLE) == wanted:
                widget.setCurrentRow(row)
                return

    def selected_action_id(self) -> str | None:
        item = self.action_list.currentItem()
        return str(item.data(_ROLE)) if item is not None else None

    def selected_macro(self) -> MacroDefinition | None:
        item = self.macro_list.currentItem()
        if item is None:
            return None
        return self._service.store.load(str(item.data(_ROLE)))

    def select_macro(self, macro_id: str) -> bool:
        for row in range(self.macro_list.count()):
            if self.macro_list.item(row).data(_ROLE) == macro_id:
                self.macro_list.setCurrentRow(row)
                return True
        return False

    # -- actions -----------------------------------------------------------------------

    def run_selected_action(self) -> None:
        action_id = self.selected_action_id()
        descriptor = self._service.catalog.get(action_id) if action_id else None
        if descriptor is None:
            return
        answer = self._parameters_dialog(descriptor, self)
        if answer is None:
            return
        params, selector = answer
        if descriptor.requires_confirmation and not self._confirm(
            _ts("Run {title}? This cannot be previewed.").format(title=descriptor.title)
        ):
            return
        result = self._service.run_action(descriptor.action_id, params, selector)
        self._report(self._describe_counts(result.counts(), result.stopped_reason))
        self.refresh()

    def toggle_recording(self) -> None:
        recorder = self._service.recorder
        if not recorder.is_recording:
            recorder.start()
            self._report(_ts("Recording started. Run actions from this panel to add steps."))
        else:
            definition = recorder.stop(app_version=self._service.app_version)
            if definition is None:
                self._report(_ts("Nothing recorded."))
            else:
                name = self._ask_name(definition.name)
                if name:
                    definition = MacroDefinition(
                        macro_id=definition.macro_id,
                        name=name,
                        steps=definition.steps,
                        created_app_version=definition.created_app_version,
                    )
                self._service.store.save(definition)
                self._report(
                    _ts("Saved macro {name} ({count} step(s), {dropped} dropped)").format(
                        name=definition.name,
                        count=len(definition.steps),
                        dropped=recorder.dropped_count,
                    )
                )
        self.refresh()

    def dry_run_selected(self) -> MacroPlan | None:
        definition = self.selected_macro()
        if definition is None:
            return None
        plan = self._service.dry_run(definition)
        self.report.setPlainText("\n".join(plan.report_lines()))
        return plan

    def play_selected(self) -> None:
        definition = self.selected_macro()
        if definition is None:
            return
        plan = self._service.dry_run(definition)
        if not plan.is_ready:
            self.report.setPlainText("\n".join(plan.report_lines()))
            self._report(_ts("Macro is not ready on this page; see the dry run."))
            return
        if plan.requires_confirmation or plan.effect_kinds() != ("mutation",):
            summary = "\n".join(plan.report_lines())
            if not self._confirm(_ts("Run this macro?") + "\n" + summary):
                return
        result = self._service.run_on_page(definition)
        self._report(self._describe_counts(result.counts(), result.stopped_reason))

    def batch_selected(self) -> None:
        definition = self.selected_macro()
        if definition is None:
            return

        def dry_run(scope: str, spec: str) -> str:
            targets = self._service.targets_for_scope(scope, spec)  # type: ignore[arg-type]
            lines: list[str] = []
            for target in targets:
                lines.extend(self._service.dry_run(definition, target).report_lines())
            return "\n".join(lines) or _ts("No targets.")

        dialog = self._batch_dialog(
            definition.name, len(self._service.page_ids()), dry_run=dry_run, parent=self
        )
        try:
            if dialog.exec() != 1:
                return
            targets = self._service.targets_for_scope(dialog.scope(), dialog.range_spec())
            policy = dialog.policy()
        finally:
            dialog.deleteLater()
        if not targets:
            self._report(_ts("No targets."))
            return
        if not self._confirm(
            _ts("Run {name} on {count} page(s) with policy {policy}?").format(
                name=definition.name, count=len(targets), policy=policy
            )
        ):
            return
        result = self._service.run_batch(definition, targets, policy=policy)
        lines = [
            f"{record.target_id}: {record.status}"
            + (f" [{record.reason_code}]" if record.reason_code else "")
            for record in result.records
        ]
        remaining = result.remaining_targets()
        if remaining:
            lines.append(_ts("Not processed: {ids}").format(ids=", ".join(remaining)))
        self.report.setPlainText("\n".join(lines))
        self._report(self._describe_counts(result.counts(), result.stopped_reason))

    def delete_selected(self) -> None:
        definition = self.selected_macro()
        if definition is None:
            return
        if self._confirm(_ts("Delete macro {name}?").format(name=definition.name)):
            self._service.store.delete(definition.macro_id)
            self.refresh()

    def import_macro(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, _ts("Import macro"), "", "Macro JSON (*.json)"
        )
        if not chosen:
            return
        definition = self._service.store.import_json(Path(chosen))
        self._report(
            _ts("Imported {name}").format(name=definition.name)
            if definition is not None
            else _ts("The file is not a valid macro.")
        )
        self.refresh()

    def export_selected(self) -> None:
        definition = self.selected_macro()
        if definition is None:
            return
        chosen, _filter = QFileDialog.getSaveFileName(
            self, _ts("Export macro"), f"{definition.macro_id}.json", "Macro JSON (*.json)"
        )
        if not chosen:
            return
        try:
            self._service.store.export_json(definition, Path(chosen))
        except FileExistsError:
            self._report(_ts("That file already exists; choose another name."))
            return
        self._report(_ts("Exported {name}").format(name=definition.name))

    # -- helpers -----------------------------------------------------------------------

    @staticmethod
    def _describe_counts(counts: dict[str, int], stopped: str) -> str:
        parts = [f"{key}={value}" for key, value in sorted(counts.items())]
        text = ", ".join(parts) if parts else "-"
        return f"{text} (stopped: {stopped})" if stopped else text

    def _report(self, text: str) -> None:
        self.status_label.setText(text)

    def _ask_yes_no(self, question: str) -> bool:
        return (
            QMessageBox.question(self, _ts("Actions"), question) == QMessageBox.StandardButton.Yes
        )

    def _prompt_name(self, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, _ts("Save macro"), _ts("Macro name:"), text=default)
        return name.strip() if ok and name.strip() else None
