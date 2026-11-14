"""Export for Print dialog (CT-11 P3/P4, spec 11 §5.2).

Scope selection reuses the CT-10 X1 planner.  Output kinds are the ones
this build can honestly produce: the Standard RGB PDF (P4) and the
flattened CMYK TIFF / Grayscale-K TIFF sequences (P3), and PDF/X candidates.
PDF/X candidates require manual compliance verification before submission.
A schematic
trim / bleed / crop-mark preview shows the geometry the PDF will use.
The output file / folder itself is picked by the caller after OK.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from importlib.util import find_spec
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.batch_export_planner import ExportScopeError, resolve_scope_page_ids
from ...core.bleed_geometry import trim_bounds
from ...core.comic_document_profile import PhysicalPageLayout
from ...core.export_collision_policy import ExportCollisionPolicy
from ...core.export_naming import NamingError, validate_template
from ...core.format_guide_geometry import layout_from_guides
from ...core.page_templates import PageTemplateStore
from ...core.print_format_library import (
    PrintFormat,
    PrintFormatLibrary,
    PrintSubmissionSettings,
)
from ...core.print_margin_geometry import annotate_print_geometry
from ...core.print_margin_models import PrintMarginContext, PrintMarginOptions
from ...core.print_mark_models import PrintMarkOptions
from ...core.print_page_geometry import (
    PrintPageGeometry,
    geometry_for_canvas,
    geometry_for_trim_bounds,
)
from ...io.bundled_color_profiles import create_print_profile_registry
from ...io.color_profile_registry import ColorProfileRegistry
from ..print_geometry_preview import PrintGeometryPreview
from ..print_profile_picker import PrintProfilePicker
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.print_margin_controls import PrintMarginControls
from ..widgets.print_mark_controls import PrintMarkControls

__all__ = [
    "DEFAULT_NAMING_TEMPLATE",
    "ExportPrintContext",
    "ExportPrintDialog",
    "ExportPrintPlan",
]

DEFAULT_NAMING_TEMPLATE = "{document_name}_{index}"

#: (output id, label, available). PDF/X candidates require pikepdf.
_OUTPUTS: tuple[tuple[str, str, bool], ...] = (
    ("pdf", "Standard PDF", True),
    ("pdfx1a", "PDF/X-1a candidate", find_spec("pikepdf") is not None),
    ("pdfx4", "PDF/X-4 candidate", find_spec("pikepdf") is not None),
    ("cmyk_tiff", "CMYK TIFF sequence", True),
    ("grayscale_k_tiff", "Grayscale K TIFF sequence", True),
    ("grayscale_tiff", "Grayscale TIFF sequence", True),
    ("bilevel_tiff", "1-bit TIFF sequence", True),
)
_SEQUENCE_OUTPUTS = frozenset({"cmyk_tiff", "grayscale_k_tiff", "grayscale_tiff", "bilevel_tiff"})
_PDF_OUTPUTS = frozenset({"pdf", "pdfx1a", "pdfx4"})
_CMYK_OUTPUTS = frozenset({"cmyk_tiff", "pdfx1a", "pdfx4"})
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
    "pdfx1a": "Verify the PDF/X candidate in callas pdfToolbox Desktop before submission.",
    "pdfx4": "Verify the PDF/X candidate in callas pdfToolbox Desktop before submission.",
    "cmyk_tiff": "Flattened CMYK TIFF, one file per page, with the profile embedded.",
    "grayscale_k_tiff": (
        "Grayscale K TIFF: flattened lightness on the K channel (C=M=Y=0), one file per page."
    ),
    "grayscale_tiff": "8-bit grayscale TIFF, one file per page.",
    "bilevel_tiff": "1-bit black-and-white TIFF, one file per page.",
}


@dataclass(frozen=True, slots=True)
class ExportPrintContext:
    page_ids: tuple[str, ...]
    metadata_by_id: Mapping[str, object]
    current_page_id: str | None
    canvas_size_px: tuple[int, int] = (0, 0)
    dpi: tuple[float, float] = (72.0, 72.0)
    document_profile_name: str | None = None
    current_page_guides: list[dict[str, Any]] | None = None
    page_layouts: Mapping[str, PhysicalPageLayout] = field(default_factory=dict)


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
    mark_options: PrintMarkOptions = field(default_factory=PrintMarkOptions)
    margin_options: PrintMarginOptions = field(default_factory=PrintMarginOptions)
    threshold: int = 128
    dither: str = "threshold"

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
        preset_library: PrintFormatLibrary | None = None,
        file_dialog: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._registry = registry if registry is not None else create_print_profile_registry()
        self._preset_library = preset_library or PrintFormatLibrary(PageTemplateStore())
        self._saved_presets = self._preset_library.submission_presets()
        self._applying_preset = False
        self._missing_profile_id: str | None = None
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
        self._preset_combo = QComboBox(self)
        self._preset_combo.addItem(translate_with_fallback("FormatGuidesDialog", "Custom"), "")
        self._translation_binder.register_domain(
            "FormatGuidesDialog", lambda text: self._preset_combo.setItemText(0, text), "Custom"
        )
        for key, value in self._saved_presets.items():
            self._preset_combo.addItem(value.name, key)
        self._save_preset_button = QPushButton(
            translate_with_fallback("FormatGuidesDialog", "Save Format Preset"), self
        )
        self._translation_binder.register_domain(
            "FormatGuidesDialog", self._save_preset_button.setText, "Save Format Preset"
        )
        self._output_combo = QComboBox(self)
        self._output_model = QStandardItemModel(self._output_combo)
        self._output_combo.setModel(self._output_model)
        for index, (value, label, available) in enumerate(_OUTPUTS):
            self._output_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._output_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
            if not available:
                item = self._output_model.item(index)
                if item is not None:
                    item.setEnabled(False)
                    self._translation_binder.register(
                        item.setToolTip,
                        self._ts("Not available: no PDF/X backend is installed."),
                    )
        self._marks_box = QCheckBox(self)
        self._translation_binder.register(self._marks_box.setText, self._ts("Draw crop marks"))
        self._marks_box.setChecked(True)
        self._mark_controls = PrintMarkControls(self)
        self._margin_controls = PrintMarginControls(self)
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
        self._mono_group = QGroupBox(self)
        self._translation_binder.register(self._mono_group.setTitle, self._ts("1-bit conversion"))
        self._threshold_spin = QSpinBox(self._mono_group)
        self._threshold_spin.setRange(0, 255)
        self._threshold_spin.setValue(128)
        self._dither_combo = QComboBox(self._mono_group)
        for value, label in (("threshold", "Threshold"), ("ordered4x4", "Ordered 4x4 dither")):
            index = self._dither_combo.count()
            self._dither_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, i=index: self._dither_combo.setItemText(i, text), self._ts(label)
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
        preset_row = QHBoxLayout()
        preset_row.addWidget(self._preset_combo, 1)
        preset_row.addWidget(self._save_preset_button)
        form.addRow(self._label("Preset:", self), preset_row)
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
        mono_form = QFormLayout(self._mono_group)
        mono_form.addRow(self._label("Threshold:", self._mono_group), self._threshold_spin)
        mono_form.addRow(self._label("Method:", self._mono_group), self._dither_combo)

        preview_layout = QVBoxLayout(self._preview_group)
        preview_layout.addWidget(self._preview, 1)
        preview_layout.addWidget(self._geometry_label)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self._marks_box)
        left.addWidget(self._mark_controls)
        left.addWidget(self._margin_controls)
        left.addWidget(self._note_label)
        left.addWidget(self._color_group)
        left.addWidget(self._sequence_group)
        left.addWidget(self._mono_group)
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
        self._preset_combo.currentIndexChanged.connect(self._choose_preset)
        self._save_preset_button.clicked.connect(self._save_preset)
        for signal in (
            self._output_combo.currentIndexChanged,
            self._marks_box.toggled,
            self._mark_controls.changed,
            self._intent_combo.currentIndexChanged,
            self._bpc_box.toggled,
            self._threshold_spin.valueChanged,
            self._dither_combo.currentIndexChanged,
        ):
            signal.connect(self._clear_selected_preset)
        self._scope_combo.currentIndexChanged.connect(self._refresh)
        self._range_edit.textChanged.connect(self._refresh)
        self._output_combo.currentIndexChanged.connect(self._refresh)
        self._marks_box.toggled.connect(self._refresh)
        self._mark_controls.changed.connect(self._refresh)
        self._margin_controls.changed.connect(self._refresh)
        self._profile_picker.profile_changed.connect(self._refresh)
        self._profile_picker.profile_changed.connect(self._clear_missing_profile)
        self._template_edit.textChanged.connect(self._refresh)

    def _clear_missing_profile(self) -> None:
        if not self._applying_preset:
            self._missing_profile_id = None
            self._clear_selected_preset()
            self._refresh()

    def _clear_selected_preset(self) -> None:
        if not self._applying_preset and self._preset_combo.currentIndex() != 0:
            self._preset_combo.setCurrentIndex(0)

    def _choose_preset(self) -> None:
        preset = self._saved_presets.get(str(self._preset_combo.currentData() or ""))
        if preset is None or preset.print_settings is None:
            return
        settings = preset.print_settings
        self._applying_preset = True
        try:
            self.select_output(settings.output)
            self._marks_box.setChecked(settings.with_marks)
            self._mark_controls.set_options(settings.mark_options)
            self._intent_combo.setCurrentIndex(self._intent_combo.findData(settings.intent))
            self._bpc_box.setChecked(settings.black_point_compensation)
            self._threshold_spin.setValue(settings.threshold)
            self._dither_combo.setCurrentIndex(self._dither_combo.findData(settings.dither))
            self._missing_profile_id = None
            if settings.cmyk_profile_id and not self._profile_picker.select_profile_id(
                settings.cmyk_profile_id
            ):
                self._missing_profile_id = settings.cmyk_profile_id
        finally:
            self._applying_preset = False
        self._refresh()

    def _save_preset(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            translate_with_fallback("FormatGuidesDialog", "Save Format Preset"),
            translate_with_fallback("FormatGuidesDialog", "Name"),
        )
        if not accepted:
            return
        width, height = self._context.canvas_size_px
        if width <= 0 or height <= 0:
            return
        try:
            layout = layout_from_guides(
                self._context.current_page_guides or [], (width, height), self._context.dpi
            )
            settings = PrintSubmissionSettings(
                output=self.current_output(),
                with_marks=self._marks_box.isChecked(),
                mark_options=self._mark_controls.options(),
                cmyk_profile_id=(
                    self._profile_picker.profile_id()
                    if self.current_output() in _CMYK_OUTPUTS
                    else None
                ),
                intent=str(self._intent_combo.currentData()),
                black_point_compensation=self._bpc_box.isChecked(),
                threshold=self._threshold_spin.value(),
                dither=str(self._dither_combo.currentData()),
            )
            preset = PrintFormat(name, layout, print_settings=settings)
            key = self._preset_library.save(preset)
        except (ValueError, OSError, RuntimeError):
            self._status_label.setText(self._ts("Could not save the format preset."))
            return
        self._saved_presets[key] = preset
        self._preset_combo.addItem(preset.name, key)
        self._preset_combo.setCurrentIndex(self._preset_combo.count() - 1)

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
        unavailable = output not in {value for value, _label, available in _OUTPUTS if available}
        page_ids, problem = self._resolve_page_ids()
        if page_ids is None or unavailable:
            return problem or self._translate_runtime(
                "Not available: no PDF/X backend is installed."
            )
        preset = self._saved_presets.get(str(self._preset_combo.currentData() or ""))
        if preset is not None:
            expected = preset.layout
            for page_id in page_ids:
                actual = self._context.page_layouts.get(page_id)
                if (
                    actual is None
                    or any(
                        abs(a - b) > 0.1
                        for values_a, values_b in (
                            (actual.trim_mm, expected.trim_mm),
                            (actual.bleed_mm, expected.bleed_mm),
                            (actual.safe_mm, expected.safe_mm),
                        )
                        for a, b in zip(values_a, values_b, strict=True)
                    )
                    or any(abs(a - b) > 0.01 for a, b in zip(actual.dpi, expected.dpi, strict=True))
                ):
                    return self._translate_runtime(
                        "The selected print preset does not match every page's format and DPI."
                    )
        color_problem = self._color_problem(output)
        if color_problem:
            return color_problem
        if output in ("pdfx1a", "pdfx4") and self._margin_controls.options().enabled:
            return self._translate_runtime("PDF/X candidates do not support margin information.")
        if output in _SEQUENCE_OUTPUTS:
            try:
                validate_template(self._template_edit.text())
            except NamingError as error:
                return self._translate_runtime("Invalid file name template: {error}").format(
                    error=error
                )
        return ""

    def _color_problem(self, output: str) -> str:
        if output not in _CMYK_OUTPUTS:
            return ""
        if self._missing_profile_id:
            return self._translate_runtime("The saved CMYK profile must be loaded again.")
        if not self._profile_picker.profile_id():
            return self._translate_runtime("A CMYK profile is required for CMYK output.")
        return ""

    def _refresh(self) -> None:
        output = self.current_output()
        is_sequence = output in _SEQUENCE_OUTPUTS
        self._range_edit.setEnabled(self._scope_combo.currentData() == "range")
        self._marks_box.setEnabled(output in _PDF_OUTPUTS)
        self._mark_controls.setEnabled(output in _PDF_OUTPUTS and self._marks_box.isChecked())
        self._mark_controls.setVisible(output in _PDF_OUTPUTS)
        self._margin_controls.setVisible(output in _PDF_OUTPUTS)
        self._color_group.setVisible(is_sequence or output in _CMYK_OUTPUTS)
        self._profile_picker.setEnabled(output in _CMYK_OUTPUTS)
        self._intent_combo.setEnabled(output in _CMYK_OUTPUTS)
        self._bpc_box.setEnabled(output in _CMYK_OUTPUTS)
        self._sequence_group.setVisible(is_sequence)
        self._mono_group.setVisible(output == "bilevel_tiff")
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
        with_marks = output in _PDF_OUTPUTS and self._marks_box.isChecked()
        trim = trim_bounds(self._context.current_page_guides or [], (width, height))
        geometry = (
            geometry_for_trim_bounds(
                width,
                height,
                self._context.dpi,
                trim,
                with_marks=with_marks,
                mark_options=self._mark_controls.options(),
            )
            if trim is not None
            else geometry_for_canvas(
                width,
                height,
                self._context.dpi,
                with_marks=with_marks,
                mark_options=self._mark_controls.options(),
            )
        )
        if output == "pdf":
            geometry = annotate_print_geometry(
                geometry,
                self._margin_controls.options(),
                PrintMarginContext(
                    file_name=translate_with_fallback("PrintMarginControls", "File name"),
                    total_pages=len(self._context.page_ids),
                    date_time=datetime.now().astimezone().isoformat(timespec="minutes"),
                ),
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
            with_marks=self._marks_box.isChecked() and output in _PDF_OUTPUTS,
            output=output,
            cmyk_profile_id=(
                self._profile_picker.profile_id() if output in _CMYK_OUTPUTS else None
            ),
            intent=str(self._intent_combo.currentData() or "relative"),
            black_point_compensation=self._bpc_box.isChecked(),
            naming_template=self._template_edit.text().strip() or DEFAULT_NAMING_TEMPLATE,
            collision_policy=str(self._collision_combo.currentData() or "ask"),
            mark_options=self._mark_controls.options(),
            margin_options=self._margin_controls.options(),
            threshold=self._threshold_spin.value(),
            dither=str(self._dither_combo.currentData()),
        )
        self.accept()

    # -- i18n --------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._profile_picker.refresh_translations()
        self._mark_controls.refresh_translations()
        self._margin_controls.refresh_translations()
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportPrintDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
