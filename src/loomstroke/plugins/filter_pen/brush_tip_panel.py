"""Compact filter-pen tip selection and independent import actions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from loomstroke.platform.async_task_owner import AsyncTaskOwner
from loomstroke.plugins.basic_pen.abr_importer import ABRParseError
from loomstroke.ui._dock_safe import is_valid

from .tip_import import import_abr, import_png, import_selection
from .tip_library import TipLibrary
from .translations import ts

logger = logging.getLogger(__name__)


class BrushTipPanel(QWidget, AsyncTaskOwner):
    tip_selected = Signal(object)

    def __init__(
        self,
        library: TipLibrary,
        *,
        canvas_getter: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        AsyncTaskOwner.__init__(self)
        self.destroyed.connect(self.cancel_owned_tasks)
        self.library = library
        self._canvas_getter = canvas_getter
        self._error_source = ""
        self._error_detail = ""
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel(self)
        self.combo = QComboBox(self)
        self.combo.setIconSize(QSize(28, 28))
        self.combo.currentIndexChanged.connect(self._selected)
        self.png_button = QPushButton(self)
        self.abr_button = QPushButton(self)
        self.selection_button = QPushButton(self)
        self.png_button.clicked.connect(self._choose_png)
        self.abr_button.clicked.connect(self._choose_abr)
        self.selection_button.clicked.connect(self._create_selection)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.label, 0, 0, 1, 2)
        layout.addWidget(self.combo, 1, 0, 1, 2)
        layout.addWidget(self.png_button, 2, 0)
        layout.addWidget(self.abr_button, 2, 1)
        layout.addWidget(self.selection_button, 3, 0, 1, 2)
        layout.addWidget(self.status, 4, 0, 1, 2)
        self._retranslate()
        self.refresh()

    def refresh(self, selected_path: str | None = None) -> None:
        if selected_path is None:
            selected_path = self.combo.currentData()
        previous = self.combo.blockSignals(True)
        try:
            tips = self.library.list_tips()
            self.combo.clear()
            self.combo.addItem(ts("Round"), None)
            for tip in tips:
                path = self.library.path_for(tip)
                self.combo.addItem(QIcon(QPixmap(str(path))), tip.label, str(path))
            if selected_path and self.combo.findData(selected_path) < 0:
                self.combo.addItem(Path(selected_path).stem, selected_path)
            self.combo.setCurrentIndex(max(0, self.combo.findData(selected_path)))
        except (OSError, TypeError, ValueError) as exc:
            self._show_error(exc)
        finally:
            self.combo.blockSignals(previous)

    def set_tip(self, path: str | None) -> None:
        index = self.combo.findData(path)
        if index < 0:
            self.refresh(path)
            index = self.combo.findData(path)
        previous = self.combo.blockSignals(True)
        self.combo.setCurrentIndex(max(0, index))
        self.combo.blockSignals(previous)

    def _selected(self, _index: int) -> None:
        self.tip_selected.emit(self.combo.currentData())

    def _choose_png(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, ts("Import PNG"), str(self.library.root_dir), ts("PNG Images (*.png)")
        )
        if path:
            self._start_import(path, abr=False)

    def import_png_path(self, path: str) -> bool:
        try:
            tip = import_png(self.library, path)
            self._use_tip(str(self.library.path_for(tip)))
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._show_error(exc)
            return False

    def _choose_abr(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, ts("Import ABR"), str(self.library.root_dir), ts("Photoshop Brush (*.abr)")
        )
        if path:
            self._start_import(path, abr=True)

    def _start_import(self, path: str, *, abr: bool) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if abr:
                self.import_abr_path(path)
            else:
                self.import_png_path(path)
            return
        self.setEnabled(False)
        self.create_owned_task(self._import_async(path, abr=abr), require_running=True)

    async def _import_async(self, path: str, *, abr: bool) -> None:
        try:
            if abr:
                entries = await asyncio.to_thread(import_abr, self.library, path)
                tip = entries[0]
            else:
                tip = await asyncio.to_thread(import_png, self.library, path)
            self._use_tip(str(self.library.path_for(tip)))
        except (ABRParseError, OSError, TypeError, ValueError) as exc:
            self._show_error(exc)
        finally:
            if is_valid(self):
                self.setEnabled(True)

    def import_abr_path(self, path: str) -> bool:
        try:
            tips = import_abr(self.library, path)
            self._use_tip(str(self.library.path_for(tips[0])))
            return True
        except (ABRParseError, OSError, TypeError, ValueError) as exc:
            self._show_error(exc)
            return False

    def _create_selection(self) -> None:
        try:
            canvas = self._canvas_getter() if self._canvas_getter is not None else None
            tip = import_selection(self.library, canvas, str(ts("Selection Tip")))
            self._use_tip(str(self.library.path_for(tip)))
        except (OSError, TypeError, ValueError) as exc:
            self._show_error(exc)

    def _use_tip(self, path: str) -> None:
        self.status.hide()
        self._error_source = ""
        self._error_detail = ""
        self.refresh(path)
        self.tip_selected.emit(path)

    def _show_error(self, error: Exception) -> None:
        logger.warning("Effect pen brush tip import failed: %s", error)
        detail = str(error)
        self._error_source = (
            detail
            if detail
            in {
                "No active selection",
                "Selection snapshot is not available",
                "No tips were found in this ABR file",
            }
            else "Tip import failed: {error}"
        )
        self._error_detail = detail
        self.status.setText(ts(self._error_source).format(error=ts(detail)))
        self.status.show()

    def _retranslate(self) -> None:
        self.label.setText(ts("Brush Tip"))
        self.png_button.setText(ts("Import PNG"))
        self.abr_button.setText(ts("Import ABR"))
        self.selection_button.setText(ts("Create Tip from Selection"))
        if self.combo.count():
            self.combo.setItemText(0, ts("Round"))
        if self._error_source:
            self.status.setText(ts(self._error_source).format(error=ts(self._error_detail)))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._retranslate()
