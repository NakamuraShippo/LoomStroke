"""Transient figure hover feedback and reversible per-surface cursors."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid as is_valid

from loomstroke.core.drawing_figures.model import pose_from_layer

from .figure_rotation_gizmo import hit_rotation_ring
from .guide_canvas_bridge import update_guide_overlay


class FigureHoverFeedback(QObject):
    """Own hover appearance without changing document or interaction state."""

    def __init__(self, figure: Any) -> None:
        super().__init__(figure)
        self.figure = figure
        self.joint: str | None = None
        self.axis: str | None = None
        self._key: tuple[Any, ...] | None = None
        self._saved: dict[QWidget, tuple[QCursor, bool]] = {}
        self._watched: list[QWidget] = []
        self._surfaces: list[QWidget] = []
        self._cursors: dict[float, QCursor] = {}
        self._changing_cursor = False

    def _signature(self, layer: Any) -> tuple[Any, ...]:
        viewport = self.figure.owner._widget.viewport
        transform = tuple(
            float(v) for p in ((0, 0), (1, 0), (0, 1)) for v in viewport.canvas_to_widget(*p)
        )
        return (
            id(layer),
            self.figure.selected_joint,
            id(getattr(layer, "scene_3d", None)),
            repr(getattr(layer, "camera_3d", None)),
            getattr(layer, "offset_x", 0),
            getattr(layer, "offset_y", 0),
            transform,
        )

    def valid(self) -> bool:
        if self._key is None:
            return True
        layer = self.figure.layer()
        return (
            (
                self.figure.owner.drag_mode() == "pose"
                or (self.figure.hand_editor_active and self.figure.owner.drag_mode() == "orbit")
            )
            and layer is not None
            and pose_from_layer(layer) is not None
            and self._key == self._signature(layer)
        )

    def move(self, event: Any) -> bool:
        owner = self.figure.owner
        layer = (
            self.figure.layer()
            if owner.drag_mode() == "pose"
            or (self.figure.hand_editor_active and owner.drag_mode() == "orbit")
            else None
        )
        point = event.position()
        xy = (float(point.x()), float(point.y()))
        buttons = getattr(event, "buttons", lambda: Qt.MouseButton.NoButton)()
        if (
            layer is None
            or buttons != Qt.MouseButton.NoButton
            or pose_from_layer(layer) is None
            or not owner._is_inside_canvas(owner._widget.viewport.widget_to_canvas(*xy))
        ):
            self.clear()
            return False
        joint = self.figure.hit_joint(layer, xy)
        ring = (
            None
            if joint is not None
            else hit_rotation_ring(self.figure.rings(layer), np.asarray(xy))
        )
        if joint is None and ring is None:
            self.clear()
            return False
        self.set_hit(layer, joint, ring.axis if ring is not None else None)
        event.accept()
        return True

    def set_hit(self, layer: Any, joint: str | None, axis: str | None) -> None:
        key = self._signature(layer)
        changed = (self.joint, self.axis, self._key) != (joint, axis, key)
        self.joint, self.axis, self._key = joint, axis, key
        if not self._watched:
            widget = self.figure.owner._widget
            host = getattr(widget, "_guide_viewport_host", widget)
            targets = (
                host,
                getattr(host, "_native_surface", None),
                getattr(host, "_overlay_surface", None),
                widget,
            )
            for target in targets:
                if isinstance(target, QWidget) and is_valid(target) and target not in self._watched:
                    self._watched.append(target)
                    self._surfaces.append(target)
                    target.installEventFilter(self)
            if isinstance(host, QWidget) and is_valid(host) and host.window() not in self._watched:
                self._watched.append(host.window())
                host.window().installEventFilter(self)
        if joint is not None:
            for target in self._surfaces:
                self._set_circle(target)
        else:
            self._restore_cursors()
        if changed:
            update_guide_overlay(self.figure.owner._widget)

    def _is_ours(self, cursor: QCursor) -> bool:
        key = cursor.pixmap().cacheKey()
        return bool(key) and any(
            key == value.pixmap().cacheKey() for value in self._cursors.values()
        )

    def _set_circle(self, target: QWidget) -> None:
        if not is_valid(target):
            return
        if self._is_ours(target.cursor()):
            return
        self._saved[target] = (
            QCursor(target.cursor()),
            target.testAttribute(Qt.WidgetAttribute.WA_SetCursor),
        )
        ratio = float(target.devicePixelRatioF())
        if ratio not in self._cursors:
            pixmap = QPixmap(round(32 * ratio), round(32 * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for color, width in ((QColor(15, 20, 26), 3.5), (QColor("white"), 1.5)):
                painter.setPen(QPen(color, width))
                painter.drawEllipse(QPointF(16, 16), 7, 7)
            painter.end()
            self._cursors[ratio] = QCursor(pixmap, 16, 16)
        self._changing_cursor = True
        try:
            # Bypass host propagation; each child owns an independent snapshot.
            QWidget.setCursor(target, self._cursors[ratio])
        finally:
            self._changing_cursor = False

    def _restore_cursors(self) -> None:
        self._changing_cursor = True
        try:
            for target, (cursor, explicit) in self._saved.items():
                if is_valid(target) and self._is_ours(target.cursor()):
                    if explicit:
                        QWidget.setCursor(target, cursor)
                    else:
                        QWidget.unsetCursor(target)
        finally:
            self._changing_cursor = False
            self._saved.clear()

    def clear(self) -> None:
        changed = self.joint is not None or self.axis is not None
        self.joint, self.axis, self._key = None, None, None
        for target in self._watched:
            if is_valid(target):
                target.removeEventFilter(self)
        self._watched.clear()
        self._surfaces.clear()
        self._restore_cursors()
        if changed:
            widget = self.figure.owner._widget
            if not isinstance(widget, QWidget) or is_valid(widget):
                update_guide_overlay(widget)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in {
            QEvent.Type.Leave,
            QEvent.Type.Hide,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.MouseButtonPress,
            getattr(QEvent.Type, "DevicePixelRatioChange", None),
        }:
            self.clear()
        elif (
            event.type() == QEvent.Type.CursorChange
            and not self._changing_cursor
            and self.joint is not None
            and isinstance(watched, QWidget)
            and watched in self._surfaces
        ):
            if self._is_ours(watched.cursor()) and not watched.testAttribute(
                Qt.WidgetAttribute.WA_SetCursor
            ):
                self._saved[watched] = (QCursor(watched.cursor()), False)
            else:
                self._set_circle(watched)
        return super().eventFilter(watched, event)
