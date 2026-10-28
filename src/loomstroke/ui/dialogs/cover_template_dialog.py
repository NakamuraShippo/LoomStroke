"""Save a wrap-cover profile in the existing page template library."""

from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

from ...core.comic_document_profile import canvas_pixel_size
from ...core.cover_profile import CoverProfile, cover_template
from ..translations import translate_with_fallback


class CoverTemplateDialog(QDialog):
    def __init__(self, store, *, page_count=32, binding="right", parent=None):
        super().__init__(parent)
        self.store = store
        self.labels = []
        form = QFormLayout(self)
        self.name = QLineEdit(self)
        self.name.setMaxLength(64)
        self._row(form, "Template name", self.name)
        self.binding = QComboBox(self)
        for value in ("right", "left"):
            self.binding.addItem("", value)
        self.binding.setCurrentIndex(max(0, self.binding.findData(binding)))
        self._row(form, "Binding", self.binding)
        self.fields = {}
        for key, source, low, high, value in (
            ("width", "Face width (mm)", 1, 1000, 148),
            ("height", "Face height (mm)", 1, 1000, 210),
            ("pages", "Body pages", 1, 100000, max(1, page_count)),
            ("paper", "Sheet thickness (mm)", 0, 10, 0.1),
            ("spine", "Spine width (mm)", 0, 1000, 1.6),
            ("flap", "Flap width on each side (mm)", 0, 1000, 0),
            ("bleed", "Bleed (mm)", 0, 100, 3),
            ("dpi", "DPI", 1, 2400, 350),
        ):
            spin = QSpinBox(self) if key == "pages" else QDoubleSpinBox(self)
            spin.setRange(low, high)
            if isinstance(spin, QDoubleSpinBox):
                spin.setDecimals(4)
            spin.setValue(value)
            self.fields[key] = spin
            self._row(form, source, spin)
        self.direct = QCheckBox(self)
        form.addRow(self.direct)
        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for spin in self.fields.values():
            spin.valueChanged.connect(self._update_summary)
        self.direct.toggled.connect(self._update_summary)
        self._retranslate()

    def _row(self, form, source, widget):
        label = QLabel(self)
        self.labels.append((label, source))
        form.addRow(label, widget)

    def profile_value(self):
        v = {k: s.value() for k, s in self.fields.items()}
        return CoverProfile(
            trim_mm=(v["width"], v["height"]),
            page_count=v["pages"],
            paper_mm=v["paper"],
            spine_mm=v["spine"] if self.direct.isChecked() else None,
            flap_mm=v["flap"],
            bleed_mm=v["bleed"],
            dpi=(v["dpi"], v["dpi"]),
            binding=self.binding.currentData(),
        )

    def _update_summary(self):
        self.fields["spine"].setEnabled(self.direct.isChecked())
        try:
            profile = self.profile_value()
            width, height = canvas_pixel_size(profile.layout)
            self.summary.setText(
                self._ts(
                    "Spine {spine} mm; canvas {width} x {height} px. "
                    "Saved templates appear in the Pages panel."
                ).format(spine=f"{profile.spine_width:g}", width=width, height=height)
            )
        except ValueError:
            self.summary.setText(self._ts("Invalid cover dimensions or pixel limit exceeded."))

    def accept(self):
        try:
            template = cover_template(
                self.profile_value(),
                name=self.name.text(),
                template_id="comic.cover." + uuid4().hex,
            )
            self.store.save_user_template(template)
        except (ValueError, OSError, RuntimeError):
            self.error.setText(self._ts("Could not save the cover template."))
            return
        super().accept()

    @staticmethod
    def _ts(source):
        return translate_with_fallback("CoverTemplateDialog", source)

    def _retranslate(self):
        self.setWindowTitle(self._ts("Cover Template"))
        for label, source in self.labels:
            label.setText(self._ts(source))
        for index, source in enumerate(("Right binding", "Left binding")):
            self.binding.setItemText(index, self._ts(source))
        self.direct.setText(self._ts("Specify spine width directly"))
        if self.error.text():
            self.error.setText(self._ts("Could not save the cover template."))
        self._update_summary()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()


def open_cover_template(window):
    pages = getattr(getattr(window, "_host_api", None), "pages", None)
    manager = getattr(pages, "_template_manager", None)
    if manager is None:
        return
    document = window._get_document_service()
    resolve = getattr(document, "resolve_book_layout", None)
    layout = resolve() if callable(resolve) else None
    CoverTemplateDialog(
        manager.template_store,
        page_count=len(pages.list_pages()),
        binding=layout.binding if layout else "right",
        parent=window,
    ).exec()
