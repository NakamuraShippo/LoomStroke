"""Preset metadata form (Phase 7-d).

Centre-pane widget of the Effect Editor. Collects the ``manifest.json``
metadata the author must supply before a ``.lsfx`` can be exported — the
things the workflow file itself can't tell us (display name, category,
description, tags, trigger, optional icon).

Kept structurally independent from :class:`ChoiceInspector` so the form
can be unit-tested without wiring a candidate list. The form holds no
other state than its widgets: callers read ``collect()`` at export time.

Design choices:
- Category / trigger combo boxes are pinned to ``manifest_validator``'s
  VALID_* sets so any new value there propagates here automatically.
- Icon is held as raw bytes rather than a path so the atomic write in
  ``write_lsfx_atomic`` does not depend on the user's filesystem state
  between "Choose icon..." and "Export preset...".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from loomstroke.plugins.ai_effect.lsfx_io import MAX_ASSET_SIZE
from loomstroke.plugins.ai_effect.manifest_validator import (
    VALID_CATEGORIES,
    VALID_TRIGGERS,
)
from loomstroke.ui.translations import translate_with_fallback


def _tr(text: str) -> str:
    return translate_with_fallback("MetadataForm", text)


# Ordered tuples for stable combo indices; sorted() to avoid the frozenset
# iteration order leaking into the UI.
_CATEGORY_ORDER: tuple[str, ...] = tuple(sorted(VALID_CATEGORIES))
_TRIGGER_ORDER: tuple[str, ...] = tuple(sorted(VALID_TRIGGERS))

# Pulled from lsfx_io so the editor's upfront size cap tracks the archive
# reader's per-entry cap — a user-picked icon that would later fail the
# zip-bomb check in read_lsfx is rejected here instead, with a clearer
# error pathway.
_MAX_ICON_BYTES = MAX_ASSET_SIZE
# Phase 7-e: preview.png is capped by the same asset-size budget (20 MiB)
# as the icon. Keeping the two constants separate documents the intent
# even though they share a value — if the spec later relaxes or tightens
# one independently, only one constant needs updating.
_MAX_PREVIEW_BYTES = MAX_ASSET_SIZE


@dataclass
class ExportMetadata:
    """Snapshot of :class:`MetadataForm` contents, ready for export."""

    name: str = ""
    category: str = "other"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    trigger: str = "manual"
    icon_bytes: bytes | None = None
    # Phase 7-e: author-provided preview.png bytes, symmetric to
    # icon_bytes. Used as the thumbnail in Effect Panel. Independent of
    # any preset-execution output — the author picks whatever represents
    # the preset best.
    preview_bytes: bytes | None = None


class MetadataForm(QWidget):
    """QFormLayout-driven editor for ``manifest.json`` metadata fields.

    Emits :attr:`metadata_changed` whenever any user-editable field is
    touched (including icon changes). The host uses the signal to refresh
    the enable state of the Export action rather than re-reading the
    whole form on every keystroke.
    """

    metadata_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._icon_bytes: bytes | None = None
        self._icon_source_name: str = ""
        self._preview_bytes: bytes | None = None
        self._preview_source_name: str = ""

        form = QFormLayout(self)
        form.setContentsMargins(8, 8, 8, 8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(_tr("Required"))

        # Editable combo: pre-fills with the canonical categories
        # (``VALID_CATEGORIES``) but accepts free-form user text. The
        # manifest validator only emits a warning for unknown
        # categories so authors can organise their library however
        # they like ("LineArt XL", "実験用", etc.) without breaking
        # the round-trip.
        self._category_combo = QComboBox()
        self._category_combo.setEditable(True)
        self._category_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for cat in _CATEGORY_ORDER:
            self._category_combo.addItem(cat, cat)
        self._category_combo.setCurrentText("other")

        self._description_edit = QPlainTextEdit()
        self._description_edit.setPlaceholderText(_tr("Optional preset description"))
        self._description_edit.setFixedHeight(90)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText(_tr("comma-separated"))

        self._trigger_combo = QComboBox()
        for trig in _TRIGGER_ORDER:
            self._trigger_combo.addItem(trig, trig)
        self._set_combo_by_data(self._trigger_combo, "manual")

        # Icon picker: Browse button + inline status label. We store bytes
        # rather than path so later user filesystem churn can't corrupt
        # the deferred export.
        icon_row = QWidget()
        icon_layout = QHBoxLayout(icon_row)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        self._icon_button = QPushButton(_tr("Choose icon..."))
        self._icon_button.clicked.connect(self._on_choose_icon)
        self._icon_label = QLabel(_tr("(none)"))
        self._icon_label.setStyleSheet("color: palette(mid);")
        icon_layout.addWidget(self._icon_button)
        icon_layout.addWidget(self._icon_label, stretch=1)

        # Preview picker (Phase 7-e): mirrors the icon row one-to-one so
        # the author notices the pattern immediately. Preview is a
        # pure-authored asset — not derived from executing the preset.
        preview_row = QWidget()
        preview_layout = QHBoxLayout(preview_row)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_button = QPushButton(_tr("Choose preview..."))
        self._preview_button.clicked.connect(self._on_choose_preview)
        self._preview_label = QLabel(_tr("(none)"))
        self._preview_label.setStyleSheet("color: palette(mid);")
        preview_layout.addWidget(self._preview_button)
        preview_layout.addWidget(self._preview_label, stretch=1)

        form.addRow(_tr("Name"), self._name_edit)
        form.addRow(_tr("Category"), self._category_combo)
        form.addRow(_tr("Description"), self._description_edit)
        form.addRow(_tr("Tags"), self._tags_edit)
        form.addRow(_tr("Trigger"), self._trigger_combo)
        form.addRow(_tr("Icon"), icon_row)
        form.addRow(_tr("Preview"), preview_row)

        self._name_edit.textChanged.connect(self._emit_changed)
        # editTextChanged covers both list-pick + free-form typing for
        # an editable combo; currentIndexChanged would miss the latter.
        self._category_combo.editTextChanged.connect(self._emit_changed)
        self._description_edit.textChanged.connect(self._emit_changed)
        self._tags_edit.textChanged.connect(self._emit_changed)
        self._trigger_combo.currentIndexChanged.connect(self._emit_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> ExportMetadata:
        """Snapshot the current form state into :class:`ExportMetadata`."""
        # Free-form category: prefer the visible text (which may be a
        # custom user-typed value) over the combo's stored ``data``
        # role — the latter only exists for canonical entries the form
        # was seeded with. Falls back to ``"other"`` for empty/whitespace.
        cat_text = self._category_combo.currentText().strip()
        return ExportMetadata(
            name=self._name_edit.text().strip(),
            category=cat_text or "other",
            description=self._description_edit.toPlainText(),
            tags=_parse_tags(self._tags_edit.text()),
            trigger=str(self._trigger_combo.currentData() or "manual"),
            icon_bytes=self._icon_bytes,
            preview_bytes=self._preview_bytes,
        )

    # ------------------------------------------------------------------
    # Thin field accessors — lets callers (host window, tests) touch the
    # preset metadata without reaching into the underlying Qt widgets
    # (Phase 7-d-fix-G / review #14).
    # ------------------------------------------------------------------

    def preset_name(self) -> str:
        return self._name_edit.text()

    def set_preset_name(self, value: str) -> None:
        self._name_edit.setText(value)

    def preset_description(self) -> str:
        return self._description_edit.toPlainText()

    def set_preset_description(self, value: str) -> None:
        self._description_edit.setPlainText(value)

    def preset_tags_text(self) -> str:
        return self._tags_edit.text()

    def set_preset_tags_text(self, value: str) -> None:
        self._tags_edit.setText(value)

    def preview_bytes(self) -> bytes | None:
        """Current preview.png bytes, or ``None`` when none chosen."""
        return self._preview_bytes

    def preview_source_name(self) -> str:
        """Display hint for the preview row (filename of the picked
        image, ``"imported preview"`` after an import, or empty for the
        bare ``set_preview_bytes`` default)."""
        return self._preview_source_name

    def set_defaults(self, display_name: str = "") -> None:
        """Reset the form to neutral defaults.

        Intended to be called when a new workflow is loaded. Prefills the
        name with the workflow file stem so authors rarely have to type
        one from scratch.
        """
        # Block signals during reset so we do not fire ``metadata_changed``
        # once per field — the caller probably triggers a separate refresh
        # when the workflow loads.
        for w in (
            self._name_edit,
            self._category_combo,
            self._description_edit,
            self._tags_edit,
            self._trigger_combo,
        ):
            w.blockSignals(True)
        try:
            self._name_edit.setText(display_name)
            # Editable combo: setCurrentText accepts arbitrary strings
            # so we don't need to lean on the data-role lookup.
            self._category_combo.setCurrentText("other")
            self._description_edit.setPlainText("")
            self._tags_edit.setText("")
            self._set_combo_by_data(self._trigger_combo, "manual")
            self._icon_bytes = None
            self._icon_source_name = ""
            self._icon_label.setText(_tr("(none)"))
            self._preview_bytes = None
            self._preview_source_name = ""
            self._preview_label.setText(_tr("(none)"))
        finally:
            for w in (
                self._name_edit,
                self._category_combo,
                self._description_edit,
                self._tags_edit,
                self._trigger_combo,
            ):
                w.blockSignals(False)
        self._emit_changed()

    def apply_metadata(self, metadata: ExportMetadata) -> None:
        """Replace every form field with values from ``metadata``.

        Used by the Phase 7-f import path to restore the author's
        previously saved metadata into the form. Signals are blocked
        during the bulk update so only a single ``metadata_changed``
        fires at the end, matching the invariant other bulk setters
        (``set_defaults``) already establish.

        Tags are re-joined with ``", "`` so the author sees a consistent
        comma-separated representation; the form re-normalises them at
        ``collect`` time via :func:`_parse_tags`.
        """
        widgets = (
            self._name_edit,
            self._category_combo,
            self._description_edit,
            self._tags_edit,
            self._trigger_combo,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            self._name_edit.setText(metadata.name)
            # Custom user-defined categories survive the import →
            # editor → re-export round-trip because the editable
            # combo accepts arbitrary text. Canonical categories
            # still pick the matching dropdown entry; unknown ones
            # become the typed text.
            self._category_combo.setCurrentText(metadata.category or "other")
            self._description_edit.setPlainText(metadata.description)
            self._tags_edit.setText(", ".join(metadata.tags))
            self._set_combo_by_data(self._trigger_combo, metadata.trigger)
        finally:
            for w in widgets:
                w.blockSignals(False)
        # Icon state lives outside the signal-blocked widget set; update
        # it directly so the label reflects whatever the preset carried.
        self._icon_bytes = metadata.icon_bytes
        self._icon_source_name = ""
        if metadata.icon_bytes is None:
            self._icon_label.setText(_tr("(none)"))
        else:
            self._icon_label.setText(_tr("(icon set)"))
        # Preview is handled symmetrically — distinct label text so the
        # author can tell "came from the imported .lsfx" apart from a
        # freshly picked one ("<filename>") and an absent one ("(none)").
        self._preview_bytes = metadata.preview_bytes
        self._preview_source_name = ""
        if metadata.preview_bytes is None:
            self._preview_label.setText(_tr("(none)"))
        else:
            self._preview_label.setText(_tr("(imported preview)"))
        self._emit_changed()

    def set_icon_bytes(self, data: bytes | None, source_name: str = "") -> None:
        """Programmatically inject icon bytes (used by tests / 7-f import)."""
        self._icon_bytes = data
        self._icon_source_name = source_name
        if data is None:
            label_text = _tr("(none)")
        elif source_name:
            label_text = source_name
        else:
            label_text = _tr("(icon set)")
        self._icon_label.setText(label_text)
        self._emit_changed()

    def set_preview_bytes(self, data: bytes | None, source_name: str = "") -> None:
        """Programmatically inject preview bytes (Phase 7-e / tests).

        Mirror of :meth:`set_icon_bytes` — picks the label text from
        ``source_name`` > generic ``"(preview set)"`` > ``"(none)"`` so
        the UI state tracks the injection origin.
        """
        self._preview_bytes = data
        self._preview_source_name = source_name
        if data is None:
            label_text = _tr("(none)")
        elif source_name:
            label_text = source_name
        else:
            label_text = _tr("(preview set)")
        self._preview_label.setText(label_text)
        self._emit_changed()

    def is_ready_for_export(self) -> bool:
        """Cheap predicate the host uses to toggle the Export action."""
        return bool(self._name_edit.text().strip())

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_choose_icon(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _tr("Choose icon"),
            "",
            _tr("Image files (*.png *.jpg *.jpeg);;All files (*)"),
        )
        if not path:
            return
        file_path = Path(path)
        # Single open + bounded read closes the TOCTOU window between the
        # stat-based size check and the actual read: the file cannot be
        # swapped out from under us once the handle is open, and reading
        # one byte past the cap is enough to detect an over-size file
        # without materialising it in memory.
        try:
            with file_path.open("rb") as fh:
                data = fh.read(_MAX_ICON_BYTES + 1)
        except OSError:
            self._icon_label.setText(_tr("(read error)"))
            return
        if len(data) > _MAX_ICON_BYTES:
            self._icon_label.setText(
                _tr("(too large, max %d MiB)") % (_MAX_ICON_BYTES // (1024 * 1024))
            )
            return
        self._icon_bytes = data
        self._icon_source_name = file_path.name
        self._icon_label.setText(file_path.name)
        self._emit_changed()

    def _on_choose_preview(self) -> None:
        """Phase 7-e: mirror of :meth:`_on_choose_icon` for ``preview.png``.

        The two pickers share the same image-file filter and TOCTOU-safe
        bounded read. Structural duplication (vs. factoring into a
        shared helper) is intentional for now — the two rows may grow
        asymmetric behaviours (resize preview on pick, display inline
        thumbnail, etc.) and the shared helper would become lossy.
        """
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _tr("Choose preview"),
            "",
            _tr("Image files (*.png *.jpg *.jpeg);;All files (*)"),
        )
        if not path:
            return
        file_path = Path(path)
        try:
            with file_path.open("rb") as fh:
                data = fh.read(_MAX_PREVIEW_BYTES + 1)
        except OSError:
            self._preview_label.setText(_tr("(read error)"))
            return
        if len(data) > _MAX_PREVIEW_BYTES:
            self._preview_label.setText(
                _tr("(too large, max %d MiB)") % (_MAX_PREVIEW_BYTES // (1024 * 1024))
            )
            return
        self._preview_bytes = data
        self._preview_source_name = file_path.name
        self._preview_label.setText(file_path.name)
        self._emit_changed()

    def _emit_changed(self) -> None:
        self.metadata_changed.emit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)


# Tag normalisation rules (Phase 7-d-fix-H / review #16): no AIeffect spec
# mandates these, so we adopt conventions common in tagging UIs (GitHub,
# Discourse) — lower-case, single-token, URL-safe character set, capped
# length and count. Applied silently at ``collect`` time; the UI edit field
# preserves the raw user string until export.
_MAX_TAG_LENGTH = 32
_MAX_TAGS_PER_PRESET = 20
_TAG_INVALID_CHARS = re.compile(r"[^a-z0-9_-]+")


def _normalize_tag(raw: str) -> str | None:
    """Lower-case, strip, and sanitise a single tag.

    Returns ``None`` if the tag is empty or collapses to empty after
    removing invalid characters. Non-alphanumeric runs (whitespace
    included) collapse to a single ``_``; leading / trailing ``_-`` are
    trimmed so the output stays URL-/manifest-safe.
    """
    lowered = raw.strip().lower()
    if not lowered:
        return None
    normalised = _TAG_INVALID_CHARS.sub("_", lowered).strip("_-")
    if not normalised:
        return None
    if len(normalised) > _MAX_TAG_LENGTH:
        normalised = normalised[:_MAX_TAG_LENGTH]
    return normalised


def _parse_tags(raw: str) -> list[str]:
    """Split a comma-separated tag string into a normalised, deduped list.

    Applies :func:`_normalize_tag` to each comma-separated segment.
    Duplicates (after normalisation) are dropped, preserving the order
    of first occurrence. The list is capped at ``_MAX_TAGS_PER_PRESET``
    entries — extra tags beyond the cap are silently ignored so long
    paste-ins do not balloon the manifest.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in raw.split(","):
        tag = _normalize_tag(chunk)
        if tag is None or tag in seen:
            continue
        seen.add(tag)
        ordered.append(tag)
        if len(ordered) >= _MAX_TAGS_PER_PRESET:
            break
    return ordered


__all__ = ["ExportMetadata", "MetadataForm"]
