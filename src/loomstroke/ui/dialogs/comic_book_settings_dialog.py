"""Comic Book Settings dialog (CT-06 spec §6.1).

One place for the document-level book settings: binding direction, which
face the body starts on, how the book opens (Single/Spread), and the first
folio number.  Binding and the view already have controls in the Pages
panel; this dialog does not replace them — all three write the same book
metadata through ``set_book_layout``, so they can never disagree.

The dialog itself never writes anything.  It reports which values the user
*changed* (``changed_values``), and the caller commits them as **one**
``SetBookLayoutCommand`` — one undo step for one visit to the dialog,
however many fields were touched.  Reporting only the changed keys also
keeps the command a semantic no-op (and out of history) when the dialog is
accepted untouched.

The spec asks for a note that changing the binding never reorders pages;
it is shown permanently at the bottom rather than as a confirmation, so it
teaches instead of interrupting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.book_spread_layout import (
    KNOWN_PAGE_ROLES,
    BookLayout,
    PageBookAttributes,
    resolve_spreads,
)
from ...core.folio_resolver import resolve_folios
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from ..widgets.spinbox_sizing import fit_spinbox_to_values

_FOLIO_RANGE = (1, 9999)
_PREVIEW_MAX_SPREADS = 8


class ComicBookSettingsDialog(QDialog):
    """Edit the book-level comic settings stored in the document."""

    def __init__(
        self,
        *,
        layout: BookLayout,
        preview_pages: Sequence[tuple[str, str]] | None = None,
        page_attributes: Mapping[str, PageBookAttributes] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # Spread preview inputs (spec §6.1: show the pairing before the
        # change is committed).  (page_id, display name) in logical order;
        # empty means "no document" and the preview section is omitted.
        self._preview_pages = tuple(preview_pages or ())
        self._page_attributes = dict(page_attributes or {})
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Comic Book Settings"))
        self._initial = layout

        self._binding_combo = QComboBox(self)
        self._add_item(self._binding_combo, "Left Binding", "left")
        self._add_item(self._binding_combo, "Right Binding", "right")
        self._select(self._binding_combo, layout.binding)

        self._body_start_combo = QComboBox(self)
        self._add_item(self._body_start_combo, "Auto", "auto")
        self._add_item(self._body_start_combo, "Recto (front face)", "recto")
        self._add_item(self._body_start_combo, "Verso (back face)", "verso")
        self._select(self._body_start_combo, layout.body_start)
        self._body_start_combo.setToolTip(
            self._ts(
                "Which face the first body page falls on. Auto keeps the "
                "current layout; Recto and Verso insert at most one blank "
                "facing slot to line the body up."
            )
        )

        self._view_combo = QComboBox(self)
        self._add_item(self._view_combo, "Single", "single")
        self._add_item(self._view_combo, "Spread", "spread")
        self._select(self._view_combo, layout.default_view)

        self._folio_start_spin = QSpinBox(self)
        self._folio_start_spin.setRange(*_FOLIO_RANGE)
        self._folio_start_spin.setValue(
            min(max(int(layout.folio_start), _FOLIO_RANGE[0]), _FOLIO_RANGE[1])
        )
        fit_spinbox_to_values(self._folio_start_spin, _FOLIO_RANGE)

        self._folio_prefix_edit = QLineEdit(self)
        self._folio_prefix_edit.setMaxLength(16)
        self._folio_prefix_edit.setText(layout.folio_prefix)

        # F2: which roles show no folio.  The checkboxes cover the known
        # roles; hidden roles outside that list (unknown metadata) are
        # preserved verbatim so saving the dialog never drops them.
        known_role_values = {value for value, _label in KNOWN_PAGE_ROLES}
        self._unknown_hidden_roles = tuple(
            role for role in layout.folio_hidden_roles if role not in known_role_values
        )
        self._hidden_role_boxes: dict[str, QCheckBox] = {}
        for value, label in KNOWN_PAGE_ROLES:
            box = QCheckBox(self)
            self._translation_binder.register(box.setText, self._ts(label))
            box.setChecked(value in layout.folio_hidden_roles)
            self._hidden_role_boxes[value] = box

        self._folio_example_label = QLabel(self)
        self._folio_prefix_edit.textChanged.connect(self._refresh_folio_example)
        self._folio_start_spin.valueChanged.connect(self._refresh_folio_example)
        self._refresh_folio_example()

        self._spread_preview_label = QLabel(self)
        self._spread_preview_label.setWordWrap(False)
        if self._preview_pages:
            self._binding_combo.currentIndexChanged.connect(self._refresh_spread_preview)
            self._body_start_combo.currentIndexChanged.connect(self._refresh_spread_preview)
            self._folio_start_spin.valueChanged.connect(self._refresh_spread_preview)
            self._folio_prefix_edit.textChanged.connect(self._refresh_spread_preview)
            for box in self._hidden_role_boxes.values():
                box.toggled.connect(self._refresh_spread_preview)
            self._refresh_spread_preview()

        self._build_layout()

    # -- results -----------------------------------------------------------

    def changed_values(self) -> dict[str, Any]:
        """The keys the user changed, ready for ``set_book_layout``."""

        changed: dict[str, Any] = {}
        if self._binding_combo.currentData() != self._initial.binding:
            changed["binding"] = self._binding_combo.currentData()
        if self._body_start_combo.currentData() != self._initial.body_start:
            changed["body_start"] = self._body_start_combo.currentData()
        if self._view_combo.currentData() != self._initial.default_view:
            changed["default_view"] = self._view_combo.currentData()
        if int(self._folio_start_spin.value()) != int(self._initial.folio_start):
            changed["folio_start"] = int(self._folio_start_spin.value())
        if self._folio_prefix_edit.text() != self._initial.folio_prefix:
            changed["folio_prefix"] = self._folio_prefix_edit.text()
        hidden_roles = self._selected_hidden_roles()
        if set(hidden_roles) != set(self._initial.folio_hidden_roles):
            # A JSON-friendly list: this value goes straight into metadata.
            changed["folio_hidden_roles"] = list(hidden_roles)
        return changed

    def _selected_hidden_roles(self) -> tuple[str, ...]:
        checked = tuple(
            value
            for value, _label in KNOWN_PAGE_ROLES
            if self._hidden_role_boxes[value].isChecked()
        )
        return checked + self._unknown_hidden_roles

    # -- layout ------------------------------------------------------------

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        binding_label = QLabel(self)
        self._translation_binder.register(binding_label.setText, self._ts("Binding:"))
        form.addRow(binding_label, self._binding_combo)

        body_start_label = QLabel(self)
        self._translation_binder.register(body_start_label.setText, self._ts("Start body page:"))
        form.addRow(body_start_label, self._body_start_combo)

        view_label = QLabel(self)
        self._translation_binder.register(view_label.setText, self._ts("Page display:"))
        form.addRow(view_label, self._view_combo)

        folio_label = QLabel(self)
        self._translation_binder.register(folio_label.setText, self._ts("Folio starts at:"))
        form.addRow(folio_label, self._folio_start_spin)

        prefix_label = QLabel(self)
        self._translation_binder.register(prefix_label.setText, self._ts("Folio prefix:"))
        form.addRow(prefix_label, self._folio_prefix_edit)
        form.addRow(QLabel(self), self._folio_example_label)

        hidden_label = QLabel(self)
        self._translation_binder.register(hidden_label.setText, self._ts("Hide folio for:"))
        hidden_column = QVBoxLayout()
        hidden_column.setSpacing(2)
        for value, _label in KNOWN_PAGE_ROLES:
            hidden_column.addWidget(self._hidden_role_boxes[value])
        form.addRow(hidden_label, hidden_column)

        layout.addLayout(form)

        if self._preview_pages:
            preview_title = QLabel(self)
            self._translation_binder.register(preview_title.setText, self._ts("Spread preview:"))
            layout.addWidget(preview_title)
            layout.addWidget(self._spread_preview_label)

        note = QLabel(self)
        note.setWordWrap(True)
        self._translation_binder.register(
            note.setText,
            self._ts("Changing the binding does not reorder pages"),
        )
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            self._translation_binder.register(ok_button.setText, self._ts("OK"))
        if cancel_button is not None:
            self._translation_binder.register(cancel_button.setText, self._ts("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_item(self, combo: QComboBox, text: str, value: str) -> None:
        """Add an item whose label survives a runtime language switch.

        The display text retranslates in place; the stable ``value`` in
        ``itemData`` is what gets written to the document.
        """
        index = combo.count()
        combo.addItem(self._ts(text), value)
        self._translation_binder.register(
            lambda translated, c=combo, i=index: c.setItemText(i, translated),
            self._ts(text),
        )

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    # -- i18n --------------------------------------------------------------

    def _candidate_layout(self) -> BookLayout:
        """The BookLayout the dialog currently describes (not yet saved)."""
        return BookLayout(
            binding=self._binding_combo.currentData(),
            body_start=self._body_start_combo.currentData(),
            default_view=self._view_combo.currentData(),
            folio_start=int(self._folio_start_spin.value()),
            folio_prefix=self._folio_prefix_edit.text(),
            folio_hidden_roles=self._selected_hidden_roles(),
        )

    def _refresh_spread_preview(self) -> None:
        """Re-derive the pairing with the dialog's candidate settings.

        Uses the same resolvers as the Pages panel and the canvas, so what
        the preview promises is exactly what committing will produce.
        """
        page_ids = [page_id for page_id, _name in self._preview_pages]
        names = dict(self._preview_pages)
        layout = self._candidate_layout()
        spreads = resolve_spreads(page_ids, layout, self._page_attributes)
        folios = resolve_folios(page_ids, layout, self._page_attributes)
        position = {page_id: index for index, page_id in enumerate(page_ids)}

        lines = []
        for spread in spreads[:_PREVIEW_MAX_SPREADS]:
            left, right = spread.slots
            lines.append(
                f"{spread.index + 1}:  "
                f"{self._slot_text(left.page_id, names, folios, position)}  |  "
                f"{self._slot_text(right.page_id, names, folios, position)}"
            )
        hidden_count = len(spreads) - _PREVIEW_MAX_SPREADS
        if hidden_count > 0:
            lines.append(self._translate_runtime("... and {count} more").format(count=hidden_count))
        self._spread_preview_label.setText("\n".join(lines))

    def _slot_text(
        self,
        page_id: str | None,
        names: Mapping[str, str],
        folios: Mapping[str, str | None],
        position: Mapping[str, int],
    ) -> str:
        if page_id is None:
            return self._translate_runtime("(empty)")
        text = f"{position.get(page_id, 0) + 1}. {names.get(page_id, page_id)}"
        folio = folios.get(page_id)
        return f"{text} [{folio}]" if folio else text

    def _refresh_folio_example(self) -> None:
        """A live sample of what an auto folio will look like."""
        example = f"{self._folio_prefix_edit.text()}{self._folio_start_spin.value()}"
        self._folio_example_label.setText(
            self._translate_runtime("Example: {folio}").format(folio=example)
        )

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._refresh_folio_example()
        if self._preview_pages:
            self._refresh_spread_preview()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ComicBookSettingsDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
