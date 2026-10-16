"""A draggable width-profile preview for pen entry and exit lengths."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from loomstroke.core.pen_taper_model import taper_scale
from loomstroke.ui.translations import translated_literal_domain

_START = "taper_start_length"
_END = "taper_end_length"
_ENTRY_COLOR = QColor("#42a5f5")
_EXIT_COLOR = QColor("#ec7a9b")
_HINT = (
    "Drag the handles to adjust taper lengths. Left/Right adjust; Shift changes by 10 px; "
    "Up/Down choose a handle; Home turns off; End sets 2000 px."
)


def _ts(source: str) -> str:
    return translated_literal_domain("PenPresets", source, use_fallback=True)


def _bounded(value: float) -> float:
    number = float(value)
    return max(0.0, min(2000.0, number)) if math.isfinite(number) else 0.0


class PenTaperEditor(QWidget):
    """Present canvas-pixel lengths while keeping drag coordinates stable."""

    lengthsEdited = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start = 0.0
        self._end = 0.0
        self._span = 200.0
        self._active = _START
        self._dragging: str | None = None
        self._drag_offset = 0.0
        self._last_drag_x: float | None = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(140)
        self.retranslate()

    @property
    def start_length(self) -> float:
        return self._start

    @property
    def end_length(self) -> float:
        return self._end

    @property
    def preview_length(self) -> float:
        return self._span

    def set_lengths(self, start: float, end: float) -> None:
        """Synchronize even during a drag, without changing its anchor or emitting."""
        self._start, self._end = _bounded(start), _bounded(end)
        if self._dragging is None:
            self._refit()
        self.retranslate()

    def _refit(self) -> None:
        self._span = max(200.0, 2.5 * max(self._start, self._end))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(220, 140)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(100, 140)

    def _plot_rect(self) -> QRectF:
        return QRectF(12.0, 58.0, max(1.0, self.width() - 24.0), 30.0)

    def handle_position(self, key: str) -> QPointF:
        """Expose the handle centers for host interaction and UI verification."""
        plot = self._plot_rect()
        if key == _START:
            distance, y = self._start, 47.0
        elif key == _END:
            distance, y = self._span - self._end, 100.0
        else:
            raise ValueError("Unknown taper handle")
        fraction = max(0.0, min(1.0, distance / self._span))
        return QPointF(plot.left() + fraction * plot.width(), y)

    def _hit_handle(self, point: QPointF) -> str | None:
        for key in (_START, _END):
            center = self.handle_position(key)
            if abs(point.x() - center.x()) <= 12.0 and abs(point.y() - center.y()) <= 12.0:
                return key
        return None

    def _edit_length(self, key: str, value: float) -> None:
        value = round(_bounded(value), 2)
        current = self._start if key == _START else self._end
        if value == current:
            return
        if key == _START:
            self._start = value
        else:
            self._end = value
        if self._dragging is None:
            self._refit()
        self.retranslate()
        self.lengthsEdited.emit({key: value})

    def _drag_to(self, point: QPointF) -> None:
        if self._dragging is None:
            return
        if point.x() == self._last_drag_x:
            return
        self._last_drag_x = point.x()
        plot = self._plot_rect()
        distance = (point.x() - self._drag_offset - plot.left()) / plot.width() * self._span
        value = distance if self._dragging == _START else self._span - distance
        self._edit_length(self._dragging, value)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        key = self._hit_handle(event.position())
        if event.button() != Qt.MouseButton.LeftButton or key is None:
            super().mousePressEvent(event)
            return
        self._active = self._dragging = key
        self._drag_offset = event.position().x() - self.handle_position(key).x()
        self._last_drag_x = event.position().x()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.retranslate()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._dragging is not None:
            self._drag_to(event.position())
            event.accept()
        else:
            cursor = Qt.CursorShape.SizeHorCursor if self._hit_handle(event.position()) else None
            self.setCursor(cursor or Qt.CursorShape.ArrowCursor)
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._dragging is None:
            super().mouseReleaseEvent(event)
            return
        self._drag_to(event.position())
        self._finish_drag()
        event.accept()

    def _finish_drag(self) -> None:
        self._dragging = None
        self._last_drag_x = None
        self._refit()
        self.unsetCursor()
        self.retranslate()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        key = event.key()
        value = self._start if self._active == _START else self._end
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._active = _START if key == Qt.Key.Key_Up else _END
            self.retranslate()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._edit_length(self._active, value + (step if key == Qt.Key.Key_Right else -step))
        elif key == Qt.Key.Key_Home:
            self._edit_length(self._active, 0.0)
        elif key == Qt.Key.Key_End:
            self._edit_length(self._active, 2000.0)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        if self._dragging is not None:
            self._finish_drag()
        super().focusOutEvent(event)
        self.update()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.update()

    def retranslate(self) -> None:
        """Refresh accessible text and paint labels without modifying input state."""
        self.setAccessibleName(_ts("Pen taper editor"))
        self.setToolTip(_ts(_HINT))
        name = _ts("Taper In" if self._active == _START else "Taper Out")
        value = self._start if self._active == _START else self._end
        self.setAccessibleDescription(
            _ts("{handle}: {length} px").format(handle=name, length=f"{value:g}")
        )
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()

    def _profile_path(self) -> QPainterPath:
        plot = self._plot_rect()
        center = plot.center().y()
        samples = [
            (
                plot.left() + index / 128.0 * plot.width(),
                11.0 * taper_scale(index / 128.0 * self._span, self._span, self._start, self._end),
            )
            for index in range(129)
        ]
        path = QPainterPath(QPointF(samples[0][0], center - samples[0][1]))
        for x, half_width in samples[1:]:
            path.lineTo(x, center - half_width)
        for x, half_width in reversed(samples):
            path.lineTo(x, center + half_width)
        path.closeSubpath()
        return path

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        painter.setPen(QPen(palette.mid().color(), 1.0))
        painter.drawLine(plot.topLeft(), plot.topRight())
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        painter.fillPath(self._profile_path(), palette.text())
        self._draw_text(
            painter,
            QRectF(6, 4, self.width() - 12, 20),
            _ts("Preview length: {length} px").format(length=f"{self._span:g}"),
        )
        for key, color, source, y in (
            (_START, _ENTRY_COLOR, "In: {length} px", 24.0),
            (_END, _EXIT_COLOR, "Out: {length} px", 114.0),
        ):
            position = self.handle_position(key)
            value = self._start if key == _START else self._end
            painter.setPen(QPen(color, 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(
                QPointF(position.x(), plot.top()), QPointF(position.x(), plot.bottom())
            )
            painter.setPen(QPen(color, 2.0 if self.hasFocus() and self._active == key else 1.0))
            painter.setBrush(color if self._active == key else palette.base())
            painter.drawEllipse(position, 6.0, 6.0)
            painter.drawLine(
                position, QPointF(position.x(), plot.top() if key == _START else plot.bottom())
            )
            self._draw_text(
                painter,
                QRectF(6, y, self.width() - 12, 20),
                _ts(source).format(length=f"{value:g}"),
            )
        if self.hasFocus():
            painter.setPen(QPen(palette.highlight().color(), 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -2, -2), 3, 3)

    @staticmethod
    def _draw_text(painter: QPainter, bounds: QRectF, text: str) -> None:
        elided = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, int(bounds.width())
        )
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, elided)
