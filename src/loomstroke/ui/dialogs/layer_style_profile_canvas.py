"""Direct manipulation and painting of relief profile control points."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from loomstroke.core.appearance.relief_style_parameters import MAX_PROFILE_POINTS, evaluate_profile


class LayerStyleProfileCanvas(QWidget):
    def __init__(self, editor: Any) -> None:
        super().__init__(editor)
        self.editor = editor
        self.setObjectName("profile_canvas")
        self.setMinimumSize(180, 140)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._dragging = False

    def plot_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def screen_point(self, x: float, y: float) -> QPointF:
        rect = self.plot_rect()
        return QPointF(rect.left() + x * rect.width(), rect.bottom() - y * rect.height())

    def _nearest(self, position: QPointF) -> int | None:
        for index, point in enumerate(self.editor.profile["points"]):
            delta = self.screen_point(*point) - position
            if delta.x() ** 2 + delta.y() ** 2 <= 100:
                return index
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus()
        index = self._nearest(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None:
                self.editor.selected = index
                self.editor.remove_point()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if index is None:
            points = self.editor.profile["points"]
            if len(points) >= MAX_PROFILE_POINTS:
                return
            x, y = self._coordinates(event.position())
            if any(abs(x - point[0]) < 0.001 for point in points):
                return
            index = next(i for i, point in enumerate(points) if point[0] > x)
            points.insert(index, [x, y])
            self.editor.selected = index
            self.editor.changed()
        self.editor.selected = index
        self.editor.sync_controls()
        self._dragging = True
        self.update()

    def _coordinates(self, position: QPointF) -> tuple[float, float]:
        rect = self.plot_rect()
        return (
            max(0.0, min(1.0, (position.x() - rect.left()) / rect.width())),
            max(0.0, min(1.0, (rect.bottom() - position.y()) / rect.height())),
        )

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self.editor.move_point(*self._coordinates(event.position()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self.editor.move_point(*self._coordinates(event.position()))
        self._dragging = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.editor.remove_point()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot_rect()
        palette = self.palette()
        painter.fillRect(rect, palette.base())
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawRect(rect)
        for offset in (0.25, 0.5, 0.75):
            painter.drawLine(self.screen_point(offset, 0), self.screen_point(offset, 1))
            painter.drawLine(self.screen_point(0, offset), self.screen_point(1, offset))
        xs = np.linspace(0, 1, max(64, self.width()))
        ys = evaluate_profile(xs, self.editor.profile)
        path = QPainterPath(self.screen_point(float(xs[0]), float(ys[0])))
        for x, y in zip(xs[1:], ys[1:], strict=True):
            path.lineTo(self.screen_point(float(x), float(y)))
        painter.setPen(QPen(palette.highlight().color(), 2))
        painter.drawPath(path)
        for index, point in enumerate(self.editor.profile["points"]):
            painter.setBrush(
                palette.highlight() if index == self.editor.selected else palette.base()
            )
            painter.setPen(QPen(palette.text().color(), 1))
            painter.drawEllipse(self.screen_point(*point), 5, 5)
        painter.end()
