from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .pen_preset_panel import PenPresetPanel
from .tip_cache import TipCache

_PANEL_MINIMUM_SIZE_HINT = QSize(100, 50)


class _BrushTabs(QTabWidget):
    def minimumSizeHint(self) -> QSize:
        return _PANEL_MINIMUM_SIZE_HINT


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BrushManagerPanel", text, use_fallback=True)


class BrushManagerPanel(QWidget):
    swatchSelected = Signal(str)
    requestCreateTipFromSelection = Signal()
    requestSaveParamsToSelected = Signal()
    requestImportABR = Signal()
    requestRescanABR = Signal()
    requestImportImages = Signal()
    requestDeleteSelected = Signal()
    swatchRenamed = Signal(str, str)

    def __init__(self, tip_cache: TipCache, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tip_cache = tip_cache
        self._swatch_lookup: dict[str, dict[str, Any]] = {}
        self._block_item_updates = False
        self._button_layout_mode: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._tabs = _BrushTabs(self)
        layout.addWidget(self._tabs)
        brush_page = QWidget(self._tabs)
        brush_layout = QVBoxLayout(brush_page)
        brush_layout.setContentsMargins(0, 0, 0, 0)
        brush_layout.setSpacing(6)
        self._tabs.addTab(brush_page, self._ts("Brushes"))
        self.pen_panel = PenPresetPanel(self._tabs)
        self._tabs.addTab(self.pen_panel, self._ts("Pen"))

        self._list = QListWidget(self)
        self._list.setViewMode(QListWidget.ViewMode.IconMode)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._list.itemChanged.connect(self._on_item_changed)
        brush_layout.addWidget(self._list, 1)

        self._button_container = QWidget(self)
        self._button_layout = QGridLayout(self._button_container)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        self._button_layout.setSpacing(6)
        self._button_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._btn_create = QPushButton(self._ts("Create Tip"), self)
        self._btn_save = QPushButton(self._ts("Save Params"), self)
        self._btn_import = QPushButton(self._ts("Import ABR"), self)
        self._btn_rescan = QPushButton(self._ts("Rescan ABR"), self)
        self._btn_import_images = QPushButton(self._ts("Import PNG"), self)
        self._btn_delete = QPushButton(self._ts("Delete"), self)

        self._btn_create.clicked.connect(self.requestCreateTipFromSelection.emit)
        self._btn_save.clicked.connect(self.requestSaveParamsToSelected.emit)
        self._btn_import.clicked.connect(self.requestImportABR.emit)
        self._btn_rescan.clicked.connect(self.requestRescanABR.emit)
        self._btn_import_images.clicked.connect(self.requestImportImages.emit)
        self._btn_delete.clicked.connect(self.requestDeleteSelected.emit)

        self._buttons = [
            self._btn_create,
            self._btn_save,
            self._btn_rescan,
            self._btn_import,
            self._btn_import_images,
            self._btn_delete,
        ]
        self._update_button_layout(force=True)
        brush_layout.addWidget(self._button_container)

    def minimumSizeHint(self) -> QSize:
        return _PANEL_MINIMUM_SIZE_HINT

    def _ts(self, text: str) -> str:
        return _ts(text)

    def changeEvent(self, event) -> None:  # pragma: no cover - translation update path
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._tabs.setTabText(0, self._ts("Brushes"))
            self._tabs.setTabText(1, self._ts("Pen"))
            self._btn_create.setText(self._ts("Create Tip"))
            self._btn_save.setText(self._ts("Save Params"))
            self._btn_import.setText(self._ts("Import ABR"))
            self._btn_rescan.setText(self._ts("Rescan ABR"))
            self._btn_import_images.setText(self._ts("Import PNG"))
            self._btn_delete.setText(self._ts("Delete"))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_button_layout()

    def refresh_button_layout(self) -> None:
        self._update_button_layout(force=True)

    def set_swatches(self, swatches: list[dict[str, Any]]) -> None:
        self._block_item_updates = True
        try:
            self._swatch_lookup = {swatch.get("id"): swatch for swatch in swatches}
            self._list.clear()
            icon_size = self._icon_size()
            self._list.setIconSize(icon_size)
            grid = icon_size.width() + 16
            self._list.setGridSize(QSize(grid, grid + 24))
            for swatch in swatches:
                swatch_id = swatch.get("id")
                if not isinstance(swatch_id, str):
                    continue
                label = str(swatch.get("label", swatch_id))
                tip_path = swatch.get("tip_path")
                item = QListWidgetItem(label, self._list)
                item.setData(Qt.ItemDataRole.UserRole, swatch_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                if isinstance(tip_path, str):
                    abs_path = swatch.get("_abs_tip_path")
                    if isinstance(abs_path, str):
                        pix = self._tip_cache.get_pixmap(abs_path, icon_size.width())
                        item.setIcon(QIcon(pix))
            self._list.sortItems(Qt.SortOrder.AscendingOrder)
        finally:
            self._block_item_updates = False

    def select_swatch(self, swatch_id: str) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == swatch_id:
                self._list.setCurrentItem(item)
                return

    def get_selected_swatch_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        swatch_id = item.data(Qt.ItemDataRole.UserRole)
        return swatch_id if isinstance(swatch_id, str) else None

    def clear_swatch_selection(self) -> None:
        with QSignalBlocker(self._list):
            self._list.setCurrentRow(-1)

    def _icon_size(self) -> QSize:
        size = 64
        return QSize(size, size)

    def _on_selection_changed(self) -> None:
        swatch_id = self.get_selected_swatch_id()
        if swatch_id:
            self.swatchSelected.emit(swatch_id)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._block_item_updates:
            return
        swatch_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(swatch_id, str):
            return
        label = item.text().strip()
        if not label:
            label = swatch_id
            item.setText(label)
        self.swatchRenamed.emit(swatch_id, label)

    def _required_row_width(self) -> int:
        spacing = self._button_layout.horizontalSpacing()
        if spacing < 0:
            spacing = self._button_layout.spacing()
        margins = self._button_layout.contentsMargins()
        width = margins.left() + margins.right()
        for btn in self._buttons:
            width += btn.sizeHint().width()
        if self._buttons:
            width += spacing * (len(self._buttons) - 1)
        return width

    def _layout_mode(self) -> str:
        size = self.size()
        if size.height() > 0:
            return "wide" if size.width() > size.height() else "tall"
        available = self._button_container.width() if self._button_container else self.width()
        return "wide" if available >= self._required_row_width() else "tall"

    def _update_button_layout(self, *, force: bool = False) -> None:
        mode = self._layout_mode()
        columns = 6 if mode == "wide" else 3
        available = max(1, self.width() - 36)
        spacing = max(0, self._button_layout.spacing())
        while columns > 1:
            required = sum(
                max(button.sizeHint().width() for button in self._buttons[col::columns])
                for col in range(columns)
            ) + spacing * (columns - 1)
            if required <= available:
                break
            columns -= 1
        layout_mode = f"{mode}:{columns}"
        if not force and layout_mode == self._button_layout_mode:
            return
        self._button_layout_mode = layout_mode
        while self._button_layout.count():
            item = self._button_layout.takeAt(0)
            if widget := item.widget():
                self._button_layout.removeWidget(widget)
        wide_order = [
            self._btn_create,
            self._btn_save,
            self._btn_rescan,
            self._btn_import,
            self._btn_import_images,
            self._btn_delete,
        ]
        for index, btn in enumerate(wide_order):
            self._button_layout.addWidget(btn, index // columns, index % columns)
