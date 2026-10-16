"""Interactive graph widgets for tone-adjustment filters.

Provides Photoshop-style draggable graph controls for ``LevelsFilter`` and
``CurvesFilter`` so users can shape the LUT visually instead of guessing
numeric values. Both widgets are driven by the same parameter dict the
:class:`FilterPanel` already maintains, so adding them to other LUT-based
filters in the future is purely additive.

Histogram overlay is intentionally out of scope for this iteration —
``canvas_service`` does not yet expose a "composited image below this
filter layer" API. The graph already carries the LUT so users can see
exactly how their parameters reshape input → output without it.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from loomstroke.core.filters.curves import _cached_build_lut, _normalize_points

logger = logging.getLogger(__name__)


# ---- Visual constants (kept here so both widgets render consistently) ----
_PLOT_SIZE = 200  # square plot edge in pixels
_AXIS_STRIP = 14  # marker strip width below / left of plot for Levels
_PADDING = 6  # outer padding around the whole widget

_BG_COLOR = QColor(36, 36, 36)
_BORDER_COLOR = QColor(58, 58, 58)
_GRID_COLOR = QColor(58, 58, 58)
_IDENTITY_COLOR = QColor(90, 90, 90)
_CURVE_COLOR = QColor(167, 139, 250)  # purple accent matching panel QSS
_MARKER_FILL = QColor(245, 245, 245)
_MARKER_FILL_ACTIVE = QColor(167, 139, 250)
_MARKER_OUTLINE = QColor(80, 80, 80)
_GAMMA_FILL = QColor(180, 180, 180)
_LABEL_COLOR = QColor(150, 150, 150)


# ---- Parameter clamps mirroring the filter classes themselves ----
_LEVELS_DEFAULTS: dict[str, Any] = {
    "in_black": 0,
    "in_white": 255,
    "gamma": 1.0,
    "out_black": 0,
    "out_white": 255,
}


class _LUTGraphBase(QWidget):
    """Base class with shared rendering of a 256-entry LUT polyline.

    Subclasses fill in :attr:`_lut`, parameter state, and overlay rendering /
    mouse interaction for their markers.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._lut: NDArray[Any] | None = None

    # Subclasses override; default plot rect is the upper-left square.
    def _plot_rect(self) -> QRect:
        return QRect(_PADDING, _PADDING, _PLOT_SIZE, _PLOT_SIZE)

    def _value_to_pos(self, x: int | float, y: int | float) -> QPointF:
        rect = self._plot_rect()
        px = rect.left() + float(x) * rect.width() / 255.0
        py = rect.bottom() - float(y) * rect.height() / 255.0
        return QPointF(px, py)

    def paintEvent(self, event: QPaintEvent) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_plot_background(painter)
            self._paint_lut_curve(painter)
            self._paint_overlay(painter)
        finally:
            painter.end()

    def _paint_plot_background(self, painter: QPainter) -> None:
        rect = self._plot_rect()
        painter.fillRect(rect, _BG_COLOR)
        painter.setPen(QPen(_BORDER_COLOR, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        # Quartile grid (3 vertical + 3 horizontal lines).
        painter.setPen(QPen(_GRID_COLOR, 1, Qt.PenStyle.DotLine))
        for i in range(1, 4):
            x = rect.left() + i * rect.width() // 4
            y = rect.top() + i * rect.height() // 4
            painter.drawLine(x, rect.top() + 1, x, rect.bottom() - 1)
            painter.drawLine(rect.left() + 1, y, rect.right() - 1, y)
        painter.setPen(QPen(_IDENTITY_COLOR, 1, Qt.PenStyle.DashLine))
        painter.drawLine(rect.bottomLeft(), rect.topRight())

    def _paint_lut_curve(self, painter: QPainter) -> None:
        if self._lut is None or len(self._lut) != 256:
            return
        painter.setPen(QPen(_CURVE_COLOR, 2))
        poly = QPolygonF()
        for x in range(256):
            poly.append(self._value_to_pos(x, int(self._lut[x])))
        painter.drawPolyline(poly)

    def _paint_overlay(self, painter: QPainter) -> None:
        """Override to draw markers / labels on top of the curve."""

    def _set_lut(self, lut: NDArray[Any]) -> None:
        self._lut = lut
        self.update()


class LevelsGraphWidget(_LUTGraphBase):
    """Photoshop-style Levels control: graph + 3 input + 2 output markers.

    Markers live in the strips just outside the plot box (input on the
    bottom, output on the left). Drag a triangle to update the
    corresponding parameter; the LUT polyline redraws live and the
    ``value_changed`` signal carries the change back to
    :class:`FilterPanel` so the spinbox + canvas state stay in sync.
    """

    value_changed = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Widget size: plot + axis strips on bottom and left + padding.
        side = _PLOT_SIZE + _AXIS_STRIP + _PADDING * 2
        self.setFixedSize(side, side)
        self._params: dict[str, Any] = dict(_LEVELS_DEFAULTS)
        self._dragging: str | None = None
        self._recompute_lut()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_params(self, params: dict[str, Any]) -> None:
        """Adopt the given param values and redraw the LUT.

        Unknown keys are ignored so the panel can pass its full
        ``_collect_params()`` dict without filtering. Type coercion is
        defensive: out-of-range or wrongly-typed values fall back to the
        previous value rather than raising.
        """
        for key in self._params:
            if key not in params:
                continue
            value = params[key]
            if value is None:
                continue
            try:
                self._params[key] = float(value) if key == "gamma" else int(value)
            except (TypeError, ValueError):
                continue
        self._recompute_lut()

    def get_params(self) -> dict[str, Any]:
        return dict(self._params)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def _plot_rect(self) -> QRect:
        # Plot is in the top-right corner; strips occupy the bottom &
        # left so all axis markers sit outside the curve box.
        return QRect(_PADDING + _AXIS_STRIP, _PADDING, _PLOT_SIZE, _PLOT_SIZE)

    def _input_strip_rect(self) -> QRect:
        rect = self._plot_rect()
        return QRect(rect.left(), rect.bottom() + 2, rect.width(), _AXIS_STRIP - 2)

    def _output_strip_rect(self) -> QRect:
        rect = self._plot_rect()
        return QRect(rect.left() - _AXIS_STRIP, rect.top(), _AXIS_STRIP - 2, rect.height())

    def _input_x(self, value: int) -> float:
        rect = self._plot_rect()
        return rect.left() + float(value) * rect.width() / 255.0

    def _output_y(self, value: int) -> float:
        rect = self._plot_rect()
        return rect.bottom() - float(value) * rect.height() / 255.0

    def _gamma_x(self) -> float:
        """Photoshop-style gamma marker position between in_black and in_white."""
        in_b = int(self._params["in_black"])
        in_w = int(self._params["in_white"])
        gamma = max(0.01, min(9.99, float(self._params["gamma"])))
        if in_w <= in_b:
            return float(self._input_x(in_b))
        # gamma=1 -> midway; gamma>1 -> shifted toward in_black; gamma<1 -> toward in_white.
        ratio = 0.5 ** (1.0 / gamma)
        return float(self._input_x(in_b) + ratio * (self._input_x(in_w) - self._input_x(in_b)))

    # ------------------------------------------------------------------
    # LUT recomputation (mirrors LevelsFilter._build_lut)
    # ------------------------------------------------------------------
    def _recompute_lut(self) -> None:
        in_black = max(0, min(254, int(self._params["in_black"])))
        in_white = max(in_black + 1, min(255, int(self._params["in_white"])))
        gamma = max(0.01, min(9.99, float(self._params["gamma"])))
        out_black = max(0, min(255, int(self._params["out_black"])))
        out_white = max(0, min(255, int(self._params["out_white"])))
        x = np.arange(256, dtype=np.float64)
        normalized = np.clip((x - in_black) / (in_white - in_black), 0.0, 1.0)
        gamma_corrected = np.power(normalized, 1.0 / gamma)
        result = out_black + gamma_corrected * (out_white - out_black)
        self._set_lut(np.clip(result, 0, 255).astype(np.uint8))

    # ------------------------------------------------------------------
    # Overlay rendering (markers, output ramp)
    # ------------------------------------------------------------------
    def _paint_overlay(self, painter: QPainter) -> None:
        in_strip = self._input_strip_rect()
        out_strip = self._output_strip_rect()

        # Input ramp gradient (visualises the input space).
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(in_strip.width()):
            shade = int(255 * i / max(1, in_strip.width() - 1))
            painter.setBrush(QBrush(QColor(shade, shade, shade)))
            painter.drawRect(in_strip.left() + i, in_strip.top(), 1, 4)

        # Output ramp on the left strip.
        for i in range(out_strip.height()):
            shade = int(255 * (out_strip.height() - 1 - i) / max(1, out_strip.height() - 1))
            painter.setBrush(QBrush(QColor(shade, shade, shade)))
            painter.drawRect(out_strip.right() - 4, out_strip.top() + i, 4, 1)

        # Input markers: in_black, gamma, in_white below the plot.
        baseline_y = in_strip.top() + 6
        self._paint_triangle_up(
            painter, self._input_x(int(self._params["in_black"])), baseline_y, "in_black"
        )
        self._paint_triangle_up(painter, self._gamma_x(), baseline_y, "gamma", fill=_GAMMA_FILL)
        self._paint_triangle_up(
            painter, self._input_x(int(self._params["in_white"])), baseline_y, "in_white"
        )

        # Output markers: out_black, out_white left of the plot.
        baseline_x = out_strip.right() - 6
        self._paint_triangle_right(
            painter, baseline_x, self._output_y(int(self._params["out_black"])), "out_black"
        )
        self._paint_triangle_right(
            painter, baseline_x, self._output_y(int(self._params["out_white"])), "out_white"
        )

    def _paint_triangle_up(
        self, painter: QPainter, x: float, y: float, key: str, *, fill: QColor | None = None
    ) -> None:
        active = self._dragging == key
        color = fill if fill is not None else _MARKER_FILL
        painter.setPen(QPen(_MARKER_OUTLINE, 1))
        painter.setBrush(QBrush(_MARKER_FILL_ACTIVE if active else color))
        poly = QPolygonF([QPointF(x - 5, y + 6), QPointF(x + 5, y + 6), QPointF(x, y - 4)])
        painter.drawPolygon(poly)

    def _paint_triangle_right(self, painter: QPainter, x: float, y: float, key: str) -> None:
        active = self._dragging == key
        painter.setPen(QPen(_MARKER_OUTLINE, 1))
        painter.setBrush(QBrush(_MARKER_FILL_ACTIVE if active else _MARKER_FILL))
        poly = QPolygonF([QPointF(x + 6, y - 5), QPointF(x + 6, y + 5), QPointF(x - 4, y)])
        painter.drawPolygon(poly)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = self._marker_at(event.position().x(), event.position().y())
        if self._dragging is not None:
            self.update()
            self._apply_drag(event.position().x(), event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging is None:
            return
        self._apply_drag(event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging is not None:
            self._dragging = None
            self.update()

    def _marker_at(self, mx: float, my: float) -> str | None:
        """Hit-test markers; returns the closest within tolerance or None."""
        in_strip = self._input_strip_rect()
        out_strip = self._output_strip_rect()
        candidates: list[tuple[float, str]] = []
        # Input markers respond when the cursor is in the input strip band.
        if in_strip.top() - 4 <= my <= in_strip.bottom() + 4:
            for key, x in (
                ("in_black", self._input_x(int(self._params["in_black"]))),
                ("gamma", self._gamma_x()),
                ("in_white", self._input_x(int(self._params["in_white"]))),
            ):
                candidates.append((abs(mx - x), key))
        # Output markers respond when the cursor is in the output strip band.
        if out_strip.left() - 4 <= mx <= out_strip.right() + 4:
            for key, y in (
                ("out_black", self._output_y(int(self._params["out_black"]))),
                ("out_white", self._output_y(int(self._params["out_white"]))),
            ):
                candidates.append((abs(my - y), key))
        if not candidates:
            return None
        candidates.sort()
        # 12px tolerance keeps neighbouring markers distinguishable while
        # still feeling forgiving on a high-DPI screen.
        return candidates[0][1] if candidates[0][0] <= 12 else None

    def _apply_drag(self, mx: float, my: float) -> None:
        rect = self._plot_rect()
        key = self._dragging
        if key is None:
            return
        if key in ("in_black", "in_white"):
            value = round((mx - rect.left()) / max(1, rect.width()) * 255)
            value = max(0, min(255, value))
            if key == "in_black":
                value = min(value, int(self._params["in_white"]) - 1)
            else:
                value = max(value, int(self._params["in_black"]) + 1)
            if value != int(self._params[key]):
                self._params[key] = value
                self._recompute_lut()
                self.value_changed.emit(key, value)
        elif key == "gamma":
            in_b = int(self._params["in_black"])
            in_w = int(self._params["in_white"])
            x_lo = self._input_x(in_b)
            x_hi = self._input_x(in_w)
            if x_hi <= x_lo:
                return
            ratio = (mx - x_lo) / (x_hi - x_lo)
            ratio = max(0.001, min(0.999, ratio))
            # Inverse of gamma_x mapping: ratio = 0.5 ** (1/gamma)  →  gamma = -log(2)/log(ratio)
            gamma = float(np.log(0.5) / np.log(ratio))
            gamma = max(0.01, min(9.99, gamma))
            # Quantise to 2 decimals to match the spinbox step.
            gamma = round(gamma, 2)
            if gamma != float(self._params["gamma"]):
                self._params["gamma"] = gamma
                self._recompute_lut()
                self.value_changed.emit("gamma", gamma)
        elif key in ("out_black", "out_white"):
            value = round((rect.bottom() - my) / max(1, rect.height()) * 255)
            value = max(0, min(255, value))
            if value != int(self._params[key]):
                self._params[key] = value
                self._recompute_lut()
                self.value_changed.emit(key, value)


class CurvesGraphWidget(_LUTGraphBase):
    """Free-form tone curve editor.

    The user shapes the curve directly: drag a control point to move it,
    left-click on the curve to insert a new point, right-click on a
    middle point to delete it. Endpoints (X=0 and X=255) are always
    present and only their Y is editable. The widget acts as a single
    parameter widget for the ``curve_points`` schema type — no separate
    sliders accompany it.

    Public API matches the convention used by other complex-value
    widgets in the panel (``GradientStopsWidget``): :meth:`value` /
    :meth:`setValue` round-trip a list of ``(x, y)`` tuples, and
    :attr:`valueChanged` fires whenever the user edits the curve.
    """

    valueChanged = Signal(object)
    # Larger plot than Levels — more room for free-form editing pays off.
    _CURVE_PLOT_SIZE = 220
    _MAX_POINTS = 16
    _HIT_RADIUS = 10
    _MIN_DX = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        side = self._CURVE_PLOT_SIZE + _PADDING * 2
        self.setFixedSize(side, side)
        self._points: list[tuple[int, int]] = [(0, 0), (255, 255)]
        self._dragging_index: int | None = None
        # Default Qt behaviour (mouseMoveEvent only when a button is held)
        # is what we want — there is no hover state in this widget so
        # ``setMouseTracking(True)`` would only generate spurious events.
        self._recompute_lut()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def value(self) -> list[tuple[int, int]]:
        return [(int(x), int(y)) for x, y in self._points]

    def setValue(self, points: Any) -> None:  # noqa: N802 — Qt naming convention
        coerced = self._coerce_points(points)
        if not coerced:
            return
        # Same-value early return — every user drag round-trips
        # value → submit → canvas → ``effect.params.changed`` → panel
        # ``_set_param_values`` → setValue with the *same* points we just
        # emitted. Bailing here skips a redundant LUT recompute and a
        # repaint per drag tick. Comparing tuple-of-tuples is O(n) and
        # n ≤ 16 in practice.
        if coerced == self._points:
            return
        # Preserve any in-progress drag: ``_apply_drag`` clamps the
        # index so a genuinely external setValue (e.g. preset load)
        # that changes the point count can't crash a stale drag.
        self._points = coerced
        self._recompute_lut()

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------
    def _plot_rect(self) -> QRect:
        return QRect(_PADDING, _PADDING, self._CURVE_PLOT_SIZE, self._CURVE_PLOT_SIZE)

    def _pixel_to_value(self, mx: float, my: float) -> tuple[int, int]:
        rect = self._plot_rect()
        x = round((mx - rect.left()) / max(1, rect.width()) * 255)
        y = round((rect.bottom() - my) / max(1, rect.height()) * 255)
        return (max(0, min(255, x)), max(0, min(255, y)))

    # ------------------------------------------------------------------
    # LUT recomputation (shares the cached helper with CurvesFilter)
    # ------------------------------------------------------------------
    def _recompute_lut(self) -> None:
        normalized = _normalize_points(self._points)
        # ``_cached_build_lut`` returns a shared array — never mutate;
        # ``_paint_lut_curve`` only reads it.
        self._set_lut(_cached_build_lut(tuple(normalized)))

    # ------------------------------------------------------------------
    # Overlay (control point markers)
    # ------------------------------------------------------------------
    def _paint_overlay(self, painter: QPainter) -> None:
        for i, (x, y) in enumerate(self._points):
            pos = self._value_to_pos(x, y)
            active = self._dragging_index == i
            painter.setPen(QPen(_MARKER_OUTLINE, 1))
            painter.setBrush(QBrush(_MARKER_FILL_ACTIVE if active else _MARKER_FILL))
            painter.drawEllipse(pos, 5.0, 5.0)

    # ------------------------------------------------------------------
    # Mouse interaction
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        mx = event.position().x()
        my = event.position().y()
        if event.button() == Qt.MouseButton.RightButton:
            self._handle_right_click(mx, my)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        hit = self._point_at(mx, my)
        if hit is not None:
            self._dragging_index = hit
            self.update()
            return
        # Click on empty area inside the plot → insert a new control point.
        rect = self._plot_rect()
        if not rect.contains(int(mx), int(my)):
            return
        if len(self._points) >= self._MAX_POINTS:
            return
        new_value = self._pixel_to_value(mx, my)
        index = self._insert_point(new_value)
        if index is not None:
            self._dragging_index = index
            self._recompute_lut()
            self.valueChanged.emit(self.value())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging_index is None:
            return
        self._apply_drag(event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging_index is not None:
            self._dragging_index = None
            self.update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _point_at(self, mx: float, my: float) -> int | None:
        best: tuple[float, int] | None = None
        for i, (x, y) in enumerate(self._points):
            pos = self._value_to_pos(x, y)
            d = ((mx - pos.x()) ** 2 + (my - pos.y()) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, i)
        return best[1] if best is not None and best[0] <= self._HIT_RADIUS else None

    def _handle_right_click(self, mx: float, my: float) -> None:
        idx = self._point_at(mx, my)
        if idx is None:
            return
        # Endpoints are non-removable; the curve always spans 0..255.
        if idx == 0 or idx == len(self._points) - 1:
            return
        del self._points[idx]
        self._dragging_index = None
        self._recompute_lut()
        self.valueChanged.emit(self.value())

    def _insert_point(self, value: tuple[int, int]) -> int | None:
        x, y = value
        # Refuse to insert too close to an existing X — keeps the cubic
        # interpolator's strictly-increasing X invariant intact.
        for px, _py in self._points:
            if abs(px - x) < self._MIN_DX:
                return None
        self._points.append((x, y))
        self._points.sort(key=lambda p: p[0])
        return [p[0] for p in self._points].index(x)

    def _apply_drag(self, mx: float, my: float) -> None:
        idx = self._dragging_index
        if idx is None or idx >= len(self._points):
            return
        new_x, new_y = self._pixel_to_value(mx, my)
        old_x, old_y = self._points[idx]
        # Endpoints: X is locked, only Y editable.
        if idx == 0:
            new_x = 0
        elif idx == len(self._points) - 1:
            new_x = 255
        else:
            # Middle point: keep strict X ordering with neighbours.
            prev_x = self._points[idx - 1][0]
            next_x = self._points[idx + 1][0]
            new_x = max(prev_x + self._MIN_DX, min(next_x - self._MIN_DX, new_x))
        if new_x == old_x and new_y == old_y:
            return
        self._points[idx] = (new_x, new_y)
        self._recompute_lut()
        self.valueChanged.emit(self.value())

    @staticmethod
    def _coerce_points(raw: Any) -> list[tuple[int, int]]:
        """Defensively turn any sequence-of-pairs into a clean point list."""
        if not isinstance(raw, (list, tuple)):
            return []
        out: list[tuple[int, int]] = []
        for entry in raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            try:
                x = int(entry[0])
                y = int(entry[1])
            except (TypeError, ValueError):
                continue
            out.append((max(0, min(255, x)), max(0, min(255, y))))
        if not out:
            return []
        # Sort by X so the editor renders in canonical order.
        out.sort(key=lambda p: p[0])
        # Always anchor the endpoints — the schema's identity default
        # already respects this, but loaded data might not.
        if out[0][0] != 0:
            out.insert(0, (0, out[0][1]))
        if out[-1][0] != 255:
            out.append((255, out[-1][1]))
        return out
