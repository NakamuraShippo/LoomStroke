"""Export for Print dialog (CT-11 P3/P4, spec 11 §5.2).

Scope selection reuses the CT-10 X1 planner.  Output kinds are the ones
this build can honestly produce: the Standard RGB PDF (P4) and the
flattened CMYK TIFF / Grayscale-K TIFF sequences (P3).  PDF/X-1a and
PDF/X-4 are listed but disabled until a PDF/X backend and validator exist
(spec 11 §5.2 — a plain PDF is never presented as PDF/X).  A schematic
trim / bleed / crop-mark preview shows the geometry the PDF will use.
The output file / folder itself is picked by the caller after OK.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_export_planner import ExportScopeError, resolve_scope_page_ids
from ...core.export_collision_policy import ExportCollisionPolicy
from ...core.export_naming import NamingError, validate_template
from ...core.print_page_geometry import PrintPageGeometry, geometry_for_canvas
from ...io.bundled_color_profiles import create_print_profile_registry
from ...io.color_profile_registry import ColorProfileRegistry
from ..print_geometry_preview import PrintGeometryPreview
from ..print_profile_picker import PrintProfilePicker
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

__all__ = [
    "DEFAULT_NAMING_TEMPLATE",
    "ExportPrintContext",
    "ExportPrintDialog",
    "ExportPrintPlan",
]

DEFAULT_NAMING_TEMPLATE = "{document_name}_{index}"

#: (output id, label, available).  PDF/X stays disabled until a backend exists.
_OUTPUTS: tuple[tuple[str, str, bool], ...] = (
    ("pdf", "Standard PDF", True),
    ("pdfx1a", "PDF/X-1a", False),
    ("pdfx4", "PDF/X-4", False),
    ("cmyk_tiff", "CMYK TIFF sequence", True),
    ("grayscale_k_tiff", "Grayscale K TIFF sequence", True),
)
_SEQUENCE_OUTPUTS = frozenset({"cmyk_tiff", "grayscale_k_tiff"})
_INTENTS: tuple[tuple[str, str], ...] = (
    ("relative", "Relative Colorimetric"),
    ("perceptual", "Perceptual"),
    ("saturation", "Saturation"),
    ("absolute", "Absolute Colorimetric"),
)
_COLLISION_LABELS: dict[str, str] = {
    "ask": "Ask before replacing",
    "replace": "Replace existing files",
    "skip": "Skip existing files",
    "suffix": "Add a numeric suffix",
}
_OUTPUT_NOTES: dict[str, str] = {
    "pdf": "Standard RGB PDF (not PDF/X). One logical page per PDF page, in reading order.",
    "cmyk_tiff": "Flattened CMYK TIFF, one file per page, with the profile embedded.",
    "grayscale_k_tiff": (
        "Grayscale K TIFF: flattened lightness on the K channel (C=M=Y=0), one file per page."
    ),
}


@dataclass(frozen=True, slots=True)
class ExportPrintContext:
    page_ids: tuple[str, ...]
    metadata_by_id: Mapping[str, object]
    current_page_id: str | None
    canvas_size_px: tuple[int, int] = (0, 0)
    dpi: tuple[float, float] = (72.0, 72.0)
    document_profile_name: str | None = None


@dataclass(frozen=True, slots=True)
class ExportPrintPlan:
    page_ids: tuple[str, ...]
    with_marks: bool
    output: str = "pdf"
    cmyk_profile_id: str | None = None
    intent: str = "relative"
    black_point_compensation: bool = True
    naming_template: str = DEFAULT_NAMING_TEMPLATE
    collision_policy: str = "ask"

    @property
    def is_sequence(self) -> bool:
        return self.output in _SEQUENCE_OUTPUTS


class ExportPrintDialog(QDialog):
    """Scope, output kind, colour settings and geometry preview for print output."""

    def __init__(
        self,
        *,
        context: ExportPrintContext,
        registry: ColorProfileRegistry | None = None,
        file_dialog: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._registry = registry if registry is not None else create_print_profile_registry()
        self._plan: ExportPrintPlan | None = None
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Export for Print"))
        self._build_scope_widgets()
        self._build_output_widgets()
        self._build_color_widgets(file_dialog)
        self._build_sequence_widgets()
        self._build_preview_widgets()
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        self._build_layout()
        self._connect_signals()
        self._refresh()

    # -- API ---------------------------------------------------------------------

    def export_plan(self) -> ExportPrintPlan | None:
        return self._plan

    def select_output(self, output: str) -> None:
        index = self._output_combo.findData(output)
        if index >= 0:
            self._output_combo.setCurrentIndex(index)

    def current_output(self) -> str:
        return str(self._output_combo.currentData())

    def profile_picker(self) -> PrintProfilePicker:
        return self._profile_picker

    def preview_geometry(self) -> PrintPageGeometry | None:
        return self._preview.geometry_model()

    # -- widgets -----------------------------------------------------------------

    def _build_scope_widgets(self) -> None:
        self._scope_combo = QComboBox(self)
        for value, label in (
            ("all", "All Pages"),
            ("range", "Page Range"),
            ("current", "Current Page"),
        ):
            index = self._scope_combo.count()
            self._scope_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._scope_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._range_edit = QLineEdit(self)
        self._range_edit.setPlaceholderText("1-4, 7")
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)

    def _build_output_widgets(self) -> None:
        self._output_combo = QComboBox(self)
        for index, (value, label, available) in enumerate(_OUTPUTS):
            self._output_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._output_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
            if not available:
                item = self._output_combo.model().item(index)
                if item is not None:
                    item.setEnabled(False)
                    self._translation_binder.register(
                        item.setToolTip,
                        self._ts("Not available: no PDF/X backend is installed."),
                    )
        self._marks_box = QCheckBox(self)
        self._translation_binder.register(self._marks_box.setText, self._ts("Draw crop marks"))
        self._marks_box.setChecked(True)
        self._note_label = QLabel(self)
        self._note_label.setWordWrap(True)

    def _build_color_widgets(self, file_dialog: Callable[[], Any] | None) -> None:
        self._color_group = QGroupBox(self)
        self._translation_binder.register(self._color_group.setTitle, self._ts("Colour"))
        self._source_label = QLabel(self._color_group)
        self._source_label.setWordWrap(True)
        self._profile_picker = PrintProfilePicker(
            self._registry, file_dialog=file_dialog, parent=self._color_group
        )
        self._intent_combo = QComboBox(self._color_group)
        for index, (value, label) in enumerate(_INTENTS):
            self._intent_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._intent_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._bpc_box = QCheckBox(self._color_group)
        self._translation_binder.register(
            self._bpc_box.setText, self._ts("Black point compensation")
        )
        self._bpc_box.setChecked(True)

    def _build_sequence_widgets(self) -> None:
        self._sequence_group = QGroupBox(self)
        self._translation_binder.register(self._sequence_group.setTitle, self._ts("Files"))
        self._template_edit = QLineEdit(self._sequence_group)
        self._template_edit.setText(DEFAULT_NAMING_TEMPLATE)
        self._collision_combo = QComboBox(self._sequence_group)
        for index, policy in enumerate(ExportCollisionPolicy.POLICIES):
            label = _COLLISION_LABELS[policy]
            self._collision_combo.addItem(self._ts(label), policy)
            self._translation_binder.register(
                lambda text, c=self._collision_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )

    def _build_preview_widgets(self) -> None:
        self._preview_group = QGroupBox(self)
        self._translation_binder.register(self._preview_group.setTitle, self._ts("Preview"))
        self._preview = PrintGeometryPreview(self._preview_group)
        self._geometry_label = QLabel(self._preview_group)
        self._geometry_label.setWordWrap(True)

    def _build_layout(self) -> None:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow(self._label("Pages:", self), self._scope_combo)
        form.addRow(self._label("Range:", self), self._range_edit)
        form.addRow(self._label("Output:", self), self._output_combo)

        color_form = QFormLayout(self._color_group)
        color_form.addRow(self._source_label)
        color_form.addRow(self._label("CMYK profile:", self._color_group), self._profile_picker)
        color_form.addRow(self._label("Rendering intent:", self._color_group), self._intent_combo)
        color_form.addRow(self._bpc_box)

        sequence_form = QFormLayout(self._sequence_group)
        sequence_form.addRow(self._label("File names:", self._sequence_group), self._template_edit)
        sequence_form.addRow(
            self._label("Existing files:", self._sequence_group), self._collision_combo
        )

        preview_layout = QVBoxLayout(self._preview_group)
        preview_layout.addWidget(self._preview, 1)
        preview_layout.addWidget(self._geometry_label)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self._marks_box)
        left.addWidget(self._note_label)
        left.addWidget(self._color_group)
        left.addWidget(self._sequence_group)
        left.addWidget(self._status_label)
        left.addStretch(1)

        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(self._preview_group, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(self._buttons)

    def _label(self, source: str, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _connect_signals(self) -> None:
        self._scope_combo.currentIndexChanged.connect(self._refresh)
        self._range_edit.textChanged.connect(self._refresh)
        self._output_combo.currentIndexChanged.connect(self._refresh)
        self._marks_box.toggled.connect(self._refresh)
        self._profile_picker.profile_changed.connect(self._refresh)
        self._template_edit.textChanged.connect(self._refresh)

    # -- state -------------------------------------------------------------------

    def _resolve_page_ids(self) -> tuple[tuple[str, ...] | None, str]:
        try:
            page_ids = resolve_scope_page_ids(
                self._context.page_ids,
                scope=self._scope_combo.currentData(),
                range_text=self._range_edit.text(),
                current_page_id=self._context.current_page_id,
                page_metadata_by_id=self._context.metadata_by_id,  # type: ignore[arg-type]
            )
        except ExportScopeError as error:
            return None, self._translate_runtime("Invalid page range: {error}").format(error=error)
        if not page_ids:
            return None, self._translate_runtime("No pages match this scope.")
        return page_ids, ""

    def _problem(self, output: str) -> str:
        """First blocking problem for the current settings, or ``""``."""
        page_ids, problem = self._resolve_page_ids()
        if page_ids is None:
            return problem
        if output == "cmyk_tiff" and not self._profile_picker.profile_id():
            return self._translate_runtime("A CMYK profile is required for CMYK output.")
        if output in _SEQUENCE_OUTPUTS:
            try:
                validate_template(self._template_edit.text())
            except NamingError as error:
                return self._translate_runtime("Invalid file name template: {error}").format(
                    error=error
                )
        return ""

    def _refresh(self) -> None:
        output = self.current_output()
        is_sequence = output in _SEQUENCE_OUTPUTS
        self._range_edit.setEnabled(self._scope_combo.currentData() == "range")
        self._marks_box.setEnabled(output == "pdf")
        self._color_group.setVisible(is_sequence)
        self._profile_picker.setEnabled(output == "cmyk_tiff")
        self._intent_combo.setEnabled(output == "cmyk_tiff")
        self._bpc_box.setEnabled(output == "cmyk_tiff")
        self._sequence_group.setVisible(is_sequence)
        self._note_label.setText(self._translate_runtime(_OUTPUT_NOTES.get(output, "")))
        source_name = self._context.document_profile_name or self._translate_runtime(
            "sRGB (assumed)"
        )
        self._source_label.setText(
            self._translate_runtime("Source profile: {name}").format(name=source_name)
        )
        self._refresh_preview(output)
        problem = self._problem(output)
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if problem:
            self._status_label.setText(problem)
            if ok_button is not None:
                ok_button.setEnabled(False)
            return
        page_ids, _problem = self._resolve_page_ids()
        self._status_label.setText(
            self._translate_runtime("{count} page(s) will be exported.").format(
                count=len(page_ids or ())
            )
        )
        if ok_button is not None:
            ok_button.setEnabled(True)

    def _refresh_preview(self, output: str) -> None:
        width, height = self._context.canvas_size_px
        if width <= 0 or height <= 0:
            self._preview.set_geometry(None)
            self._geometry_label.setText("")
            return
        with_marks = output == "pdf" and self._marks_box.isChecked()
        geometry = geometry_for_canvas(
            int(width), int(height), self._context.dpi, with_marks=with_marks
        )
        self._preview.set_geometry(geometry)
        _x, _y, trim_w, trim_h = geometry.trim_box_pt
        text = self._translate_runtime("Trim: {width} x {height} mm").format(
            width=f"{trim_w / 72.0 * 25.4:.1f}", height=f"{trim_h / 72.0 * 25.4:.1f}"
        )
        if not geometry.has_bleed:
            text += "\n" + self._translate_runtime(
                "No physical layout: the whole canvas is the trim area (no bleed)."
            )
        self._geometry_label.setText(text)

    def _on_accept(self) -> None:
        output = self.current_output()
        if self._problem(output):
            return
        page_ids, _problem = self._resolve_page_ids()
        if page_ids is None:
            return
        self._plan = ExportPrintPlan(
            page_ids=page_ids,
            with_marks=self._marks_box.isChecked() and output == "pdf",
            output=output,
            cmyk_profile_id=(self._profile_picker.profile_id() if output == "cmyk_tiff" else None),
            intent=str(self._intent_combo.currentData() or "relative"),
            black_point_compensation=self._bpc_box.isChecked(),
            naming_template=self._template_edit.text().strip() or DEFAULT_NAMING_TEMPLATE,
            collision_policy=str(self._collision_combo.currentData() or "ask"),
        )
        self.accept()

    # -- i18n --------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._profile_picker.refresh_translations()
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportPrintDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
