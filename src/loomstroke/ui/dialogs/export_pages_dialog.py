"""Export Pages dialog (CT-10 X2, spec 10 §5).

Collects the batch-export plan: scope, format, destination, and naming.
All resolution goes through the shared CT-10 X1 core — the dialog never
re-derives page sets or names — and everything the user is about to get
is previewed live, including collisions, before OK ever enables.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_export_planner import ExportScopeError, resolve_scope_page_ids
from ...core.book_spread_layout import KNOWN_PAGE_ROLES
from ...core.export_naming import NamingContext, NamingError, detect_collisions, render_export_names
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

_PREVIEW_NAME_COUNT = 3
_FORMATS: tuple[tuple[str, str], ...] = (
    ("png", "PNG"),
    ("jpeg", "JPEG"),
    ("tiff", "TIFF"),
    ("webp", "WebP"),
)


@dataclass(frozen=True, slots=True)
class ExportPagesContext:
    """Everything the dialog needs to resolve scopes and names."""

    page_ids: tuple[str, ...]
    names_by_id: Mapping[str, str]
    metadata_by_id: Mapping[str, object]
    roles_by_id: Mapping[str, str]
    folios_by_id: Mapping[str, str | None]
    current_page_id: str | None
    document_name: str


@dataclass(frozen=True, slots=True)
class ExportPagesPlan:
    """A frozen, validated plan ready for the runner."""

    page_ids: tuple[str, ...]
    file_names: tuple[str, ...]
    destination: Path
    format_id: str
    #: CT-10 X4: ask | replace | skip | suffix (spec 10 §5)
    collision_policy: str = "ask"
    #: CT-10 X4: write ``export-report.json`` next to the files (spec 10 §13)
    write_report: bool = False


_COLLISION_POLICIES: tuple[tuple[str, str], ...] = (
    ("ask", "Ask before replacing"),
    ("replace", "Replace existing files"),
    ("skip", "Skip existing files"),
    ("suffix", "Add a numeric suffix"),
)


class ExportPagesDialog(QDialog):
    """Scope / format / destination / naming with a live validity preview."""

    def __init__(
        self,
        *,
        context: ExportPagesContext,
        initial_directory: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._plan: ExportPagesPlan | None = None
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Export Pages"))

        self._scope_combo = QComboBox(self)
        for value, label in (
            ("all", "All Pages"),
            ("range", "Page Range"),
            ("current", "Current Page"),
            ("odd", "Odd Pages"),
            ("even", "Even Pages"),
            ("role", "By Role"),
        ):
            index = self._scope_combo.count()
            self._scope_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._scope_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )

        self._range_edit = QLineEdit(self)
        self._range_edit.setPlaceholderText("1-4, 7")

        self._role_boxes: dict[str, QCheckBox] = {}
        for value, label in KNOWN_PAGE_ROLES:
            box = QCheckBox(self)
            self._translation_binder.register(box.setText, self._ts(label))
            box.setChecked(value == "body")
            self._role_boxes[value] = box

        self._format_combo = QComboBox(self)
        for value, label in _FORMATS:
            self._format_combo.addItem(label, value)

        self._directory_edit = QLineEdit(self)
        if initial_directory is not None:
            self._directory_edit.setText(str(initial_directory))
        self._browse_button = QPushButton(self)
        self._translation_binder.register(self._browse_button.setText, self._ts("Browse..."))
        self._browse_button.clicked.connect(self._on_browse)

        self._template_edit = QLineEdit(self)
        self._template_edit.setText("{document_name}_{index}")
        self._zero_pad_spin = QSpinBox(self)
        self._zero_pad_spin.setRange(0, 6)
        self._zero_pad_spin.setValue(3)

        self._collision_combo = QComboBox(self)
        for value, label in _COLLISION_POLICIES:
            index = self._collision_combo.count()
            self._collision_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._collision_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._report_check = QCheckBox(self)
        self._translation_binder.register(
            self._report_check.setText, self._ts("Write export-report.json")
        )

        self._preview_label = QLabel(self)
        self._preview_label.setWordWrap(True)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        self._build_layout()
        for signal in (
            self._scope_combo.currentIndexChanged,
            self._range_edit.textChanged,
            self._format_combo.currentIndexChanged,
            self._directory_edit.textChanged,
            self._template_edit.textChanged,
            self._zero_pad_spin.valueChanged,
        ):
            signal.connect(self._refresh_preview)
        for box in self._role_boxes.values():
            box.toggled.connect(self._refresh_preview)
        self._refresh_preview()

    # -- results -----------------------------------------------------------

    def export_plan(self) -> ExportPagesPlan | None:
        """The validated plan; ``None`` until an OK with valid inputs."""
        return self._plan

    # -- resolution --------------------------------------------------------

    def _resolve_plan(self) -> tuple[ExportPagesPlan | None, str]:
        """(plan, problem_text) — exactly one side is meaningful."""
        page_ids, problem = self._resolve_scope_ids()
        if page_ids is None:
            return None, problem
        names, problem = self._resolve_file_names(page_ids)
        if names is None:
            return None, problem
        directory_text = self._directory_edit.text().strip()
        if not directory_text:
            return None, self._translate_runtime("Choose a destination folder.")
        return (
            ExportPagesPlan(
                page_ids=page_ids,
                file_names=names,
                destination=Path(directory_text),
                format_id=str(self._format_combo.currentData()),
                collision_policy=str(self._collision_combo.currentData()),
                write_report=bool(self._report_check.isChecked()),
            ),
            "",
        )

    def _resolve_scope_ids(self) -> tuple[tuple[str, ...] | None, str]:
        context = self._context
        scope = self._scope_combo.currentData()
        try:
            include_roles = None
            if scope == "role":
                include_roles = [
                    value for value, box in self._role_boxes.items() if box.isChecked()
                ]
                if not include_roles:
                    return None, self._translate_runtime("Select at least one role.")
            page_ids = resolve_scope_page_ids(
                context.page_ids,
                scope=scope,
                range_text=self._range_edit.text(),
                current_page_id=context.current_page_id,
                include_roles=include_roles,
                page_metadata_by_id=context.metadata_by_id,  # type: ignore[arg-type]
            )
        except ExportScopeError as error:
            return None, self._translate_runtime("Invalid page range: {error}").format(error=error)
        if not page_ids:
            return None, self._translate_runtime("No pages match this scope.")
        return page_ids, ""

    def _resolve_file_names(self, page_ids: tuple[str, ...]) -> tuple[tuple[str, ...] | None, str]:
        context = self._context
        position = {page_id: index for index, page_id in enumerate(context.page_ids)}
        items = [
            NamingContext(
                index=position.get(page_id, 0) + 1,
                page_id=page_id,
                page_name=str(context.names_by_id.get(page_id, "")),
                folio=context.folios_by_id.get(page_id),
                role=str(context.roles_by_id.get(page_id, "body")),
                document_name=context.document_name,
            )
            for page_id in page_ids
        ]
        format_id = str(self._format_combo.currentData())
        try:
            names = render_export_names(
                self._template_edit.text(),
                items,
                zero_pad=int(self._zero_pad_spin.value()),
                extension="jpg" if format_id == "jpeg" else format_id,
            )
        except NamingError as error:
            return None, self._translate_runtime("Invalid name template: {error}").format(
                error=error
            )
        collisions = detect_collisions(names)
        if collisions:
            return None, self._translate_runtime(
                "The template produces duplicate file names (e.g. {name})."
            ).format(name=collisions[0])
        return names, ""

    def _refresh_preview(self) -> None:
        scope = self._scope_combo.currentData()
        self._range_edit.setEnabled(scope == "range")
        for box in self._role_boxes.values():
            box.setEnabled(scope == "role")

        plan, problem = self._resolve_plan()
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if plan is None:
            self._preview_label.setText(problem)
            if ok_button is not None:
                ok_button.setEnabled(False)
            return
        shown = list(plan.file_names[:_PREVIEW_NAME_COUNT])
        if len(plan.file_names) > _PREVIEW_NAME_COUNT:
            shown.append("...")
        self._preview_label.setText(
            self._translate_runtime("{count} pages -> {names}").format(
                count=len(plan.page_ids), names=", ".join(shown)
            )
        )
        if ok_button is not None:
            ok_button.setEnabled(True)

    def _on_accept(self) -> None:
        plan, _problem = self._resolve_plan()
        if plan is None:
            return
        self._plan = plan
        self.accept()

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, self._ts("Export Pages"), self._directory_edit.text()
        )
        if directory:
            self._directory_edit.setText(directory)

    # -- layout ------------------------------------------------------------

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        scope_label = QLabel(self)
        self._translation_binder.register(scope_label.setText, self._ts("Pages:"))
        form.addRow(scope_label, self._scope_combo)
        range_label = QLabel(self)
        self._translation_binder.register(range_label.setText, self._ts("Range:"))
        form.addRow(range_label, self._range_edit)

        roles_label = QLabel(self)
        self._translation_binder.register(roles_label.setText, self._ts("Roles:"))
        roles_column = QVBoxLayout()
        roles_column.setSpacing(2)
        for value, _label in KNOWN_PAGE_ROLES:
            roles_column.addWidget(self._role_boxes[value])
        form.addRow(roles_label, roles_column)

        format_label = QLabel(self)
        self._translation_binder.register(format_label.setText, self._ts("Format:"))
        form.addRow(format_label, self._format_combo)

        directory_label = QLabel(self)
        self._translation_binder.register(directory_label.setText, self._ts("Folder:"))
        directory_row = QHBoxLayout()
        directory_row.addWidget(self._directory_edit, 1)
        directory_row.addWidget(self._browse_button, 0)
        form.addRow(directory_label, directory_row)

        template_label = QLabel(self)
        self._translation_binder.register(template_label.setText, self._ts("File names:"))
        template_row = QHBoxLayout()
        template_row.addWidget(self._template_edit, 1)
        template_row.addWidget(self._zero_pad_spin, 0)
        form.addRow(template_label, template_row)

        collision_label = QLabel(self)
        self._translation_binder.register(collision_label.setText, self._ts("Existing files:"))
        form.addRow(collision_label, self._collision_combo)
        form.addRow(self._report_check)

        layout.addLayout(form)
        layout.addWidget(self._preview_label)
        layout.addWidget(self._buttons)

    # -- i18n --------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._refresh_preview()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportPagesDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
