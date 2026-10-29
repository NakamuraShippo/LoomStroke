"""Transient dialog previews: no settings writes, guide mutation, or history."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QPointF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox
from shiboken6 import isValid

from ...core.guide_transform import guide_line
from .guide_canvas_bridge import guide_canvas, update_guide_overlay
from .guide_preview_interaction import GuidePreviewDrag


class GuideDialogPreview:
    def __init__(self, dialog: Any, canvas: Any, kind: str, read: Callable) -> None:
        self.canvas = canvas = guide_canvas(canvas)
        self.attribute = f"_{kind}_dialog_preview"
        self.previous = getattr(canvas, self.attribute, None)
        self.read = read
        self.values: Any = None
        self.closed = False
        self.dialog = dialog
        self.drag = GuidePreviewDrag(self, dialog)
        self.page = self._page()
        setattr(canvas, self.attribute, self)
        for widget_type, signal in (
            (QCheckBox, "toggled"),
            (QComboBox, "currentIndexChanged"),
            (QSpinBox, "valueChanged"),
            (QDoubleSpinBox, "valueChanged"),
        ):
            for widget in dialog.findChildren(widget_type):
                getattr(widget, signal).connect(self.refresh)
        dialog.finished.connect(self.close)
        dialog.destroyed.connect(self.close)
        self.refresh()

    def _alive(self) -> bool:
        return not isinstance(self.canvas, QObject) or isValid(self.canvas)

    def _page(self) -> Any:
        host = getattr(self.canvas, "_host_api", None)
        pages = getattr(host, "pages", None)
        return pages.get_active_page_id() if pages is not None else None

    def page_matches(self) -> bool:
        return self._alive() and self.page == self._page()

    def current_values(self) -> Any:
        if self.closed or not self.page_matches():
            return None
        # A modeless editor's selection frame can change without a field signal.
        try:
            return self.read()
        except ValueError:
            return None

    def refresh(self, *_args: Any) -> None:
        if self.closed or not self._alive():
            return
        try:
            self.values = self.read()
        except ValueError:
            self.values = None
        update_guide_overlay(self.canvas)

    def close(self, *_args: Any) -> None:
        if self.closed:
            return
        self.closed = True
        self.values = None
        if not self._alive():
            return
        if getattr(self.canvas, self.attribute, None) is self:
            previous = self.previous
            while previous is not None and previous.closed:
                previous = previous.previous
            setattr(self.canvas, self.attribute, previous)
        update_guide_overlay(self.canvas)


def attach_guide_preview(dialog: Any, parent: Any, kind: str, read: Callable) -> Any:
    canvas = getattr(parent, "_canvas", None) or getattr(parent, "canvas", None)
    if canvas is None:
        return None
    return GuideDialogPreview(dialog, canvas, kind, read)


def close_guide_preview(dialog: Any) -> None:
    preview = getattr(dialog, "_guide_preview", None)
    if preview is not None:
        preview.close()


def preview_page_matches(dialog: Any) -> bool:
    preview = getattr(dialog, "_guide_preview", None)
    return preview is None or preview.page_matches()


def draw_layout_guide_preview(painter: QPainter, widget: Any) -> bool:
    preview = getattr(widget, "_layout_dialog_preview", None)
    values = preview.current_values() if preview is not None else None
    if values is None:
        return False
    positions, replace = values
    controller = getattr(widget, "_guide_controller", None)
    if controller is not None and not controller.guides_visible:
        return True
    if not replace and controller is not None:
        controller.draw(painter)
    host = getattr(widget, "_host_api", None)
    settings = getattr(host, "settings", None)
    color = QColor(settings.get("view/guides_color", "#00B7FF") if settings else "#00B7FF")
    pen = QPen(color, 1)
    pen.setCosmetic(True)
    painter.save()
    try:
        painter.setPen(pen)
        options = preview.dialog.placement_options()
        for axis, position in positions:
            line = guide_line(
                axis,
                position,
                (widget.state.width, widget.state.height),
                options.get("rotation", 0),
                options.get("pivot"),
            )
            if line is not None:
                painter.drawLine(QPointF(*line[0]), QPointF(*line[1]))
    finally:
        painter.restore()
    return True
