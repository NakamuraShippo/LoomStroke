"""Export Digital Comic Package dialog (CT-26 spec §ユーザーフロー / UI).

Format (CBZ / fixed-layout EPUB 3 / image package + manifest),
bibliographic metadata the user confirms, binding, cover choice, page
list in logical order (include/exclude) and the image format.  The
page list is never visually reversed for right binding: logical index
and output numbering stay story order.  The destination is picked by
the caller after OK.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.digital_package.publication_metadata import PublicationMetadata
from ...core.digital_package.publication_planner import DigitalComicPackagePlanner
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

__all__ = ["ExportDigitalPackageContext", "ExportDigitalPackageDialog", "ExportDigitalPackagePlan"]

_FORMATS: tuple[tuple[str, str], ...] = (
    ("cbz", "CBZ"),
    ("epub3_fxl", "Fixed-Layout EPUB 3"),
    ("image_manifest_v1", "Image Package + Manifest"),
)
_IMAGE_FORMATS: tuple[tuple[str, str], ...] = (("png", "PNG"), ("jpeg", "JPEG"), ("webp", "WebP"))
_BINDINGS: tuple[tuple[str, str], ...] = (
    ("right", "Right binding (right-to-left)"),
    ("left", "Left binding (left-to-right)"),
)


@dataclass(frozen=True, slots=True)
class ExportDigitalPackageContext:
    page_ids: tuple[str, ...]  # logical (story) order
    names_by_id: Mapping[str, str]
    roles_by_id: Mapping[str, str]
    spread_sides_by_id: Mapping[str, str]
    binding: str = "right"
    document_name: str = "untitled"
    default_language: str = "ja"


@dataclass(frozen=True, slots=True)
class ExportDigitalPackagePlan:
    format_id: str
    binding: str
    metadata: PublicationMetadata
    page_ids: tuple[str, ...]
    roles_by_id: Mapping[str, str]
    spread_sides_by_id: Mapping[str, str]
    cover_page_id: str | None
    image_format: str = "png"
    include_loomstroke_manifest: bool = True


class ExportDigitalPackageDialog(QDialog):
    """Collect the publication request; no rendering or writing happens here."""

    def __init__(
        self,
        *,
        context: ExportDigitalPackageContext,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._plan: ExportDigitalPackagePlan | None = None
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(
            self.setWindowTitle, self._ts("Export Digital Comic Package")
        )
        self._build_format_widgets()
        self._build_metadata_widgets()
        self._build_page_widgets()
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

    def export_plan(self) -> ExportDigitalPackagePlan | None:
        return self._plan

    def select_format(self, format_id: str) -> None:
        index = self._format_combo.findData(format_id)
        if index >= 0:
            self._format_combo.setCurrentIndex(index)

    def set_page_included(self, page_id: str, included: bool) -> None:
        for row in range(self._page_list.count()):
            item = self._page_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == page_id:
                item.setCheckState(Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
                return

    def generate_identifier(self) -> None:
        self._identifier_edit.setText(PublicationMetadata.generate_identifier())

    def included_page_ids(self) -> tuple[str, ...]:
        included: list[str] = []
        for row in range(self._page_list.count()):
            item = self._page_list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                included.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(included)

    # -- widgets -----------------------------------------------------------------

    def _build_format_widgets(self) -> None:
        self._format_combo = QComboBox(self)
        for index, (value, label) in enumerate(_FORMATS):
            self._format_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._format_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        self._image_combo = QComboBox(self)
        for value, label in _IMAGE_FORMATS:
            self._image_combo.addItem(label, value)
        self._binding_combo = QComboBox(self)
        for index, (value, label) in enumerate(_BINDINGS):
            self._binding_combo.addItem(self._ts(label), value)
            self._translation_binder.register(
                lambda text, c=self._binding_combo, i=index: c.setItemText(i, text),
                self._ts(label),
            )
        binding_index = self._binding_combo.findData(self._context.binding)
        self._binding_combo.setCurrentIndex(max(0, binding_index))
        self._manifest_check = QCheckBox(self)
        self._translation_binder.register(
            self._manifest_check.setText, self._ts("Include the LoomStroke manifest in the CBZ")
        )
        self._manifest_check.setChecked(True)
        self._note_label = QLabel(self)
        self._note_label.setWordWrap(True)

    def _build_metadata_widgets(self) -> None:
        self._metadata_group = QGroupBox(self)
        self._translation_binder.register(self._metadata_group.setTitle, self._ts("Metadata"))
        self._title_edit = QLineEdit(self._context.document_name, self._metadata_group)
        self._language_edit = QLineEdit(self._context.default_language, self._metadata_group)
        self._identifier_edit = QLineEdit(self._metadata_group)
        self._generate_button = QPushButton(self._metadata_group)
        self._translation_binder.register(self._generate_button.setText, self._ts("Generate"))
        self._creators_edit = QLineEdit(self._metadata_group)
        self._series_edit = QLineEdit(self._metadata_group)
        self._volume_edit = QLineEdit(self._metadata_group)
        self._description_edit = QLineEdit(self._metadata_group)

    def _build_page_widgets(self) -> None:
        self._pages_group = QGroupBox(self)
        self._translation_binder.register(self._pages_group.setTitle, self._ts("Pages"))
        self._cover_combo = QComboBox(self._pages_group)
        self._page_list = QListWidget(self._pages_group)
        planner = DigitalComicPackagePlanner()
        default_cover: str | None = None
        for index, page_id in enumerate(self._context.page_ids):
            name = self._context.names_by_id.get(page_id, page_id)
            role = planner.publication_role(self._context.roles_by_id.get(page_id))
            side = self._context.spread_sides_by_id.get(page_id, "unspecified")
            item = QListWidgetItem(f"{index + 1:>4}  {name}  [{role}]  ({side})")
            item.setData(Qt.ItemDataRole.UserRole, page_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._page_list.addItem(item)
            self._cover_combo.addItem(name, page_id)
            if default_cover is None and role == "cover":
                default_cover = page_id
        if default_cover is None and self._context.page_ids:
            default_cover = self._context.page_ids[0]
        if default_cover is not None:
            self._cover_combo.setCurrentIndex(max(0, self._cover_combo.findData(default_cover)))
        self._summary_label = QLabel(self)
        self._summary_label.setWordWrap(True)
        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)

    def _build_layout(self) -> None:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow(self._label("Format:", self), self._format_combo)
        form.addRow(self._label("Images:", self), self._image_combo)
        form.addRow(self._label("Binding:", self), self._binding_combo)
        form.addRow(self._manifest_check)

        metadata_form = QFormLayout(self._metadata_group)
        metadata_form.addRow(self._label("Title:", self._metadata_group), self._title_edit)
        metadata_form.addRow(self._label("Language:", self._metadata_group), self._language_edit)
        identifier_row = QHBoxLayout()
        identifier_row.addWidget(self._identifier_edit, 1)
        identifier_row.addWidget(self._generate_button)
        metadata_form.addRow(self._label("Identifier:", self._metadata_group), identifier_row)
        metadata_form.addRow(self._label("Creators:", self._metadata_group), self._creators_edit)
        metadata_form.addRow(self._label("Series:", self._metadata_group), self._series_edit)
        metadata_form.addRow(self._label("Volume:", self._metadata_group), self._volume_edit)
        metadata_form.addRow(
            self._label("Description:", self._metadata_group), self._description_edit
        )

        pages_layout = QVBoxLayout(self._pages_group)
        cover_row = QHBoxLayout()
        cover_row.addWidget(self._label("Cover:", self._pages_group))
        cover_row.addWidget(self._cover_combo, 1)
        pages_layout.addLayout(cover_row)
        pages_layout.addWidget(self._page_list, 1)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addWidget(self._note_label)
        left.addWidget(self._metadata_group)
        left.addStretch(1)
        body = QHBoxLayout()
        body.addLayout(left, 3)
        body.addWidget(self._pages_group, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self._summary_label)
        layout.addWidget(self._status_label)
        layout.addWidget(self._buttons)

    def _label(self, source: str, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _connect_signals(self) -> None:
        self._format_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        self._binding_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        self._cover_combo.currentIndexChanged.connect(lambda _i: self._refresh())
        self._page_list.itemChanged.connect(lambda _item: self._refresh())
        self._generate_button.clicked.connect(self.generate_identifier)
        for edit in (self._title_edit, self._language_edit, self._identifier_edit):
            edit.textChanged.connect(lambda _t: self._refresh())

    # -- state -------------------------------------------------------------------

    def _metadata(self) -> PublicationMetadata | None:
        creators = tuple(
            part.strip() for part in self._creators_edit.text().split(",") if part.strip()
        )
        try:
            return PublicationMetadata(
                title=self._title_edit.text().strip(),
                language=self._language_edit.text().strip(),
                identifier=self._identifier_edit.text().strip(),
                creators=creators,
                series=self._series_edit.text().strip() or None,
                volume=self._volume_edit.text().strip() or None,
                description=self._description_edit.text().strip() or None,
            )
        except ValueError:
            return None

    def _problem(self) -> str:
        if not self.included_page_ids():
            return self._translate_runtime("No pages are included.")
        if self._metadata() is None:
            return self._translate_runtime("Title, language and identifier are required.")
        cover = self._cover_combo.currentData()
        if cover and cover not in self.included_page_ids():
            return self._translate_runtime("The cover page is not included.")
        return ""

    def _refresh(self) -> None:
        format_id = str(self._format_combo.currentData() or "cbz")
        self._manifest_check.setVisible(format_id == "cbz")
        notes = {
            "cbz": "CBZ is a de facto format: readers interpret ComicInfo.xml differently.",
            "epub3_fxl": (
                "Image-only fixed-layout EPUB 3. Conformance is reported as checked only when "
                "EPUBCheck is installed."
            ),
            "image_manifest_v1": (
                "A folder of images plus publication_manifest.json — a neutral exchange "
                "package, not CBZ or EPUB."
            ),
        }
        self._note_label.setText(self._translate_runtime(notes.get(format_id, "")))
        included = self.included_page_ids()
        progression = "rtl" if self._binding_combo.currentData() == "right" else "ltr"
        self._summary_label.setText(
            self._translate_runtime(
                "{count} page(s) in story order, page progression {progression}."
            ).format(count=len(included), progression=progression)
        )
        problem = self._problem()
        self._status_label.setText(problem)
        ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setEnabled(not problem)

    def _on_accept(self) -> None:
        metadata = self._metadata()
        if metadata is None or self._problem():
            return
        included = self.included_page_ids()
        cover = self._cover_combo.currentData()
        self._plan = ExportDigitalPackagePlan(
            format_id=str(self._format_combo.currentData() or "cbz"),
            binding=str(self._binding_combo.currentData() or "right"),
            metadata=metadata,
            page_ids=included,
            roles_by_id=dict(self._context.roles_by_id),
            spread_sides_by_id=dict(self._context.spread_sides_by_id),
            cover_page_id=str(cover) if cover else None,
            image_format=str(self._image_combo.currentData() or "png"),
            include_loomstroke_manifest=self._manifest_check.isChecked(),
        )
        self.accept()

    # -- i18n --------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportDigitalPackageDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
