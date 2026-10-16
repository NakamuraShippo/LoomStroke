"""Export Webtoon dialog (CT-12 W1/W2, spec 12 §6).

Segments (pages, include/exclude) on the left, the virtual stream preview
in the middle, the generic profile and output settings on the right.
Every change replans the stream (pure resolver + fixed-height slicer) so
the preview, slice count and tallest slice stay live.  Layout is
session-only (spec 12 §12); the destination folder is chosen by the
caller after OK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

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
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.export_collision_policy import ExportCollisionPolicy
from ...core.export_naming import NamingError, validate_template
from ...core.webtoon.fixed_height_slicer import FixedHeightSlicer
from ...core.webtoon.slice_plan import SlicePlan
from ...core.webtoon.vertical_stream_plan import VerticalStreamPlan
from ...core.webtoon.vertical_stream_resolver import VerticalStreamResolver
from ...core.webtoon.webtoon_profile import WebtoonProfile
from ...core.webtoon.webtoon_segment import WebtoonSegment
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..webtoon_stream_preview import ThumbnailProvider, WebtoonStreamPreview

__all__ = [
    "DEFAULT_NAMING_TEMPLATE",
    "ExportWebtoonContext",
    "ExportWebtoonDialog",
    "ExportWebtoonPlan",
]

DEFAULT_NAMING_TEMPLATE = "{index}"
_FORMATS: tuple[tuple[str, str], ...] = (("png", "PNG"), ("jpeg", "JPEG"), ("webp", "WebP"))
_COLLISION_LABELS: dict[str, str] = {
    "ask": "Ask before replacing",
    "replace": "Replace existing files",
    "skip": "Skip existing files",
    "suffix": "Add a numeric suffix",
}


@dataclass(frozen=True, slots=True)
class ExportWebtoonContext:
    segments: tuple[WebtoonSegment, ...]
    names_by_id: Mapping[str, str]
    document_name: str = "untitled"
    profile: WebtoonProfile | None = None


@dataclass(frozen=True, slots=True)
class ExportWebtoonPlan:
    segments: tuple[WebtoonSegment, ...]
    profile: WebtoonProfile
    target_width_px: int
    max_slice_height_px: int
    format_id: str = "png"
    naming_template: str = DEFAULT_NAMING_TEMPLATE
    write_manifest: bool = True
    collision_policy: str = "ask"
    episode_name: str = ""


class ExportWebtoonDialog(QDialog):
    """Segments + live stream preview + output settings."""

    def __init__(
        self,
        *,
        context: ExportWebtoonContext,
        thumbnail_provider: ThumbnailProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._profile = context.profile or WebtoonProfile.generic()
        self._plan: ExportWebtoonPlan | None = None
        self._stream: VerticalStreamPlan | None = None
        self._slices: tuple[SlicePlan, ...] = ()
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Export Webtoon"))
        self._build_segment_widgets()
        self._build_setting_widgets()
        self._preview = WebtoonStreamPreview(self)
        self._preview.set_thumbnail_provider(thumbnail_provider)
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

    def export_plan(self) -> ExportWebtoonPlan | None:
        return self._plan

    def stream(self) -> VerticalStreamPlan | None:
        return self._stream

    def slices(self) -> tuple[SlicePlan, ...]:
        return self._slices

    def preview(self) -> WebtoonStreamPreview:
        return self._preview

    def set_segment_included(self, page_id: str, included: bool) -> None:
        for row in range(self._segment_list.count()):
            item = self._segment_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == page_id:
                item.setCheckState(Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
                return

    def current_segments(self) -> tuple[WebtoonSegment, ...]:
        """Context segments with the dialog's include flags and gap applied."""
        included = self._included_ids()
        gap = int(self._gap_spin.value())
        return tuple(
            replace(
                segment,
                include=segment.page_id in included,
                gap_before_px=0,
                gap_after_px=gap,
            )
            for segment in self._context.segments
        )

    # -- widgets -----------------------------------------------------------------

    def _build_segment_widgets(self) -> None:
        self._segment_group = QGroupBox(self)
        self._translation_binder.register(self._segment_group.setTitle, self._ts("Segments"))
        self._segment_list = QListWidget(self._segment_group)
        for segment in self._context.segments:
            name = self._context.names_by_id.get(segment.page_id, segment.page_id)
            item = QListWidgetItem(f"{name}  ({segment.source_width} x {segment.source_height})")
            item.setData(Qt.ItemDataRole.UserRole, segment.page_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if segment.include else Qt.CheckState.Unchecked
            )
            self._segment_list.addItem(item)

    def _build_setting_widgets(self) -> None:
        self._settings_group = QGroupBox(self)
        self._translation_binder.register(self._settings_group.setTitle, self._ts("Settings"))
        self._profile_label = QLabel(self._settings_group)
        self._profile_label.setWordWrap(True)
        self._profile_note = QLabel(self._settings_group)
        self._profile_note.setWordWrap(True)
        self._width_spin = QSpinBox(self._settings_group)
        self._width_spin.setRange(64, 16384)
        self._width_spin.setValue(self._profile.target_width_px)
        self._height_spin = QSpinBox(self._settings_group)
        self._height_spin.setRange(self._profile.min_slice_height_px, 16384)
        self._height_spin.setValue(self._profile.max_slice_height_px)
        self._gap_spin = QSpinBox(self._settings_group)
        self._gap_spin.setRange(0, 4096)
        self._gap_spin.setValue(0)
        for spin in (self._width_spin, self._height_spin, self._gap_spin):
            self._translation_binder.register(spin.setSuffix, self._ts(" px"))
        self._format_combo = QComboBox(self._settings_group)
        for format_id, label in _FORMATS:
            if self._profile.allows_format(format_id):
                self._format_combo.addItem(label, format_id)
        self._template_edit = QLineEdit(self._settings_group)
        self._template_edit.setText(DEFAULT_NAMING_TEMPLATE)
        self._collision_combo = QComboBox(self._settings_group)
        for index, policy in enumerate(ExportCollisionPolicy.POLICIES):
            label = _COLLISION_LABELS[policy]
            self._collision_combo.addItem(self._ts(label), policy)
            self._translation_binder.register(
                lambda text, c=self._collision_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._manifest_check = QCheckBox(self._settings_group)
        self._translation_binder.register(
            self._manifest_check.setText, self._ts("Write webtoon_manifest.json")
        )
        self._manifest_check.setChecked(True)
        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)

    def _build_layout(self) -> None:
        segment_layout = QVBoxLayout(self._segment_group)
        segment_layout.addWidget(self._segment_list)

        form = QFormLayout(self._settings_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow(self._profile_label)
        form.addRow(self._profile_note)
        form.addRow(self._label("Target width:"), self._width_spin)
        form.addRow(self._label("Max slice height:"), self._height_spin)
        form.addRow(self._label("Gap between pages:"), self._gap_spin)
        form.addRow(self._label("Format:"), self._format_combo)
        form.addRow(self._label("File names:"), self._template_edit)
        form.addRow(self._label("Existing files:"), self._collision_combo)
        form.addRow(self._manifest_check)

        right = QVBoxLayout()
        right.addWidget(self._settings_group)
        right.addWidget(self._summary_label)
        right.addWidget(self._status_label)
        right.addStretch(1)

        body = QHBoxLayout()
        body.addWidget(self._segment_group, 2)
        body.addWidget(self._preview, 3)
        body.addLayout(right, 3)

        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self._buttons)

    def _label(self, source: str) -> QLabel:
        label = QLabel(self._settings_group)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _connect_signals(self) -> None:
        self._segment_list.itemChanged.connect(lambda _item: self._refresh())
        self._width_spin.valueChanged.connect(lambda _v: self._refresh())
        self._height_spin.valueChanged.connect(lambda _v: self._refresh())
        self._gap_spin.valueChanged.connect(lambda _v: self._refresh())
        self._template_edit.textChanged.connect(lambda _t: self._refresh())

    # -- state -------------------------------------------------------------------

    def _included_ids(self) -> set[str]:
        included: set[str] = set()
        for row in range(self._segment_list.count()):
            item = self._segment_list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                included.add(str(item.data(Qt.ItemDataRole.UserRole)))
        return included

    def _replan(self) -> None:
        segments = self.current_segments()
        if not any(segment.include for segment in segments):
            self._stream, self._slices = None, ()
            self._preview.set_plan(None)
            return
        self._stream = VerticalStreamResolver().build(
            segments, target_width=int(self._width_spin.value())
        )
        self._slices = FixedHeightSlicer().plan(
            self._stream,
            max_height=int(self._height_spin.value()),
            min_height=self._profile.min_slice_height_px,
        )
        self._preview.set_plan(self._stream, self._slices)

    def _problem(self) -> str:
        if self._stream is None:
            return self._translate_runtime("No pages are included.")
        try:
            validate_template(self._template_edit.text())
        except NamingError as error:
            return self._translate_runtime("Invalid file name template: {error}").format(
                error=error
            )
        return ""

    def _refresh(self) -> None:
        self._profile_label.setText(
            self._translate_runtime("Profile: {name} (rev {revision}, updated {date})").format(
                name=(
                    self._translate_runtime("Generic")
                    if self._profile.id == WebtoonProfile.GENERIC_ID
                    else self._profile.id
                ),
                revision=self._profile.revision,
                date=self._profile.updated or "-",
            )
        )
        self._profile_note.setText(
            self._translate_runtime("Check the platform's current requirements before publishing.")
        )
        self._replan()
        if self._stream is not None:
            tallest = max((plan.height for plan in self._slices), default=0)
            summary = self._translate_runtime(
                "Stream: {width} x {height} px, {count} slice(s), tallest {tallest} px"
            ).format(
                width=self._stream.width,
                height=self._stream.height,
                count=len(self._slices),
                tallest=tallest,
            )
            if len(self._slices) > 1:
                summary += "\n" + self._translate_runtime(
                    "Boundaries are fixed-height only; check them in the preview before "
                    "publishing."
                )
            self._summary_label.setText(summary)
        else:
            self._summary_label.setText("")
        problem = self._problem()
        self._status_label.setText(problem)
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(not problem)

    def _on_accept(self) -> None:
        if self._problem() or self._stream is None:
            return
        self._plan = ExportWebtoonPlan(
            segments=tuple(segment for segment in self.current_segments() if segment.include),
            profile=self._profile,
            target_width_px=int(self._width_spin.value()),
            max_slice_height_px=int(self._height_spin.value()),
            format_id=str(self._format_combo.currentData() or "png"),
            naming_template=self._template_edit.text().strip() or DEFAULT_NAMING_TEMPLATE,
            write_manifest=self._manifest_check.isChecked(),
            collision_policy=str(self._collision_combo.currentData() or "ask"),
            episode_name=self._context.document_name,
        )
        self.accept()

    # -- i18n --------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportWebtoonDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
