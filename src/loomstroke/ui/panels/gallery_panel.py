"""Common generation history gallery panel.

Provides a thumbnail grid with detail view, action buttons, and layer
transfer controls.  Can be embedded in any plugin panel with an optional
``service_filter`` to restrict displayed records.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, QSize, Qt, Signal  # noqa: F811
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ai_selection.generation_preview_apply_readiness import (
    analyze_preview_apply_readiness,
)
from loomstroke.ui.translations import TranslationBinder, translated_literal

if TYPE_CHECKING:
    from loomstroke.core.generation_record import GenerationRecord
    from loomstroke.services.generation_history_store import GenerationHistoryStore

logger = logging.getLogger(__name__)

_THUMB_SIZE = 128
_ICON_SIZE = QSize(_THUMB_SIZE, _THUMB_SIZE)
_GRID_ICON_SIZE = QSize(_THUMB_SIZE + 8, _THUMB_SIZE + 28)


def _sam3_generation_diff_qa_metadata_lines(
    record: GenerationRecord,
    translate: Callable[[str], object],
) -> list[str]:
    snapshot = record.service_snapshot
    if not isinstance(snapshot, Mapping):
        return []
    sam3_snapshot = snapshot.get("sam3_ai_select")
    if not isinstance(sam3_snapshot, Mapping):
        return []
    qa = sam3_snapshot.get("generation_diff_qa")
    if not isinstance(qa, Mapping):
        return []

    changed_area_ratio = _metadata_ratio(qa.get("changed_area_ratio"))
    if changed_area_ratio is None:
        return []

    status_text = "Passed" if bool(qa.get("ok", False)) else "Warning"
    lines = [
        f"{translate('SAM3 QA')}: {translate(status_text)}",
        f"{translate('Changed Area')}: {_format_metadata_ratio(changed_area_ratio)}",
    ]

    protected_region_coverage = _metadata_ratio(qa.get("protected_region_coverage"))
    if protected_region_coverage is not None:
        lines.append(
            f"{translate('Protected Changed')}: "
            f"{_format_metadata_ratio(protected_region_coverage)}"
        )

    warning_codes = _metadata_string_list(qa.get("warning_codes"))
    if warning_codes:
        lines.append(f"{translate('Warnings')}: {', '.join(warning_codes)}")

    recommended_actions = _metadata_string_list(qa.get("recommended_actions"))
    if recommended_actions:
        lines.append(f"{translate('Recommended Actions')}: {', '.join(recommended_actions)}")

    return [str(line) for line in lines]


def _sam3_preview_apply_readiness_metadata_lines(
    record: GenerationRecord,
    translate: Callable[[str], object],
) -> list[str]:
    readiness = analyze_preview_apply_readiness(record)
    if readiness.blocked_reason_codes == ("sam3_preview_history_record_invalid",):
        return []

    status_text = "Ready" if readiness.ready else "Blocked"
    lines = [f"{translate('SAM3 Apply')}: {translate(status_text)}"]

    if readiness.blocked_reason_codes:
        lines.append(
            f"{translate('Apply Blocked')}: " f"{', '.join(readiness.blocked_reason_codes)}"
        )

    if readiness.output_asset_ids:
        lines.append(f"{translate('Output Assets')}: {len(readiness.output_asset_ids)}")

    if readiness.recommended_actions:
        lines.append(
            f"{translate('Recommended Actions')}: " f"{', '.join(readiness.recommended_actions)}"
        )

    return [str(line) for line in lines]


def _metadata_ratio(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        ratio = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ratio):
        return None
    return min(1.0, max(0.0, ratio))


def _format_metadata_ratio(value: float) -> str:
    return f"{value * 100.0:.1f}%"


def _metadata_string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


# =====================================================================
# Thumbnail grid
# =====================================================================


class _ThumbnailGrid(QListWidget):
    """Icon-mode list widget displaying generation thumbnails."""

    item_selected = Signal(str)  # job_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(_ICON_SIZE)
        self.setGridSize(_GRID_ICON_SIZE)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setWrapping(True)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.currentItemChanged.connect(self._on_current_changed)

    item_deselected = Signal()

    def _on_current_changed(self, current: QListWidgetItem | None, _prev: Any) -> None:
        if current:
            job_id = current.data(Qt.ItemDataRole.UserRole)
            if job_id:
                self.item_selected.emit(job_id)
        else:
            self.item_deselected.emit()

    def mousePressEvent(self, event: Any) -> None:
        """Clear selection when clicking on empty area."""
        index = self.indexAt(event.pos())
        if not index.isValid():
            self.clearSelection()
            self.setCurrentItem(cast(QListWidgetItem, None))
            self.item_deselected.emit()
        super().mousePressEvent(event)


# =====================================================================
# Detail view
# =====================================================================


class _DetailWidget(QWidget):
    """Shows record details: preview image, metadata, action buttons."""

    regenerate_requested = Signal(str)  # job_id
    restore_requested = Signal(str)  # job_id
    delete_requested = Signal(str)  # job_id
    transfer_requested = Signal(str, str)  # job_id, layer_id ("" = new)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_job_id: str | None = None
        self._current_prompt = ""
        self._translation_binder = TranslationBinder(self.tr)
        self._no_image_text = self._ts("No image")
        self._new_layer_text = self._ts("(New Layer)")
        self._setup_ui()

    def _ts(self, text: str) -> str:
        return translated_literal(self.tr, text)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Preview + metadata row ---
        top = QHBoxLayout()

        self._preview_label = QLabel()
        self._preview_label.setFixedSize(160, 160)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setScaledContents(False)
        top.addWidget(self._preview_label)

        self._meta_text = QTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._meta_text.setMaximumHeight(160)
        self._meta_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(self._meta_text, 1)
        layout.addLayout(top)

        # --- Action buttons ---
        btn_row = QHBoxLayout()
        self._btn_copy_prompt = QPushButton()
        self._translation_binder.register(
            self._btn_copy_prompt.setText, self._ts("Copy full prompt")
        )
        self._btn_copy_prompt.setEnabled(False)
        self._btn_regenerate = QPushButton()
        self._translation_binder.register(self._btn_regenerate.setText, self._ts("Regenerate"))
        self._btn_restore = QPushButton()
        self._translation_binder.register(self._btn_restore.setText, self._ts("Restore"))
        self._btn_delete = QPushButton()
        self._translation_binder.register(self._btn_delete.setText, self._ts("Delete"))
        for btn in (
            self._btn_copy_prompt,
            self._btn_regenerate,
            self._btn_restore,
            self._btn_delete,
        ):
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._btn_regenerate.clicked.connect(lambda: self._emit_action(self.regenerate_requested))
        self._btn_restore.clicked.connect(lambda: self._emit_action(self.restore_requested))
        self._btn_delete.clicked.connect(lambda: self._emit_action(self.delete_requested))
        self._btn_copy_prompt.clicked.connect(self._copy_prompt)

        # --- Layer transfer ---
        transfer_row = QHBoxLayout()
        self._transfer_label = QLabel()
        self._translation_binder.register(
            self._transfer_label.setText, self._ts("Transfer to layer:")
        )
        transfer_row.addWidget(self._transfer_label)
        self._layer_combo = QComboBox()
        self._layer_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        transfer_row.addWidget(self._layer_combo, 1)
        self._btn_transfer = QPushButton()
        self._translation_binder.register(self._btn_transfer.setText, self._ts("Transfer"))
        transfer_row.addWidget(self._btn_transfer)
        layout.addLayout(transfer_row)

        self._btn_transfer.clicked.connect(self._on_transfer_clicked)

        # Initial state
        self.setVisible(False)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    # ---- public API ----

    def show_record(
        self,
        record: GenerationRecord,
        base_path: Path,
        has_provider: bool,
    ) -> None:
        self._current_job_id = record.job_id
        self._current_prompt = record.prompt or ""
        self._btn_copy_prompt.setEnabled(bool(self._current_prompt))
        self._load_preview(record, base_path)
        self._load_metadata(record)
        self._btn_regenerate.setEnabled(has_provider)
        self._btn_restore.setEnabled(has_provider)
        self._btn_transfer.setEnabled(record.image_path is not None)
        self.setVisible(True)

    def clear(self) -> None:
        self._current_job_id = None
        self._current_prompt = ""
        self._btn_copy_prompt.setEnabled(False)
        self._preview_label.clear()
        self._meta_text.clear()
        self.setVisible(False)

    def set_layers(self, layers: list[tuple[str, str]]) -> None:
        """Update layer combo.  layers = [(layer_id, display_name), ...]."""
        self._layer_combo.clear()
        self._layer_combo.addItem(str(self._new_layer_text), "")
        for lid, name in layers:
            self._layer_combo.addItem(name, lid)

    # ---- internals ----

    def _load_preview(self, record: GenerationRecord, base_path: Path) -> None:
        pixmap: QPixmap | None = None
        if record.image_path:
            full = base_path / record.image_path
            if full.exists():
                img = QImage(str(full))
                if not img.isNull():
                    pixmap = QPixmap.fromImage(
                        img.scaled(
                            160,
                            160,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
        if pixmap:
            self._preview_label.setPixmap(pixmap)
        else:
            self._preview_label.setText(str(self._no_image_text))

    def _load_metadata(self, record: GenerationRecord) -> None:
        lines: list[str] = []
        if record.service_name:
            lines.append(f"{self._ts('Service')}: {record.service_name}")
        if record.model_name:
            lines.append(f"{self._ts('Model')}: {record.model_name}")
        if record.prompt:
            lines.append(f"{self._ts('Prompt')}: {record.prompt}")
        if record.seed is not None:
            lines.append(f"{self._ts('Seed')}: {record.seed}")
        if record.width and record.height:
            lines.append(f"{self._ts('Size')}: {record.width}x{record.height}")
        if record.duration_sec is not None:
            lines.append(f"{self._ts('Duration')}: {record.duration_sec:.1f}s")
        lines.append(f"{self._ts('Status')}: {record.status}")
        if record.created_at:
            lines.append(f"{self._ts('Time')}: {record.created_at}")
        if record.error:
            lines.append(f"{self._ts('Error')}: {record.error}")
        lines.extend(_sam3_generation_diff_qa_metadata_lines(record, self._ts))
        lines.extend(_sam3_preview_apply_readiness_metadata_lines(record, self._ts))
        self._meta_text.setPlainText("\n".join(lines))

    def _copy_prompt(self) -> None:
        if self._current_prompt:
            QApplication.clipboard().setText(self._current_prompt)

    def _emit_action(self, signal: Any) -> None:
        if self._current_job_id:
            signal.emit(self._current_job_id)

    def _on_transfer_clicked(self) -> None:
        if self._current_job_id:
            layer_id = self._layer_combo.currentData() or ""
            self.transfer_requested.emit(self._current_job_id, layer_id)


# =====================================================================
# GalleryPanel (main widget)
# =====================================================================


class GalleryPanel(QWidget):
    """Shared generation history gallery panel.

    Signals:
        regenerate_requested(str):       job_id
        restore_requested(str):          job_id
        delete_requested(str):           job_id
        transfer_to_layer_requested(str, str): job_id, layer_id ("" = new)
    """

    regenerate_requested = Signal(str)
    restore_requested = Signal(str)
    delete_requested = Signal(str)
    transfer_to_layer_requested = Signal(str, str)

    def __init__(
        self,
        history_store: GenerationHistoryStore,
        service_filter: str | None = None,
        host_api: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = history_store
        self._service_filter = service_filter
        self._host_api = host_api
        self._thumb_cache: dict[str, QIcon] = {}
        self._translation_binder = TranslationBinder(self.tr)
        self._setup_ui()
        self._connect_store()
        if host_api:
            self.transfer_to_layer_requested.connect(self._on_transfer_to_layer)
        self.refresh()

    def _ts(self, text: str) -> str:
        return translated_literal(self.tr, text)

    # ---- UI construction ----

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)

        # --- Filter bar ---
        filter_row = QHBoxLayout()

        self._status_combo = QComboBox()
        self._rebuild_status_combo()
        filter_row.addWidget(self._status_combo)

        self._service_combo = QComboBox()
        self._service_combo.addItem(str(self._ts("All Services")), "")
        filter_row.addWidget(self._service_combo)
        if self._service_filter:
            self._service_combo.setVisible(False)

        filter_row.addStretch()
        self._count_label = QLabel()
        filter_row.addWidget(self._count_label)
        root.addLayout(filter_row)

        self._status_combo.currentIndexChanged.connect(lambda _: self.refresh())
        self._service_combo.currentIndexChanged.connect(lambda _: self.refresh())

        # --- Splitter: grid + detail ---
        self._splitter = QSplitter(Qt.Orientation.Vertical)

        self._grid = _ThumbnailGrid()
        self._splitter.addWidget(self._grid)

        self._detail = _DetailWidget()
        self._splitter.addWidget(self._detail)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)

        root.addWidget(self._splitter, 1)

        # --- Wire grid → detail ---
        self._grid.item_selected.connect(self._on_item_selected)
        self._grid.item_deselected.connect(self._detail.clear)

        # --- Wire detail actions → panel signals ---
        self._detail.regenerate_requested.connect(self.regenerate_requested)
        self._detail.restore_requested.connect(self.restore_requested)
        self._detail.delete_requested.connect(self._on_delete)
        self._detail.transfer_requested.connect(self.transfer_to_layer_requested)

    def _rebuild_status_combo(self) -> None:
        current = self._status_combo.currentData()
        self._status_combo.blockSignals(True)
        self._status_combo.clear()
        for label, data in [
            ("All", ""),
            ("Completed", "completed"),
            ("Failed", "failed"),
            ("Cancelled", "cancelled"),
        ]:
            self._status_combo.addItem(str(self._ts(label)), data)
        idx = self._status_combo.findData(current)
        if idx >= 0:
            self._status_combo.setCurrentIndex(idx)
        self._status_combo.blockSignals(False)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self._rebuild_status_combo()
        self.update_service_list()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _connect_store(self) -> None:
        self._store.record_added.connect(self._on_record_added)
        self._store.record_updated.connect(self._on_record_updated)
        self._store.record_removed.connect(self._on_record_removed)
        self._store.records_loaded.connect(self.refresh)

    # ---- public API ----

    def refresh(self) -> None:
        """Re-populate the grid from the store."""
        status_filter = self._status_combo.currentData() or None
        service_filter = self._service_filter or (self._service_combo.currentData() or None)
        records = self._store.list_records(service_id=service_filter, status=status_filter)
        self._populate_grid(records)
        self._count_label.setText(f"{len(records)}")
        self._detail.clear()

    def set_layers(self, layers: list[tuple[str, str]]) -> None:
        """Update available layers for the transfer combo."""
        self._detail.set_layers(layers)

    def update_service_list(self) -> None:
        """Refresh the service combo from registered providers."""
        if self._service_filter:
            return
        current = self._service_combo.currentData()
        self._service_combo.blockSignals(True)
        self._service_combo.clear()
        self._service_combo.addItem(str(self._ts("All Services")), "")
        for sid in self._store.list_provider_ids():
            provider = self._store.get_provider(sid)
            name = provider.service_name if provider else sid
            self._service_combo.addItem(name, sid)
        # Restore selection
        idx = self._service_combo.findData(current)
        if idx >= 0:
            self._service_combo.setCurrentIndex(idx)
        self._service_combo.blockSignals(False)

    # ---- internals ----

    def _populate_grid(self, records: list[GenerationRecord]) -> None:
        self._grid.clear()
        base = self._store.base_path
        for rec in records:
            self._grid.addItem(self._make_grid_item(rec, base))

    def _load_thumbnail(self, record: GenerationRecord, base: Path) -> QIcon | None:
        if record.thumbnail_path:
            cache_key = record.thumbnail_path
            cached = self._thumb_cache.get(cache_key)
            if cached is not None:
                return cached
            full = base / record.thumbnail_path
            if full.exists():
                pm = QPixmap(str(full))
                if not pm.isNull():
                    icon = QIcon(pm)
                    self._thumb_cache[cache_key] = icon
                    return icon
        return None

    def _matches_current_filter(self, record: GenerationRecord) -> bool:
        """Check if a record passes the current UI filters."""
        status_filter = self._status_combo.currentData() or None
        service_filter = self._service_filter or (self._service_combo.currentData() or None)
        if status_filter and record.status != status_filter:
            return False
        return not (service_filter and record.service_id != service_filter)

    def _make_grid_item(self, record: GenerationRecord, base: Path) -> QListWidgetItem:
        """Create a QListWidgetItem for a single record."""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, record.job_id)
        icon = self._load_thumbnail(record, base)
        if icon:
            item.setIcon(icon)
        time_str = record.created_at[11:16] if len(record.created_at) > 16 else ""
        status_mark = {"completed": "OK", "failed": "ERR", "cancelled": "---"}.get(
            record.status, record.status
        )
        item.setText(f"{status_mark}\n{time_str}")
        item.setSizeHint(_GRID_ICON_SIZE)
        return item

    def _on_record_added(self, job_id: str) -> None:
        """Incrementally add a single record to the top of the grid."""
        record = self._store.get_record(job_id)
        if not record or not self._matches_current_filter(record):
            return
        item = self._make_grid_item(record, self._store.base_path)
        self._grid.insertItem(0, item)
        self._count_label.setText(f"{self._grid.count()}")

    def _on_record_updated(self, job_id: str) -> None:
        """Update an existing grid item when its record changes."""
        record = self._store.get_record(job_id)
        if not record:
            return
        for i in range(self._grid.count()):
            item = self._grid.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == job_id:
                # Refresh icon
                icon = self._load_thumbnail(record, self._store.base_path)
                if icon:
                    item.setIcon(icon)
                # Refresh status text
                time_str = record.created_at[11:16] if len(record.created_at) > 16 else ""
                status_mark = {"completed": "OK", "failed": "ERR", "cancelled": "---"}.get(
                    record.status, record.status
                )
                item.setText(f"{status_mark}\n{time_str}")
                break

    def _on_record_removed(self, job_id: str) -> None:
        """Incrementally remove a single record from the grid."""
        for i in range(self._grid.count()):
            item = self._grid.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == job_id:
                self._grid.takeItem(i)
                break
        self._count_label.setText(f"{self._grid.count()}")
        # Evict thumbnail cache for removed record
        record = self._store.get_record(job_id)
        if record and record.thumbnail_path:
            self._thumb_cache.pop(record.thumbnail_path, None)

    def _on_item_selected(self, job_id: str) -> None:
        record = self._store.get_record(job_id)
        if not record:
            self._detail.clear()
            return
        if self._host_api:
            self._refresh_layers()
        has_provider = self._store.get_provider(record.service_id) is not None
        self._detail.show_record(record, self._store.base_path, has_provider)

    def _on_delete(self, job_id: str) -> None:
        reply = QMessageBox.question(
            self,
            str(self._ts("Delete Record")),
            str(self._ts("Delete this generation record?")),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._store.remove_record(job_id)
            self.delete_requested.emit(job_id)

    def _refresh_layers(self) -> None:
        """Populate the layer combo from host_api (standalone mode)."""
        if not self._host_api or not hasattr(self._host_api, "canvas"):
            return
        layers: list[tuple[str, str]] = []
        try:
            for lr in self._host_api.canvas.get_layers() or []:
                layers.append((lr.id, lr.name or lr.id))
        except Exception:
            logger.debug("get_layers failed", exc_info=True)
        self.set_layers(layers)

    def _on_transfer_to_layer(self, job_id: str, layer_id: str) -> None:  # noqa: PLR0912, PLR0915
        """Handle transfer in standalone mode using host_api directly."""
        if not self._host_api:
            return
        record = self._store.get_record(job_id)
        if not record or not record.image_path:
            QMessageBox.warning(self, str(self._ts("Warning")), str(self._ts("No image available")))
            return

        image_path = self._store.resolve_path(record.image_path)
        if not image_path.exists():
            QMessageBox.warning(
                self, str(self._ts("Warning")), str(self._ts("Image file not found"))
            )
            return

        image_bytes = image_path.read_bytes()
        canvas = self._host_api.canvas

        if layer_id:
            # Overwrite existing layer - confirm first
            settings = getattr(self._host_api, "settings", None)
            skip_confirm = False
            if settings:
                skip_confirm = bool(
                    settings.get("generation_history.skip_layer_overwrite_confirm", False)
                )
            if not skip_confirm:
                from PySide6.QtWidgets import QCheckBox  # noqa: PLC0415

                msgbox = QMessageBox(self)
                msgbox.setWindowTitle(str(self._ts("Overwrite Layer")))
                msgbox.setText(str(self._ts("Overwrite the selected layer with this image?")))
                cb = QCheckBox(str(self._ts("Don't ask again")))
                msgbox.setCheckBox(cb)
                msgbox.setStandardButtons(
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                )
                if msgbox.exec() != QMessageBox.StandardButton.Ok:
                    return
                if cb.isChecked() and settings:
                    settings.set("generation_history.skip_layer_overwrite_confirm", True)
            try:
                import numpy as np  # noqa: PLC0415

                qimg = QImage()
                qimg.loadFromData(image_bytes)
                if qimg.isNull():
                    raise ValueError("Failed to decode image")
                qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
                ptr = qimg.bits()
                arr = (
                    np.frombuffer(ptr, dtype=np.uint8)
                    .reshape((qimg.height(), qimg.width(), 4))
                    .copy()
                )
                canvas.apply_image(arr, metadata={"target_layer_id": layer_id})
            except Exception as exc:
                logger.error("Failed to transfer image to layer: %s", exc)
                QMessageBox.warning(self, str(self._ts("Error")), str(exc))
        else:
            # New layer via document service. ``import_layer`` is synchronous
            # (returns ``str | None``) so we call it directly — wrapping it in
            # an asyncio task and ``await``ing the result raised TypeError and
            # blocked the qasync loop while the synchronous import ran, which
            # users perceived as a freeze.
            doc_service = getattr(self._host_api, "document", None)
            if doc_service and hasattr(doc_service, "import_layer"):
                import tempfile  # noqa: PLC0415

                tmp_path: str | None = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp.write(image_bytes)
                        tmp_path = tmp.name
                    layer_id = doc_service.import_layer(tmp_path, as_new=True)
                    if layer_id is None:
                        QMessageBox.warning(
                            self,
                            str(self._ts("Error")),
                            str(self._ts("Failed to import image as new layer")),
                        )
                except Exception as exc:
                    logger.error("Failed to import layer: %s", exc)
                    QMessageBox.warning(self, str(self._ts("Error")), str(exc))
                finally:
                    if tmp_path:
                        try:
                            Path(tmp_path).unlink(missing_ok=True)
                        except OSError:
                            logger.debug("Failed to clean up temp file %s", tmp_path, exc_info=True)
            else:
                QMessageBox.warning(
                    self,
                    str(self._ts("Warning")),
                    str(self._ts("Layer import not available")),
                )
