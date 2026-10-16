"""Graphical editing of the existing gamma/minimum pressure response."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from loomstroke.ui.translations import translated_literal_domain

from .pressure_response_settings import PressureResponseSettings

_HINT = (
    "Drag the middle handle to change the curve, or the left handle to change the minimum. "
    "Left/Right choose a handle; Up/Down adjust; Shift increases the step; Home resets; "
    "Esc cancels the drag."
)


def _ts(source: str) -> str:
    return translated_literal_domain("BasicPen", source, use_fallback=True)


class PenPressureEditor(QWidget):
    """Keep display geometry fixed while the host synchronizes accepted edits."""

    responseEdited = Signal(dict)

    def __init__(self, source_title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source_title = source_title
        self._response = PressureResponseSettings()
        self._active = "gamma"
        self._dragging: str | None = None
        self._drag_offset = 0.0
        self._drag_origin: tuple[float, float] | None = None
        self._last_drag_y: float | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(198)
        self.retranslate()

    @property
    def gamma(self) -> float:
        return self._response.size_pressure_gamma

    @property
    def minimum(self) -> float:
        return self._response.size_pressure_minimum

    def set_response(self, gamma: float, minimum: float) -> None:
        """Apply validated host values silently, preserving an ongoing drag."""
        self._response.update({"size_pressure_gamma": gamma, "size_pressure_minimum": minimum})
        self.retranslate()

    def response_at(self, pressure: float) -> float:
        return self._response.response(pressure, enabled=self.isEnabled())[0]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(240, 198)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(100, 198)

    def _plot_rect(self) -> QRectF:
        return QRectF(24, 30, max(1, self.width() - 40), 112)

    def handle_position(self, key: str) -> QPointF:
        if key not in ("gamma", "minimum"):
            raise ValueError("Unknown pressure handle")
        plot = self._plot_rect()
        pressure = 0.5 if key == "gamma" else 0.0
        return QPointF(
            plot.left() + pressure * plot.width(),
            plot.bottom() - self.response_at(pressure) * plot.height(),
        )

    def _hit_handle(self, point: QPointF) -> str | None:
        for key in ("gamma", "minimum"):
            if (point - self.handle_position(key)).manhattanLength() <= 16:
                return key
        return None

    def _edit(self, values: dict[str, float], *, snapshot: bool = False) -> None:
        before = {"gamma": self.gamma, "minimum": self.minimum}
        gamma = round(values.get("gamma", self.gamma), 2)
        minimum = round(values.get("minimum", self.minimum), 4)
        self.set_response(gamma, minimum)
        changed = {key: getattr(self, key) for key in values if before[key] != getattr(self, key)}
        if changed:
            self.responseEdited.emit(values if snapshot else changed)

    def _drag_to(self, position: QPointF) -> None:
        if self._dragging is None or position.y() == self._last_drag_y:
            return
        self._last_drag_y = position.y()
        plot = self._plot_rect()
        value = min(
            1.0, max(0.0, (plot.bottom() - position.y() + self._drag_offset) / plot.height())
        )
        if self._dragging == "minimum":
            self._edit({"minimum": value})
        elif self.minimum < 1:
            normalized = (value - self.minimum) / (1 - self.minimum)
            # The midpoint is p=0.5. Its reachable output spans gamma=4..0.1.
            normalized = min(0.5**0.1, max(0.5**4, normalized))
            self._edit({"gamma": math.log(normalized) / math.log(0.5)})

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        key = self._hit_handle(event.position())
        if not self.isEnabled() or event.button() != Qt.MouseButton.LeftButton or key is None:
            super().mousePressEvent(event)
            return
        self._active = self._dragging = key
        self._drag_origin = (self.gamma, self.minimum)
        self._drag_offset = event.position().y() - self.handle_position(key).y()
        self._last_drag_y = event.position().y()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging is not None:
            self._drag_to(event.position())
            event.accept()
        else:
            self.setCursor(
                Qt.CursorShape.SizeVerCursor
                if self._hit_handle(event.position())
                else Qt.CursorShape.ArrowCursor
            )
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._dragging is None:
            super().mouseReleaseEvent(event)
            return
        self._drag_to(event.position())
        self._finish_drag()
        event.accept()

    def _finish_drag(self) -> None:
        self._dragging = None
        self._drag_origin = None
        self._last_drag_y = None
        self.unsetCursor()
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.isEnabled():
            return
        key = event.key()
        if key == Qt.Key.Key_Escape and self._drag_origin is not None:
            gamma, minimum = self._drag_origin
            self._finish_drag()
            self._edit({"gamma": gamma, "minimum": minimum}, snapshot=True)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._active = "minimum" if key == Qt.Key.Key_Left else "gamma"
            self.retranslate()
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
            direction = 1 if key == Qt.Key.Key_Up else -1
            # A smaller gamma raises the curve, matching the vertical drag.
            delta = direction * step * (-1 if self._active == "gamma" else 1)
            self._edit({self._active: getattr(self, self._active) + delta})
        elif key == Qt.Key.Key_Home:
            self._edit({"gamma": 1.0, "minimum": 0.0})
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._finish_drag()
        super().focusOutEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        elif event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._finish_drag()

    def retranslate(self) -> None:
        self.setAccessibleName(_ts(self._source_title))
        self.setToolTip(_ts(_HINT))
        self.setAccessibleDescription(self._readout())
        self.update()

    def _readout(self) -> str:
        return _ts("Curve: {gamma}; minimum: {minimum}%").format(
            gamma=f"{self.gamma:g}", minimum=f"{self.minimum * 100:g}"
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        plot = self._plot_rect()
        painter.fillRect(self.rect(), palette.base())
        painter.setPen(QPen(palette.mid().color(), 1))
        for index in range(5):
            x = plot.left() + index / 4 * plot.width()
            y = plot.bottom() - index / 4 * plot.height()
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        path = QPainterPath()
        for index in range(65):
            point = QPointF(
                plot.left() + index / 64 * plot.width(),
                plot.bottom() - self.response_at(index / 64) * plot.height(),
            )
            path.moveTo(point) if index == 0 else path.lineTo(point)
        color = palette.highlight().color() if self.isEnabled() else palette.mid().color()
        painter.setPen(QPen(color, 2))
        painter.drawPath(path)
        if self.isEnabled():
            for key in ("gamma", "minimum"):
                painter.setBrush(color if key == self._active else palette.base())
                painter.drawEllipse(self.handle_position(key), 6, 6)
        painter.setPen(palette.text().color())
        self._draw_text(painter, QRectF(4, 2, self.width() - 8, 24), _ts(self._source_title))
        self._draw_text(painter, QRectF(4, 146, self.width() - 8, 24), _ts("Pressure (%)"))
        self._draw_text(painter, QRectF(4, 170, self.width() - 8, 24), self._readout())
        painter.drawText(QRectF(2, 28, 20, 20), "100")
        painter.drawText(QRectF(8, 126, 12, 20), "0")
        if self.hasFocus():
            painter.setPen(QPen(color, 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    @staticmethod
    def _draw_text(painter: QPainter, bounds: QRectF, text: str) -> None:
        text = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, int(bounds.width())
        )
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, text)
