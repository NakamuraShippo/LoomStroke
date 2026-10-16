"""Export Image dialog (CT-09 spec §6): format, colour, alpha, options, warnings.

The dialog never encodes anything.  It shows the format capabilities, the
colour / alpha policy and the per-format encoder options, previews the
issues the export contract would raise for that combination, and returns
an :class:`ExportImageRequest` with stable ids.  PSD / SVG (layered
formats) keep their own exporters, so the raster-only controls are
disabled for them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...io.export_contract import ExportIssue, capabilities, resolve_alpha
from ...io.export_image_request import ExportImageRequest
from ..translations import TranslationBinder, translate_with_fallback, translated_literal

#: (stable format id, source label)
_FORMATS: tuple[tuple[str, str], ...] = (
    ("png", "PNG"),
    ("jpeg", "JPEG"),
    ("tiff", "TIFF"),
    ("webp", "WebP"),
    ("bmp", "BMP"),
    ("psd", "PSD (layered)"),
    ("svg", "SVG (vector)"),
)
_TIFF_COMPRESSIONS: tuple[tuple[str, str], ...] = (
    ("tiff_lzw", "LZW"),
    ("tiff_adobe_deflate", "Deflate (ZIP)"),
    ("packbits", "PackBits"),
    ("raw", "None"),
)
_ISSUE_LABELS: dict[str, str] = {
    "alpha_flattened_to_background": "Transparency will be flattened onto the background.",
    "icc_not_stored": "This format cannot embed an ICC profile.",
    "dpi_not_stored": "This format does not store DPI metadata.",
    "document_profile_missing": "The document has no ICC profile; the file is exported untagged "
    "(treated as sRGB).",
    "source_assumed_srgb": "The document has no ICC profile; pixels are assumed to be sRGB.",
    "layered_exporter": "Layered formats use their own exporter; colour and alpha options "
    "do not apply.",
}


class ExportImageDialog(QDialog):
    """Collect an :class:`ExportImageRequest` for the active page."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        dpi: float,
        document_profile_name: str | None,
        background_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
        defaults: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._dpi = max(1.0, float(dpi))
        self._document_profile_name = document_profile_name or None
        self._background_rgba = tuple(int(part) for part in background_rgba)
        self._matte_rgba: tuple[int, int, int, int] = (
            int(self._background_rgba[0]),
            int(self._background_rgba[1]),
            int(self._background_rgba[2]),
            255,
        )
        self._defaults = dict(defaults or {})
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translation_binder.register(self.setWindowTitle, self._ts("Export Image"))
        self._build_widgets()
        self._build_layout()
        self._connect_signals()
        self._refresh()

    # -- public -----------------------------------------------------------------

    def request(self) -> ExportImageRequest:
        format_id = str(self._format_combo.currentData())
        matte = self._matte_rgba if self._matte_radio.isChecked() else None
        return ExportImageRequest(
            format_id=format_id,
            color_policy=str(self._color_combo.currentData()),  # type: ignore[arg-type]
            alpha_mode="matte" if self._matte_radio.isChecked() else "preserve",
            matte_rgba=matte,
            encoder_options=self._encoder_options(format_id),
        )

    def select_format(self, format_id: str) -> None:
        index = self._format_combo.findData(format_id)
        if index >= 0:
            self._format_combo.setCurrentIndex(index)

    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self._preview_issues())

    # -- construction -----------------------------------------------------------

    def _build_widgets(self) -> None:
        self._build_choice_widgets()
        self._build_option_widgets()

    def _build_choice_widgets(self) -> None:
        self._format_combo = QComboBox(self)
        for format_id, label in _FORMATS:
            self._format_combo.addItem(self._translate_runtime(label), format_id)
        self._info_label = QLabel(self)
        self._capability_label = QLabel(self)
        self._capability_label.setWordWrap(True)

        self._color_combo = QComboBox(self)
        self._color_combo.addItem("", "embed")
        self._color_combo.addItem("", "untagged")
        self._color_combo.addItem("", "convert_srgb")

        self._preserve_radio = QRadioButton(self)
        self._preserve_radio.setChecked(True)
        self._matte_radio = QRadioButton(self)
        self._matte_button = QPushButton(self)
        self._translation_binder.register(
            self._preserve_radio.setText, self._ts("Preserve transparency")
        )
        self._translation_binder.register(
            self._matte_radio.setText, self._ts("Flatten onto a colour")
        )

        self._jpeg_quality = QSpinBox(self)
        self._jpeg_quality.setRange(1, 100)
        self._jpeg_quality.setValue(int(self._defaults.get("jpeg_quality", 95)))
        self._jpeg_progressive = QCheckBox(self)
        self._jpeg_progressive.setChecked(bool(self._defaults.get("jpeg_progressive", False)))
        self._translation_binder.register(self._jpeg_progressive.setText, self._ts("Progressive"))
        self._png_compress = QSpinBox(self)
        self._png_compress.setRange(0, 9)
        self._png_compress.setValue(int(self._defaults.get("png_compress_level", 6)))
        self._tiff_compression = QComboBox(self)
        for value, label in _TIFF_COMPRESSIONS:
            self._tiff_compression.addItem(self._translate_runtime(label), value)
        default_tiff = self._tiff_compression.findData(
            self._defaults.get("tiff_compression", "tiff_lzw")
        )
        if default_tiff >= 0:
            self._tiff_compression.setCurrentIndex(default_tiff)
        self._webp_quality = QSpinBox(self)
        self._webp_quality.setRange(1, 100)
        self._webp_quality.setValue(int(self._defaults.get("webp_quality", 90)))
        self._webp_lossless = QCheckBox(self)
        self._webp_lossless.setChecked(bool(self._defaults.get("webp_lossless", False)))
        self._translation_binder.register(self._webp_lossless.setText, self._ts("Lossless"))

    def _build_option_widgets(self) -> None:
        self._options_stack = QStackedWidget(self)
        self._option_pages: dict[str, int] = {}
        self._option_pages["png"] = self._options_stack.addWidget(
            self._option_page([("Compression level:", self._png_compress)])
        )
        self._option_pages["jpeg"] = self._options_stack.addWidget(
            self._option_page([("Quality:", self._jpeg_quality), (None, self._jpeg_progressive)])
        )
        self._option_pages["tiff"] = self._options_stack.addWidget(
            self._option_page([("Compression:", self._tiff_compression)])
        )
        self._option_pages["webp"] = self._options_stack.addWidget(
            self._option_page([("Quality:", self._webp_quality), (None, self._webp_lossless)])
        )
        none_page = QWidget(self)
        none_label = QLabel(none_page)
        self._translation_binder.register(
            none_label.setText, self._ts("No options for this format.")
        )
        QVBoxLayout(none_page).addWidget(none_label)
        self._option_pages["none"] = self._options_stack.addWidget(none_page)

        self._include_label = QLabel(self)
        self._include_label.setWordWrap(True)
        self._translation_binder.register(
            self._include_label.setText,
            self._ts(
                "Exports the visible layer composite of the active page. Guides, the "
                "selection outline and tool overlays are never included."
            ),
        )
        self._warnings_label = QLabel(self)
        self._warnings_label.setWordWrap(True)
        self._warnings_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def _option_page(self, rows: list[tuple[str | None, QWidget]]) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        for label_source, widget in rows:
            if label_source is None:
                form.addRow(widget)
            else:
                form.addRow(self._label(label_source, page), widget)
        return page

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        format_group = QGroupBox(self)
        self._translation_binder.register(format_group.setTitle, self._ts("Format"))
        format_form = self._form(format_group)
        format_form.addRow(self._label("Format:", format_group), self._format_combo)
        format_form.addRow(self._label("Image:", format_group), self._info_label)
        format_form.addRow(self._label("Supports:", format_group), self._capability_label)
        layout.addWidget(format_group)

        self._color_group = QGroupBox(self)
        self._translation_binder.register(self._color_group.setTitle, self._ts("Colour Profile"))
        color_form = self._form(self._color_group)
        color_form.addRow(self._label("Profile:", self._color_group), self._color_combo)
        layout.addWidget(self._color_group)

        self._alpha_group = QGroupBox(self)
        self._translation_binder.register(self._alpha_group.setTitle, self._ts("Transparency"))
        alpha_form = self._form(self._alpha_group)
        alpha_form.addRow(self._preserve_radio)
        alpha_form.addRow(self._matte_radio, self._matte_button)
        layout.addWidget(self._alpha_group)

        self._options_group = QGroupBox(self)
        self._translation_binder.register(self._options_group.setTitle, self._ts("Format Options"))
        QVBoxLayout(self._options_group).addWidget(self._options_stack)
        layout.addWidget(self._options_group)

        summary_group = QGroupBox(self)
        self._translation_binder.register(summary_group.setTitle, self._ts("Included Content"))
        summary_layout = QVBoxLayout(summary_group)
        summary_layout.addWidget(self._include_label)
        layout.addWidget(summary_group)

        warnings_group = QGroupBox(self)
        self._translation_binder.register(warnings_group.setTitle, self._ts("Warnings"))
        QVBoxLayout(warnings_group).addWidget(self._warnings_label)
        layout.addWidget(warnings_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button is not None:
            self._translation_binder.register(ok_button.setText, self._ts("Export..."))
        if cancel_button is not None:
            self._translation_binder.register(cancel_button.setText, self._ts("Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _form(self, group: QGroupBox) -> QFormLayout:
        form = QFormLayout(group)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        return form

    def _label(self, source: str, parent: QWidget) -> QLabel:
        label = QLabel(parent)
        self._translation_binder.register(label.setText, self._ts(source))
        return label

    def _connect_signals(self) -> None:
        self._format_combo.currentIndexChanged.connect(self._refresh)
        self._color_combo.currentIndexChanged.connect(self._refresh)
        self._preserve_radio.toggled.connect(self._refresh)
        self._matte_radio.toggled.connect(self._refresh)
        self._matte_button.clicked.connect(self._pick_matte)

    # -- behaviour --------------------------------------------------------------

    def _pick_matte(self) -> None:
        current = QColor(*self._matte_rgba[:3])
        chosen = QColorDialog.getColor(current, self, self._translate_runtime("Matte colour"))
        if chosen.isValid():
            self._matte_rgba = (chosen.red(), chosen.green(), chosen.blue(), 255)
            self._matte_radio.setChecked(True)
            self._refresh()

    def _refresh(self) -> None:
        format_id = str(self._format_combo.currentData())
        caps = capabilities(format_id)
        raster = not caps.layered
        self._color_group.setEnabled(raster)
        self._alpha_group.setEnabled((raster and caps.supports_alpha) or raster)
        self._options_group.setEnabled(raster)
        self._options_stack.setCurrentIndex(
            self._option_pages.get(format_id, self._option_pages["none"])
        )
        self._matte_button.setText("#{:02X}{:02X}{:02X}".format(*self._matte_rgba[:3]))
        self._matte_button.setEnabled(self._matte_radio.isChecked())
        planner_mm = self._width / self._dpi * 25.4, self._height / self._dpi * 25.4
        self._info_label.setText(
            self._translate_runtime("{width} x {height} px, {dpi} dpi ({mm_w} x {mm_h} mm)").format(
                width=self._width,
                height=self._height,
                dpi=f"{self._dpi:g}",
                mm_w=f"{planner_mm[0]:.1f}",
                mm_h=f"{planner_mm[1]:.1f}",
            )
        )
        yes = self._translate_runtime("yes")
        no = self._translate_runtime("no")
        self._capability_label.setText(
            self._translate_runtime("alpha: {alpha} / ICC: {icc} / DPI: {dpi}").format(
                alpha=yes if caps.supports_alpha else no,
                icc=yes if caps.supports_icc else no,
                dpi=yes if caps.supports_dpi else no,
            )
        )
        self._retranslate_color_items()
        issues = self._preview_issues()
        if issues:
            lines = [
                "- " + self._translate_runtime(_ISSUE_LABELS.get(issue.code, issue.message))
                for issue in issues
            ]
            self._warnings_label.setText("\n".join(lines))
        else:
            self._warnings_label.setText(self._translate_runtime("No warnings."))

    def _retranslate_color_items(self) -> None:
        if self._document_profile_name:
            embed = self._translate_runtime("Embed document profile ({name})").format(
                name=self._document_profile_name
            )
        else:
            embed = self._translate_runtime("Embed document profile (none - exported untagged)")
        self._color_combo.setItemText(0, embed)
        self._color_combo.setItemText(1, self._translate_runtime("Untagged (no profile)"))
        self._color_combo.setItemText(2, self._translate_runtime("Convert to sRGB and embed"))

    def _preview_issues(self) -> tuple[ExportIssue, ...]:
        format_id = str(self._format_combo.currentData())
        caps = capabilities(format_id)
        if caps.layered:
            return (ExportIssue(code="layered_exporter", message=""),)
        issues: list[ExportIssue] = []
        policy = str(self._color_combo.currentData())
        if policy == "embed" and not self._document_profile_name:
            issues.append(ExportIssue(code="document_profile_missing", message=""))
        if policy == "convert_srgb" and not self._document_profile_name:
            issues.append(ExportIssue(code="source_assumed_srgb", message=""))
        wants_icc = policy == "convert_srgb" or (policy == "embed" and self._document_profile_name)
        if wants_icc and not caps.supports_icc:
            issues.append(ExportIssue(code="icc_not_stored", message=""))
        if not caps.supports_dpi:
            issues.append(ExportIssue(code="dpi_not_stored", message=""))
        alpha_mode = "matte" if self._matte_radio.isChecked() else "preserve"
        resolved = resolve_alpha(
            format_id,
            alpha_mode=alpha_mode,  # type: ignore[arg-type]
            matte_rgba=self._matte_rgba if alpha_mode == "matte" else None,
            background_rgba=self._background_rgba,  # type: ignore[arg-type]
        )
        issues.extend(resolved.issues)
        return tuple(issues)

    def _encoder_options(self, format_id: str) -> dict[str, Any]:
        if format_id == "jpeg":
            return {
                "quality": int(self._jpeg_quality.value()),
                "progressive": bool(self._jpeg_progressive.isChecked()),
            }
        if format_id == "png":
            return {"compress_level": int(self._png_compress.value())}
        if format_id == "tiff":
            return {"compression": str(self._tiff_compression.currentData())}
        if format_id == "webp":
            return {
                "quality": int(self._webp_quality.value()),
                "lossless": bool(self._webp_lossless.isChecked()),
            }
        return {}

    # -- i18n -------------------------------------------------------------------

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        for index, (_format_id, label) in enumerate(_FORMATS):
            self._format_combo.setItemText(index, self._translate_runtime(label))
        for index, (_value, label) in enumerate(_TIFF_COMPRESSIONS):
            self._tiff_compression.setItemText(index, self._translate_runtime(label))
        self._refresh()

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("ExportImageDialog", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()
