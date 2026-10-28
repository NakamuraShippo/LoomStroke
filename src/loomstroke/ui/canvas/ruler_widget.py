"""Ruler widget for horizontal/vertical rulers."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QDialog, QWidget

from ..dialogs.guide_layout_dialog import GuideLayoutDialog
from .guide_controller import GuideController
from .guide_dialog_preview import close_guide_preview, preview_page_matches
from .guide_models import GuideOrientation, RulerUnit


class RulerWidget(QWidget):
    """Draws a Photoshop-style ruler for one axis."""

    def __init__(
        self,
        *,
        orientation: GuideOrientation,
        canvas: QWidget,
        guide_controller: GuideController,
        thickness_px: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._orientation = orientation
        self._canvas = canvas
        self._guide_controller = guide_controller
        self._thickness = max(12, int(thickness_px))
        self._cursor_canvas_pos: QPointF | None = None
        self._dragging = False
        self.setMouseTracking(True)

    def set_thickness(self, thickness_px: int) -> None:
        self._thickness = max(12, int(thickness_px))
        self.updateGeometry()
        self.update()

    def set_cursor_canvas_pos(self, pos: QPointF | None) -> None:
        self._cursor_canvas_pos = pos
        self.update()

    def sizeHint(self) -> QSize:
        if self._orientation == "horizontal":
            return QSize(100, self._thickness)
        return QSize(self._thickness, 100)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        base_color, tick_color = self._ruler_colors()
        painter.fillRect(self.rect(), base_color)
        pen = QPen(tick_color)
        pen.setCosmetic(True)
        painter.setPen(pen)

        viewport = getattr(self._canvas, "viewport", None)
        if viewport is None:
            return

        ruler_state = self._guide_controller.ruler_state()
        unit = ruler_state.unit
        origin = ruler_state.origin
        dpi_x, dpi_y = self._guide_controller.document_dpi()

        if self._orientation == "horizontal":
            length = self.width()
            if length <= 0:
                return
            c0 = viewport.widget_to_canvas(0, 0)[0]
            c1 = viewport.widget_to_canvas(length, 0)[0]
            step = self._choose_step(unit, dpi_x, viewport)
            self._draw_ticks(
                painter,
                length,
                c0,
                c1,
                origin.x(),
                step,
                unit,
                dpi_x,
                True,
            )
            self._draw_baseline(painter, horizontal=True)
            self._draw_cursor_marker(painter, length, c0, c1, origin.x(), True)
        else:
            length = self.height()
            if length <= 0:
                return
            c0 = viewport.widget_to_canvas(0, 0)[1]
            c1 = viewport.widget_to_canvas(0, length)[1]
            step = self._choose_step(unit, dpi_y, viewport)
            self._draw_ticks(
                painter,
                length,
                c0,
                c1,
                origin.y(),
                step,
                unit,
                dpi_y,
                False,
            )
            self._draw_baseline(painter, horizontal=False)
            self._draw_cursor_marker(painter, length, c0, c1, origin.y(), False)

    # ------------------------------------------------------------------
    # Interaction: drag from ruler to create guides
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        state = getattr(self._canvas, "state", None)
        ruler = self._guide_controller.ruler_state()
        dialog = GuideLayoutDialog(
            size=(int(getattr(state, "width", 1)), int(getattr(state, "height", 1))),
            dpi=self._guide_controller.document_dpi(),
            unit=ruler.unit,
            origin=(ruler.origin.x(), ruler.origin.y()),
            parent=self,
        )
        dialog.replace.setEnabled(not self._guide_controller.guides_locked)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and preview_page_matches(dialog):
                self._guide_controller.apply_guides(
                    dialog.guide_positions(), replace=dialog.replace.isChecked()
                )
        finally:
            close_guide_preview(dialog)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self.grabMouse()
        canvas_pos = self._canvas_pos_from_global(event.globalPosition().toPoint())
        orientation: GuideOrientation = (
            "horizontal" if self._orientation == "horizontal" else "vertical"
        )
        self._guide_controller.begin_ruler_drag(orientation, canvas_pos)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        canvas_pos = self._canvas_pos_from_global(event.globalPosition().toPoint())
        self._guide_controller.update_ruler_drag(canvas_pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._dragging:
            return
        self._dragging = False
        self.releaseMouse()
        canvas_pos = self._canvas_pos_from_global(event.globalPosition().toPoint())
        self._guide_controller.end_ruler_drag(canvas_pos)

    # ------------------------------------------------------------------
    # Helpers
    def _choose_step(self, unit: RulerUnit, dpi: float, viewport: object) -> float:
        target_px = 80.0
        screen_to_canvas = getattr(viewport, "screen_to_canvas_distance", None)
        canvas_to_screen = getattr(viewport, "canvas_to_screen_distance", None)
        if not callable(screen_to_canvas) or not callable(canvas_to_screen):
            return 10.0
        canvas_target_px = float(screen_to_canvas(target_px))
        unit_target = self._px_to_unit(canvas_target_px, unit, dpi)
        if unit_target <= 0:
            unit_target = 1.0

        base = 10 ** math.floor(math.log10(unit_target))
        candidates = [1.0, 2.0, 5.0]
        best_step_unit = base
        best_score = float("inf")
        for multiplier in candidates:
            step_unit = multiplier * base
            step_px = self._unit_to_px(step_unit, unit, dpi)
            screen = float(canvas_to_screen(step_px))
            if 50.0 <= screen <= 120.0:
                return step_px
            score = abs(screen - target_px)
            if score < best_score:
                best_score = score
                best_step_unit = step_unit
        return self._unit_to_px(best_step_unit, unit, dpi)

    def _unit_to_px(self, value: float, unit: RulerUnit, dpi: float) -> float:
        if unit == "px":
            return value
        if unit == "inch":
            return value * dpi
        return value * dpi / 25.4

    def _px_to_unit(self, value_px: float, unit: RulerUnit, dpi: float) -> float:
        if unit == "px":
            return value_px
        if unit == "inch":
            return value_px / dpi
        return value_px / dpi * 25.4

    def _draw_ticks(
        self,
        painter: QPainter,
        length_px: int,
        c0: float,
        c1: float,
        origin: float,
        step_px: float,
        unit: RulerUnit,
        dpi: float,
        horizontal: bool,
    ) -> None:
        if c0 == c1 or step_px <= 0:
            return
        min_c = min(c0, c1)
        max_c = max(c0, c1)
        start = math.floor((min_c - origin) / step_px) * step_px + origin
        font_metrics = QFontMetrics(self.font())
        minor_step = step_px / 5.0
        draw_minor = self._should_draw_minor(step_px, dpi, horizontal)

        value = start
        while value <= max_c:
            screen_pos = self._map_canvas_to_screen(value, c0, c1, length_px)
            if 0 <= screen_pos <= length_px:
                if horizontal:
                    self._draw_tick_line(painter, int(screen_pos), horizontal, True)
                    self._draw_tick_label(
                        painter,
                        int(screen_pos),
                        value - origin,
                        unit,
                        dpi,
                        font_metrics,
                        horizontal,
                    )
                else:
                    self._draw_tick_line(painter, int(screen_pos), horizontal, True)
                    self._draw_tick_label(
                        painter,
                        int(screen_pos),
                        value - origin,
                        unit,
                        dpi,
                        font_metrics,
                        horizontal,
                    )
            if draw_minor:
                minor_value = value + minor_step
                for _ in range(4):
                    if minor_value >= max_c:
                        break
                    screen_minor = self._map_canvas_to_screen(minor_value, c0, c1, length_px)
                    if 0 <= screen_minor <= length_px:
                        self._draw_tick_line(painter, int(screen_minor), horizontal, False)
                    minor_value += minor_step
            value += step_px

    def _draw_tick_line(self, painter: QPainter, pos: int, horizontal: bool, major: bool) -> None:
        major_len, minor_len = self._tick_lengths()
        length = major_len if major else minor_len
        if horizontal:
            painter.drawLine(pos, self._thickness - 1, pos, self._thickness - length)
        else:
            painter.drawLine(self._thickness - 1, pos, self._thickness - length, pos)

    def _draw_tick_label(
        self,
        painter: QPainter,
        pos: int,
        value_px: float,
        unit: RulerUnit,
        dpi: float,
        font_metrics: QFontMetrics,
        horizontal: bool,
    ) -> None:
        value_unit = self._px_to_unit(value_px, unit, dpi)
        text = self._format_label(value_unit, unit)
        if not text:
            return
        if horizontal:
            text_width = font_metrics.horizontalAdvance(text)
            rect = QRect(pos + 2, 0, text_width + 4, self._thickness - 2)
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        else:
            rect = QRect(
                0, pos - font_metrics.height() // 2, self._thickness - 2, font_metrics.height()
            )
            painter.save()
            painter.translate(rect.center())
            painter.rotate(-90)
            painter.translate(-rect.center())
            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            painter.restore()

    def _format_label(self, value: float, unit: RulerUnit) -> str:
        if unit == "px":
            return str(round(value))
        if abs(value) < 10:
            return f"{value:.1f}"
        return f"{value:.0f}"

    def _map_canvas_to_screen(self, value: float, c0: float, c1: float, length_px: int) -> float:
        if c1 == c0:
            return 0.0
        return (value - c0) * float(length_px) / float(c1 - c0)

    def _should_draw_minor(self, step_px: float, dpi: float, horizontal: bool) -> bool:
        viewport = getattr(self._canvas, "viewport", None)
        if viewport is None:
            return False
        canvas_to_screen = getattr(viewport, "canvas_to_screen_distance", None)
        if not callable(canvas_to_screen):
            return False
        screen = float(canvas_to_screen(step_px / 5.0))
        return screen >= 8.0

    def _draw_cursor_marker(
        self,
        painter: QPainter,
        length_px: int,
        c0: float,
        c1: float,
        origin: float,
        horizontal: bool,
    ) -> None:
        if self._cursor_canvas_pos is None or c0 == c1:
            return
        value = self._cursor_canvas_pos.x() if horizontal else self._cursor_canvas_pos.y()
        screen_pos = self._map_canvas_to_screen(value, c0, c1, length_px)
        if not (0 <= screen_pos <= length_px):
            return
        marker_color = self.palette().highlight().color()
        marker_pen = QPen(marker_color)
        marker_pen.setCosmetic(True)
        painter.save()
        painter.setPen(marker_pen)
        marker_pos = round(screen_pos)
        if horizontal:
            painter.drawLine(marker_pos, 0, marker_pos, self._thickness)
        else:
            painter.drawLine(0, marker_pos, self._thickness, marker_pos)
        painter.restore()

    def _canvas_pos_from_global(self, global_pos: Any) -> QPointF:
        widget_pos = self._canvas.mapFromGlobal(global_pos)
        viewport = getattr(self._canvas, "viewport", None)
        if viewport is None:
            return QPointF(0.0, 0.0)
        canvas_xy = viewport.widget_to_canvas(widget_pos.x(), widget_pos.y())
        return QPointF(float(canvas_xy[0]), float(canvas_xy[1]))

    def _ruler_colors(self) -> tuple[QColor, QColor]:
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Window)
        tick = palette.color(QPalette.ColorRole.Dark)
        if abs(tick.lightness() - base.lightness()) < 16:
            alt = palette.color(QPalette.ColorRole.WindowText)
            if abs(alt.lightness() - base.lightness()) >= 16:
                tick = alt
            else:
                tick = base.darker(140) if base.lightness() > 128 else base.lighter(140)
        return base, tick

    def _tick_lengths(self) -> tuple[int, int]:
        max_len = max(2, self._thickness - 2)
        major_len = min(max_len, max(8, int(self._thickness * 0.6)))
        minor_len = min(max_len, max(4, int(self._thickness * 0.35)))
        return major_len, minor_len

    def _draw_baseline(self, painter: QPainter, *, horizontal: bool) -> None:
        if horizontal:
            painter.drawLine(0, self._thickness - 1, self.width(), self._thickness - 1)
        else:
            painter.drawLine(self._thickness - 1, 0, self._thickness - 1, self.height())
