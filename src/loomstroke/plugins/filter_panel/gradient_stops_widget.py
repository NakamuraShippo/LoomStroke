"""Interactive gradient-stops editor widget for the FilterPanel.

Provides an interactive gradient bar with draggable colour-stop handles,
similar to Photoshop / Krita gradient editors.

- Click on the bar to add a stop (colour sampled from the gradient).
- Click a handle to select it; double-click to change its colour.
- Drag handles to reposition.
- Boundary stops (position 0.0 / 1.0) cannot be deleted or moved.
- A "Delete" button removes the selected intermediate stop.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Geometry constants
_BAR_HEIGHT = 24
_HANDLE_HEIGHT = 10
_HANDLE_HALF_W = 6
_MARGIN = 6  # horizontal margin so edge handles are fully visible
_PAINTED_HEIGHT = _BAR_HEIGHT + _HANDLE_HEIGHT + 2


class _Stop:
    """A single gradient colour stop."""

    __slots__ = ("color", "position")

    def __init__(self, position: float, color: QColor) -> None:
        self.position = position
        self.color = QColor(color)


class _GradientBar(QWidget):
    """Custom-painted gradient bar with interactive handles."""

    # Signals
    stops_changed = Signal()
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stops: list[_Stop] = [
            _Stop(0.0, QColor(0, 0, 0, 255)),
            _Stop(1.0, QColor(255, 255, 255, 255)),
        ]
        self._selected: _Stop | None = None
        self._dragging = False
        self._drag_offset = 0.0
        self._drag_dirty = False  # True when drag moved but signal not yet emitted

        # Throttle stops_changed during drag (~30 fps)
        self._throttle = QTimer(self)
        self._throttle.setInterval(33)
        self._throttle.setSingleShot(True)
        self._throttle.timeout.connect(self._flush_drag)

        self.setFixedHeight(_PAINTED_HEIGHT)
        self.setMouseTracking(True)
        self.setMinimumWidth(80)

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _bar_left(self) -> int:
        return _MARGIN

    def _bar_right(self) -> int:
        return self.width() - _MARGIN

    def _bar_width(self) -> int:
        return max(1, self._bar_right() - self._bar_left())

    def _pos_to_x(self, pos: float) -> float:
        return self._bar_left() + pos * self._bar_width()

    def _x_to_pos(self, x: float) -> float:
        return max(0.0, min(1.0, (x - self._bar_left()) / self._bar_width()))

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def stops(self) -> list[_Stop]:
        return self._stops

    @property
    def selected(self) -> _Stop | None:
        return self._selected

    def selected_index(self) -> int | None:
        if self._selected is None:
            return None
        try:
            return self._stops.index(self._selected)
        except ValueError:
            return None

    def can_delete_selected(self) -> bool:
        idx = self.selected_index()
        if idx is None:
            return False
        if len(self._stops) <= 2:
            return False
        return 0 < idx < len(self._stops) - 1

    # ------------------------------------------------------------------
    # Public mutations
    # ------------------------------------------------------------------

    def set_stops_data(self, stops: list[_Stop]) -> None:
        self._stops = stops
        self._selected = None
        self.update()

    def delete_selected(self) -> bool:
        if not self.can_delete_selected():
            return False
        self._stops.remove(self._selected)  # type: ignore[arg-type]  # guarded by can_delete_selected
        self._selected = None
        self.selection_changed.emit()
        self.stops_changed.emit()
        self.update()
        return True

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bl = self._bar_left()
        bw = self._bar_width()

        # --- Gradient bar ---
        grad = QLinearGradient(bl, 0, bl + bw, 0)
        for stop in self._stops:
            grad.setColorAt(stop.position, stop.color)
        bar_rect = (bl, 0, bw, _BAR_HEIGHT)
        painter.fillRect(*bar_rect, grad)
        painter.setPen(QPen(QColor("#555"), 1))
        painter.drawRect(bl, 0, bw - 1, _BAR_HEIGHT - 1)

        # --- Handles ---
        for stop in self._stops:
            self._draw_handle(painter, stop, stop is self._selected)

        painter.end()

    def _draw_handle(self, painter: QPainter, stop: _Stop, selected: bool) -> None:
        x = self._pos_to_x(stop.position)
        top = _BAR_HEIGHT + 1
        path = QPainterPath()
        path.moveTo(QPointF(x, top))
        path.lineTo(QPointF(x - _HANDLE_HALF_W, top + _HANDLE_HEIGHT))
        path.lineTo(QPointF(x + _HANDLE_HALF_W, top + _HANDLE_HEIGHT))
        path.closeSubpath()

        painter.setBrush(stop.color)
        if selected:
            painter.setPen(QPen(QColor("#FFF"), 2))
        else:
            painter.setPen(QPen(QColor("#333"), 1))
        painter.drawPath(path)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return

        ex = event.position().x()
        ey = event.position().y()

        # Hit-test handles (reverse order for z-priority)
        hit = self._hit_test(ex, ey)
        if hit is not None:
            self._selected = hit
            self._dragging = True
            self._drag_offset = ex - self._pos_to_x(hit.position)
            self.selection_changed.emit()
            self.update()
            event.accept()
            return

        # Click on bar area (upper half, where hit_test didn't match) → add new stop
        if 0 <= ey <= _BAR_HEIGHT:
            pos = self._x_to_pos(ex)
            color = self._sample_color_at(pos)
            new_stop = _Stop(pos, color)
            self._stops.append(new_stop)
            self._stops.sort(key=lambda s: s.position)
            self._selected = new_stop
            self._dragging = True
            self._drag_offset = 0.0
            self.selection_changed.emit()
            self.stops_changed.emit()
            self.update()
            event.accept()
            return

        # Click elsewhere — deselect
        self._selected = None
        self.selection_changed.emit()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        ex = event.position().x()
        ey = event.position().y()

        if self._dragging and self._selected is not None:
            new_pos = self._x_to_pos(ex - self._drag_offset)
            new_pos = max(0.0, min(1.0, new_pos))
            self._selected.position = new_pos
            # Re-sort and track by identity
            ref = self._selected
            self._stops.sort(key=lambda s: s.position)
            self._selected = ref
            # Visual update is immediate; signal is throttled
            self.update()
            self._drag_dirty = True
            if not self._throttle.isActive():
                self._throttle.start()
            event.accept()
            return

        # Cursor feedback
        hit = self._hit_test(ex, ey)
        if hit is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif 0 <= ey <= _BAR_HEIGHT:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._flush_drag()  # emit final value
            event.accept()

    def _flush_drag(self) -> None:
        """Emit throttled stops_changed signal."""
        self._throttle.stop()
        if self._drag_dirty:
            self._drag_dirty = False
            self.stops_changed.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._hit_test(event.position().x(), event.position().y())
        if hit is not None:
            self._pick_handle_color(hit)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _hit_test(self, x: float, y: float) -> _Stop | None:
        # Accept clicks from the lower half of the bar through the handle area
        if y < _BAR_HEIGHT // 2:
            return None
        for stop in reversed(self._stops):
            hx = self._pos_to_x(stop.position)
            if abs(x - hx) <= _HANDLE_HALF_W + 3:
                return stop
        return None

    def _sample_color_at(self, pos: float) -> QColor:
        if not self._stops:
            return QColor(128, 128, 128, 255)
        if pos <= self._stops[0].position:
            return QColor(self._stops[0].color)
        if pos >= self._stops[-1].position:
            return QColor(self._stops[-1].color)
        for i in range(len(self._stops) - 1):
            s0 = self._stops[i]
            s1 = self._stops[i + 1]
            if s0.position <= pos <= s1.position:
                span = s1.position - s0.position
                t = (pos - s0.position) / span if span > 0 else 0.0
                r = int(s0.color.red() + t * (s1.color.red() - s0.color.red()) + 0.5)
                g = int(s0.color.green() + t * (s1.color.green() - s0.color.green()) + 0.5)
                b = int(s0.color.blue() + t * (s1.color.blue() - s0.color.blue()) + 0.5)
                a = int(s0.color.alpha() + t * (s1.color.alpha() - s0.color.alpha()) + 0.5)
                return QColor(r, g, b, a)
        return QColor(128, 128, 128, 255)

    def _pick_handle_color(self, stop: _Stop) -> None:
        from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup  # noqa: PLC0415

        new = ColorPickerPopup.pick_color(stop.color, self.window())
        if new is not None and new.isValid():
            stop.color = new
            self.stops_changed.emit()
            self.update()


class GradientStopsWidget(QWidget):
    """Interactive gradient-stop editor with bar, handles, and delete button.

    Value format: ``list[list[float, list[int]]]`` -- compatible with
    JSON / ``.lstroke`` serialisation.
    """

    stops_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._bar = _GradientBar(self)
        layout.addWidget(self._bar)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.addStretch()

        self._btn_delete = QPushButton(self)
        self._btn_delete.setText("\u2212")  # minus sign
        self._btn_delete.setToolTip("Delete selected stop")
        self._btn_delete.setFixedSize(24, 20)
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)

        layout.addLayout(btn_row)

        self._bar.stops_changed.connect(self._on_bar_changed)
        self._bar.selection_changed.connect(self._update_delete_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def value(self) -> list[list[Any]]:
        """Return gradient stops in JSON-compatible format."""
        return [
            [s.position, [s.color.red(), s.color.green(), s.color.blue(), s.color.alpha()]]
            for s in self._bar.stops
        ]

    def setValue(self, stops: Any) -> None:  # noqa: N802
        """Set gradient stops from JSON-compatible format."""
        # Skip external updates while the user is dragging a handle
        if self._bar._dragging:
            return
        if not isinstance(stops, (list, tuple)) or len(stops) < 2:
            return
        parsed: list[_Stop] = []
        for entry in stops:
            try:
                pos = float(entry[0])
                rgba = entry[1]
                r, g, b = int(rgba[0]), int(rgba[1]), int(rgba[2])
                a = int(rgba[3]) if len(rgba) > 3 else 255
                parsed.append(_Stop(pos, QColor(r, g, b, a)))
            except (TypeError, IndexError, ValueError):
                continue
        if len(parsed) < 2:
            return
        parsed.sort(key=lambda s: s.position)
        self._bar.set_stops_data(parsed)
        self._update_delete_btn()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _on_bar_changed(self) -> None:
        self.stops_changed.emit()

    def _on_delete(self) -> None:
        if self._bar.delete_selected():
            self._update_delete_btn()

    def _update_delete_btn(self) -> None:
        self._btn_delete.setEnabled(self._bar.can_delete_selected())
