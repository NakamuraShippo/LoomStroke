"""Qt widgets used by the color manager plugin."""

from __future__ import annotations

import math
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QImage, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.color_state import ColorDescriptor, ColorStateSnapshot
from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.plugins.color_manager.color_models import ColorPalette, PaletteSwatch
from loomstroke.ui.translations import TranslationBinder

from .color_mode_editor import ColorModeEditor


class ColorPreviewFrame(QFrame):
    """Displays a single color preview tile."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(56, 56)
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self._descriptor = ColorDescriptor((1.0, 1.0, 1.0, 1.0))
        self._apply_style()

    def descriptor(self) -> ColorDescriptor:
        return self._descriptor

    def set_color(self, descriptor: ColorDescriptor | None) -> None:
        if descriptor is None:
            descriptor = ColorDescriptor((0.0, 0.0, 0.0, 1.0))
        self._descriptor = descriptor
        self._apply_style()

    def _apply_style(self, *, empty: bool = False) -> None:
        if empty:
            self.setStyleSheet("background-color: #444; border: 1px solid #2b2b2b;")
            return
        r, g, b, _ = self._descriptor.rgba
        hex_color = f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"
        self.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #2b2b2b;")


class HistorySwatchButton(QPushButton):
    """Clickable swatch used in the history view."""

    def __init__(self, descriptor: ColorDescriptor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        self.setFixedSize(24, 24)
        r, g, b, _ = descriptor.rgba
        hex_color = f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"
        self.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #1f1f1f;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(hex_color)

    @property
    def descriptor(self) -> ColorDescriptor:
        return self._descriptor


class ColorHistoryView(QWidget):
    """Horizontal list of recently used colors."""

    colorActivated = Signal(ColorDescriptor)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

    def set_history(self, history: Sequence[ColorDescriptor]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        seen: set[tuple[float, float, float, float]] = set()
        for descriptor in history:
            key = descriptor.rgba
            if key in seen:
                continue
            seen.add(key)
            button = HistorySwatchButton(descriptor, self)
            button.clicked.connect(lambda _checked=False, d=descriptor: self.colorActivated.emit(d))
            self._layout.addWidget(button)
            if len(seen) >= 10:
                break
        self._layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )


class ColorWheelWidget(QWidget):
    """Hue wheel with transparent center to host the SV triangle."""

    hueChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hue = 0.0
        self._ring_thickness = 24
        self._outer_margin = 2.0
        self._cache: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_hue(self, hue: float) -> None:
        self._hue = hue % 1.0
        self.update()

    def set_ring_thickness(self, thickness: int) -> None:
        clamped = max(6, int(thickness))
        max_thickness = max(1, int(self.outer_radius() - 1))
        clamped = min(clamped, max_thickness)
        if clamped == self._ring_thickness:
            return
        self._ring_thickness = clamped
        self._cache = None
        self.update()

    def outer_radius(self) -> float:
        return max(0.0, min(self.width(), self.height()) / 2 - self._outer_margin)

    def inner_radius(self) -> float:
        return max(0.0, self.outer_radius() - self._ring_thickness)

    def paintEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._generate_wheel()
        painter = QPainter(self)
        painter.drawImage(0, 0, self._cache)
        center = QPointF(self.width() / 2, self.height() / 2)
        outer_radius = self.outer_radius()
        angle = self._hue * 2 * math.pi
        marker_radius = max(0.0, outer_radius - 8)
        marker = QPointF(
            center.x() + math.cos(angle) * marker_radius,
            center.y() - math.sin(angle) * marker_radius,
        )
        painter.setPen(Qt.GlobalColor.black)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.drawEllipse(marker, 5, 5)

    def mousePressEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        self._update_from_pos(event.position())

    def mouseMoveEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._update_from_pos(event.position())

    def _generate_wheel(self) -> QImage:
        size = self.size()
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        center_x = size.width() / 2
        center_y = size.height() / 2
        radius = self.outer_radius()
        inner_radius = max(0.0, radius - self._ring_thickness)
        for y in range(size.height()):
            for x in range(size.width()):
                dx = x - center_x
                dy = center_y - y
                dist = math.hypot(dx, dy)
                if dist > radius:
                    continue
                if dist < inner_radius:
                    continue
                hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
                color = QColor.fromHsvF(hue, 1.0, 1.0)
                image.setPixelColor(x, y, color)
        return image

    def _update_from_pos(self, pos: QPointF) -> None:
        center = QPointF(self.width() / 2, self.height() / 2)
        dx = pos.x() - center.x()
        dy = center.y() - pos.y()
        radius = self.outer_radius()
        inner_radius = max(0.0, radius - self._ring_thickness)
        dist = math.hypot(dx, dy)
        if dist < inner_radius or dist > radius:
            return
        angle = math.atan2(dy, dx)
        self._hue = (angle / (2 * math.pi)) % 1.0
        self.hueChanged.emit(self._hue)
        self.update()


class ToneTriangleWidget(QWidget):
    """Triangle widget used to control saturation and value."""

    svChanged = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(80, 80)
        self._hue = 0.0
        self._saturation = 0.0
        self._value = 1.0
        self._cache: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._hue = h % 1.0
        self._saturation = max(0.0, min(1.0, s))
        self._value = max(0.0, min(1.0, v))
        self._cache = None
        self.update()

    def paintEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        if self._cache is None or self._cache.size() != self.size():
            self._cache = self._generate_triangle()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, self._cache)
        points = self._triangle_points()
        painter.setPen(Qt.GlobalColor.black)
        painter.drawPolygon(points)
        marker = self._point_from_sv(self._saturation, self._value)
        painter.setBrush(Qt.GlobalColor.transparent)
        painter.drawEllipse(marker, 5, 5)

    def mousePressEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        self._update_from_pos(event.position())

    def mouseMoveEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._update_from_pos(event.position())

    def _triangle_points(self) -> QPolygonF:
        w = self.width()
        h = self.height()
        size = min(w, h)
        center = QPointF(w / 2, h / 2)
        radius = max(1.0, size / 2 - 0.5)
        angles = (-90.0, 150.0, 30.0)
        points = [
            QPointF(
                center.x() + math.cos(math.radians(angle)) * radius,
                center.y() + math.sin(math.radians(angle)) * radius,
            )
            for angle in angles
        ]
        return QPolygonF(points)

    def _generate_triangle(self) -> QImage:
        size = self.size()
        image = QImage(size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        poly = self._triangle_points()
        min_x = int(min(pt.x() for pt in poly))
        max_x = int(max(pt.x() for pt in poly))
        min_y = int(min(pt.y() for pt in poly))
        max_y = int(max(pt.y() for pt in poly))
        for y in range(min_y, max_y + 1):
            for x in range(min_x, max_x + 1):
                point = QPointF(x, y)
                if not poly.containsPoint(point, Qt.FillRule.WindingFill):
                    continue
                s, v = self._sv_from_point(point, poly)
                color = QColor.fromHsvF(self._hue, s, v)
                image.setPixelColor(x, y, color)
        return image

    def _sv_from_point(self, point: QPointF, poly: QPolygonF) -> tuple[float, float]:
        a, b, c = poly[0], poly[1], poly[2]
        denom = (b.y() - c.y()) * (a.x() - c.x()) + (c.x() - b.x()) * (a.y() - c.y())
        if denom == 0:
            return 0.0, 0.0
        w1 = ((b.y() - c.y()) * (point.x() - c.x()) + (c.x() - b.x()) * (point.y() - c.y())) / denom
        w2 = ((c.y() - a.y()) * (point.x() - c.x()) + (a.x() - c.x()) * (point.y() - c.y())) / denom
        w3 = 1.0 - w1 - w2
        w1 = max(0.0, min(1.0, w1))
        w2 = max(0.0, min(1.0, w2))
        w3 = max(0.0, min(1.0, w3))
        total = w1 + w2 + w3
        if total <= 0.0:
            return 0.0, 0.0
        w1 /= total
        w2 /= total
        w3 /= total
        saturation = max(0.0, min(1.0, w3))
        value = max(0.0, min(1.0, w1 + w3))
        return saturation, value

    def _point_from_sv(self, s: float, v: float) -> QPointF:
        poly = self._triangle_points()
        a, b, c = poly[0], poly[1], poly[2]
        s = max(0.0, min(1.0, s))
        v = max(0.0, min(1.0, v))
        w3 = s
        w1 = max(0.0, min(1.0, v - s))
        w2 = max(0.0, 1.0 - w1 - w3)
        total = w1 + w2 + w3
        if total <= 0.0:
            return QPointF((b.x() + c.x()) / 2, (b.y() + c.y()) / 2)
        w1 /= total
        w2 /= total
        w3 /= total
        point = QPointF(
            a.x() * w1 + b.x() * w2 + c.x() * w3,
            a.y() * w1 + b.y() * w2 + c.y() * w3,
        )
        return point

    def _clamp_point_to_triangle(self, point: QPointF, poly: QPolygonF) -> QPointF:
        if poly.containsPoint(point, Qt.FillRule.WindingFill):
            return point
        closest: QPointF | None = None
        closest_dist_sq = math.inf
        for index in range(poly.count()):
            start = poly[index]
            end = poly[(index + 1) % poly.count()]
            dx = end.x() - start.x()
            dy = end.y() - start.y()
            length_sq = dx * dx + dy * dy
            if length_sq <= 0.0:
                candidate = QPointF(start)
            else:
                t = ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy) / length_sq
                t = max(0.0, min(1.0, t))
                candidate = QPointF(start.x() + dx * t, start.y() + dy * t)
            dist_x = point.x() - candidate.x()
            dist_y = point.y() - candidate.y()
            dist_sq = dist_x * dist_x + dist_y * dist_y
            if dist_sq < closest_dist_sq:
                closest = candidate
                closest_dist_sq = dist_sq
        return closest if closest is not None else point

    def _update_from_pos(self, pos: QPointF) -> None:
        poly = self._triangle_points()
        clamped_pos = self._clamp_point_to_triangle(pos, poly)
        s, v = self._sv_from_point(clamped_pos, poly)
        self._saturation = s
        self._value = v
        self.svChanged.emit(s, v)
        self.update()


class ColorSelectorWidget(QWidget):
    """Overlay widget combining the hue ring and the SV triangle."""

    hueChanged = Signal(float)
    svChanged = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._wheel = ColorWheelWidget(self)
        self._triangle = ToneTriangleWidget(self)
        self._current_h = 0.0
        self._current_s = 0.0
        self._current_v = 1.0
        self._block = False

        self._wheel.hueChanged.connect(self._on_wheel_hue_changed)
        self._triangle.svChanged.connect(self._on_triangle_sv_changed)

    def set_hsv(self, h: float, s: float, v: float) -> None:
        self._block = True
        self._current_h = h % 1.0
        self._current_s = max(0.0, min(1.0, s))
        self._current_v = max(0.0, min(1.0, v))
        self._wheel.set_hue(self._current_h)
        self._triangle.set_hsv(self._current_h, self._current_s, self._current_v)
        self._block = False

    def resizeEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        size = min(self.width(), self.height())
        wheel_size = size
        wheel_x = (self.width() - wheel_size) // 2
        wheel_y = (self.height() - wheel_size) // 2
        self._wheel.setGeometry(wheel_x, wheel_y, wheel_size, wheel_size)

        ring_thickness = max(22, int(wheel_size * 0.16))
        self._wheel.set_ring_thickness(ring_thickness)
        inner_radius = self._wheel.inner_radius()
        triangle_size = max(40, int(inner_radius * 2))
        tri_x = wheel_x + (wheel_size - triangle_size) // 2
        tri_y = wheel_y + (wheel_size - triangle_size) // 2
        self._triangle.setGeometry(tri_x, tri_y, triangle_size, triangle_size)
        QWidget.resizeEvent(self, event)

    def _on_wheel_hue_changed(self, hue: float) -> None:
        if self._block:
            return
        self._current_h = hue
        self._triangle.set_hsv(hue, self._current_s, self._current_v)
        self.hueChanged.emit(hue)

    def _on_triangle_sv_changed(self, saturation: float, value: float) -> None:
        if self._block:
            return
        self._current_s = saturation
        self._current_v = value
        self.svChanged.emit(saturation, value)


class PaletteGridView(QScrollArea):
    """Scrollable grid of palette swatches."""

    swatchActivated = Signal(ColorDescriptor)
    swatchRemoved = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._container = QWidget()
        self._container.setAutoFillBackground(True)
        self._container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._layout = QGridLayout(self._container)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._gap = 1
        self._layout.setSpacing(self._gap)
        self._layout.setHorizontalSpacing(self._gap)
        self._layout.setVerticalSpacing(self._gap)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self._container)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("QScrollArea { border: 0; }")
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Expanding)
        self._palettes: list[ColorPalette] = []
        self._swatch_edge = 32
        self._max_columns_limit = 32
        self._max_columns = self._max_columns_limit
        self._current_columns = 0
        base_height = self._swatch_edge + self._gap * 2
        self.setMinimumHeight(base_height)
        self._container.setMinimumHeight(base_height)

    def set_palettes(self, palettes: Sequence[ColorPalette]) -> None:
        self._palettes = list(palettes)
        self._current_columns = 0
        self._rebuild()

    def set_max_columns(self, columns: int) -> None:
        columns = max(1, int(columns))
        if columns == self._max_columns:
            return
        self._max_columns = columns
        self._current_columns = 0
        self._rebuild()

    def fit_to_width(self, width: int) -> None:
        available = max(0, width - self._gap)
        column_span = self._swatch_edge + self._gap
        if column_span <= 0:
            return
        columns = max(1, min(self._max_columns_limit, int(available // column_span)))
        self.set_max_columns(columns)

    def _rebuild(self, *, forced_columns: int | None = None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for row in range(self._max_columns * 4):
            self._layout.setRowMinimumHeight(row, 0)
            self._layout.setRowStretch(row, 0)

        if not self._palettes:
            self._current_columns = 0
            base_height = self._swatch_edge + self._gap * 2
            self._container.setMinimumHeight(base_height)
            self._container.setMaximumHeight(base_height)
            return

        palette = self._palettes[0]
        total = len(palette.swatches)
        self._current_columns = self._max_columns if total else 0

        for index, swatch in enumerate(palette.swatches):
            tile = SwatchTile(swatch)
            tile.swatchActivated.connect(self.swatchActivated.emit)
            tile.swatchDeleted.connect(self.swatchRemoved.emit)
            row = index // self._max_columns
            col = index % self._max_columns
            self._layout.addWidget(tile, row, col)
            self._layout.setRowMinimumHeight(row, self._swatch_edge + self._gap)

        rows = max(1, (total + self._max_columns - 1) // self._max_columns)
        for col in range(self._max_columns):
            self._layout.setColumnMinimumWidth(col, self._swatch_edge)
            self._layout.setColumnStretch(col, 0)

        content_height = rows * self._swatch_edge + self._gap * (rows + 1)
        self._container.setMinimumHeight(content_height)
        self._container.setMaximumHeight(content_height)

    def minimumSizeHint(self):  # type: ignore[override]  # PySide6 override stub limitation
        width = self._max_columns * self._swatch_edge + self._gap * (self._max_columns + 1)
        height = self._swatch_edge + self._gap * 2
        return QSize(width, height)

    def sizeHint(self):  # type: ignore[override]  # PySide6 override stub limitation
        total = len(self._palettes[0].swatches) if self._palettes else 0
        rows = max(1, math.ceil(total / self._max_columns)) if total else 1
        width = self._max_columns * self._swatch_edge + self._gap * (self._max_columns + 1)
        height = rows * self._swatch_edge + self._gap * (rows + 1)
        return QSize(width, height)

    def _compute_columns(self, available_width: int, total: int) -> int:
        return self._max_columns if total else self._max_columns  # noqa: RUF034

    def resizeEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        super().resizeEvent(event)
        self.fit_to_width(event.size().width())


class SwatchTile(QWidget):
    """Composite widget hosting a swatch button and delete control."""

    swatchActivated = Signal(ColorDescriptor)
    swatchDeleted = Signal(str)

    def __init__(self, swatch: PaletteSwatch, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._swatch = swatch
        self._button = SwatchButton(swatch, self)
        self._button.move(0, 0)
        self._button.clicked.connect(lambda: self.swatchActivated.emit(self._swatch.color))

        self._delete = QToolButton(self)
        self._delete.setText("x")
        self._delete.setFixedSize(12, 12)
        self._delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete.setToolTip(self._ts("Remove swatch"))
        self._delete.setStyleSheet(
            "QToolButton { background-color: rgba(0, 0, 0, 160); "
            "color: white; border: none; border-radius: 6px; }"
            "QToolButton:hover { background-color: rgba(220, 38, 38, 200); }"
        )
        self._delete.clicked.connect(lambda: self.swatchDeleted.emit(self._swatch.id))
        self._reposition_delete()
        self._delete.hide()
        self.setFixedSize(self._button.size())
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def _reposition_delete(self) -> None:
        margin = 1
        self._delete.move(self.width() - self._delete.width() - margin, margin)
        self._delete.raise_()

    def enterEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        self._delete.show()
        super().enterEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self._delete.hide()
        super().leaveEvent(event)

    def changeEvent(self, event) -> None:  # pragma: no cover - translation update path
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._delete.setToolTip(self._ts("Remove swatch"))

    def resizeEvent(self, event):  # type: ignore[override]  # PySide6 override stub limitation
        super().resizeEvent(event)
        self._button.resize(event.size())
        self._reposition_delete()

    def sizeHint(self) -> QSize:  # type: ignore[override]  # PySide6 override stub limitation
        return QSize(self._button.width(), self._button.height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]  # PySide6 override stub limitation
        return self.sizeHint()

    @property
    def swatch(self) -> PaletteSwatch:
        return self._swatch

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)


class SwatchButton(QPushButton):
    """Button representing a palette color."""

    def __init__(self, swatch: PaletteSwatch, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._swatch = swatch
        self.setFixedSize(32, 32)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setContentsMargins(0, 0, 0, 0)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        r, g, b = (int(c * 255) for c in swatch.color.rgba[:3])
        color = f"#{r:02X}{g:02X}{b:02X}"
        self.setStyleSheet(
            f"background-color: {color}; border: 0; border-radius: 0; margin: 0; padding: 0;"
        )
        self.setToolTip(swatch.label)


class ColorPanel(QWidget):
    """Main color panel widget with Krita-like selector layout."""

    colorSelected = Signal(ColorDescriptor)
    colorCommitted = Signal(ColorDescriptor)
    paletteRequested = Signal(str, ColorDescriptor)
    paletteSwatchRemoved = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[object] = []
        self._event_tokens: list[object] = []
        self._updating = False
        self._hue = 0.0
        self._saturation = 0.0
        self._value = 1.0
        self._current_descriptor_value = ColorDescriptor((1.0, 1.0, 1.0, 1.0))
        self._source_descriptor_value = self._current_descriptor_value

        self.selector = ColorSelectorWidget()
        self.hex_edit = QLineEdit("#FFFFFF")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.setPlaceholderText("#RRGGBB")
        self.hex_edit.setFixedWidth(90)
        self.hsv_inputs: dict[str, QSpinBox] = {}
        self.rgb_inputs: dict[str, QSpinBox] = {}
        add_text = self._ts("Add to Palette")
        self.add_swatch_button = QPushButton(add_text)
        self._translation_binder.register(self.add_swatch_button.setText, add_text)
        self.palette_view = PaletteGridView()
        self._source_palettes: list[ColorPalette] = []

        self._build_ui()
        self._connect_signals()
        self._apply_descriptor(self._current_descriptor_value)
        self._subscribe_language_events()

    def _build_ui(self) -> None:  # noqa: PLR0915
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        selector_container = QHBoxLayout()
        selector_container.addStretch(1)
        selector_container.addWidget(self.selector)
        selector_container.addStretch(1)
        main_layout.addLayout(selector_container)

        controls_container = QWidget()
        controls_layout = QVBoxLayout(controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)

        hsv_group = QHBoxLayout()
        hsv_group.setSpacing(6)
        hsv_group.setContentsMargins(0, 0, 0, 0)
        hsv_text = self._ts("HSV")
        hsv_label = QLabel(hsv_text)
        self._translation_binder.register(hsv_label.setText, hsv_text)
        hsv_group.addWidget(hsv_label)
        hsv_spins: list[tuple[QSpinBox, str]] = []
        for label, maximum, suffix in (("H", 359, "°"), ("S", 100, "%"), ("V", 100, "%")):
            spin = QSpinBox()
            spin.setRange(0, maximum)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if suffix:
                spin.setSuffix(suffix)
            self.hsv_inputs[label] = spin
            hsv_spins.append((spin, suffix))
            hsv_group.addWidget(spin)
        controls_layout.addLayout(hsv_group)

        rgb_group = QHBoxLayout()
        rgb_group.setSpacing(6)
        rgb_group.setContentsMargins(0, 0, 0, 0)
        rgb_text = self._ts("RGB")
        rgb_label = QLabel(rgb_text)
        self._translation_binder.register(rgb_label.setText, rgb_text)
        rgb_group.addWidget(rgb_label)
        rgb_spins: list[QSpinBox] = []
        for label in "RGB":
            spin = QSpinBox()
            spin.setRange(0, 255)
            spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rgb_inputs[label] = spin
            rgb_spins.append(spin)
            rgb_group.addWidget(spin)
        controls_layout.addLayout(rgb_group)

        spin_widths = [
            self._spin_width_for_chars(spin, 5, suffix) for spin, suffix in hsv_spins
        ] + [self._spin_width_for_chars(spin, 5) for spin in rgb_spins]
        target_spin_width = max(spin_widths) if spin_widths else 72
        for spin, _suffix in hsv_spins:
            spin.setFixedWidth(target_spin_width)
        for spin in rgb_spins:
            spin.setFixedWidth(target_spin_width)

        label_width = hsv_label.sizeHint().width()
        gaps = len(hsv_spins)
        spacing = hsv_group.spacing()
        margins = hsv_group.contentsMargins()
        hsv_row_width = (
            label_width
            + target_spin_width * len(hsv_spins)
            + spacing * gaps
            + margins.left()
            + margins.right()
        )
        self.selector.setFixedSize(hsv_row_width, hsv_row_width)
        controls_container.setFixedWidth(hsv_row_width)
        self.palette_view.fit_to_width(hsv_row_width)
        self.setMinimumWidth(hsv_row_width)

        hex_group = QHBoxLayout()
        hex_group.setSpacing(6)
        hex_group.setContentsMargins(0, 0, 0, 0)
        hex_text = self._ts("Hex")
        hex_label = QLabel(hex_text)
        self._translation_binder.register(hex_label.setText, hex_text)
        hex_group.addWidget(hex_label)
        self.hex_edit.setFixedWidth(90)
        self.hex_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hex_group.addWidget(self.hex_edit)
        hex_group.addStretch(1)
        controls_layout.addLayout(hex_group)

        controls_wrapper = QHBoxLayout()
        controls_wrapper.addStretch(1)
        self.mode_editor = ColorModeEditor(controls_container, self)
        self.mode_editor.colorSelected.connect(
            lambda descriptor: self._dispatch_descriptor(descriptor, commit=False)
        )
        controls_wrapper.addWidget(self.mode_editor)
        controls_wrapper.addStretch(1)
        main_layout.addLayout(controls_wrapper)

        palette_header = QHBoxLayout()
        palette_header.setContentsMargins(0, 0, 0, 0)
        palette_header.setSpacing(6)
        palette_text = self._ts("Palette")
        palette_label = QLabel(palette_text)
        self._translation_binder.register(palette_label.setText, palette_text)
        palette_header.addWidget(palette_label)
        palette_header.addStretch(1)
        palette_header.addWidget(self.add_swatch_button)
        main_layout.addLayout(palette_header)
        main_layout.addWidget(self.palette_view)

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: object) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _connect_signals(self) -> None:
        self.hex_edit.editingFinished.connect(self._on_hex_edited)
        self.add_swatch_button.clicked.connect(self._on_add_swatch)
        self.palette_view.swatchActivated.connect(self._on_swatch_activated)
        self.palette_view.swatchRemoved.connect(self._on_swatch_deleted)
        self.selector.hueChanged.connect(self._on_hue_changed)
        self.selector.svChanged.connect(self._on_sv_changed)
        for label, spin in self.hsv_inputs.items():
            spin.valueChanged.connect(lambda value, key=label: self._on_hsv_changed(key, value))
        for label, spin in self.rgb_inputs.items():
            spin.valueChanged.connect(lambda value, key=label: self._on_rgb_changed(key, value))

    def set_current_color(self, snapshot: ColorStateSnapshot) -> None:
        self._apply_descriptor(snapshot.current)

    def set_palettes(self, palettes: Sequence[ColorPalette]) -> None:
        self._source_palettes = list(palettes)
        self.palette_view.set_palettes(self.mode_editor.project_palettes(self._source_palettes))

    def set_cmyk_profile(self, profile: bytes | None, *, reset_mode: bool = False) -> None:
        if self.mode_editor.set_profile(profile, reset_mode=reset_mode):
            self.set_palettes(self._source_palettes)
            self._apply_descriptor(self._source_descriptor_value)

    def _on_hue_changed(self, hue: float) -> None:
        if self._updating:
            return
        self._hue = hue % 1.0
        color = QColor.fromHsvF(self._hue, self._saturation, self._value)
        descriptor = ColorDescriptor((color.redF(), color.greenF(), color.blueF(), 1.0))
        self._dispatch_descriptor(descriptor, commit=False)

    def _on_sv_changed(self, saturation: float, value: float) -> None:
        if self._updating:
            return
        self._saturation = saturation
        self._value = value
        color = QColor.fromHsvF(self._hue, saturation, value)
        descriptor = ColorDescriptor((color.redF(), color.greenF(), color.blueF(), 1.0))
        self._dispatch_descriptor(descriptor, commit=False)

    def _on_hsv_changed(self, channel: str, value: int) -> None:
        if self._updating:
            return
        if channel == "H":
            self._hue = (value % 360) / 360.0
        elif channel == "S":
            self._saturation = max(0.0, min(1.0, value / 100.0))
        elif channel == "V":
            self._value = max(0.0, min(1.0, value / 100.0))
        color = QColor.fromHsvF(self._hue, self._saturation, self._value)
        descriptor = ColorDescriptor((color.redF(), color.greenF(), color.blueF(), 1.0))
        self._dispatch_descriptor(descriptor, commit=False)

    def _on_rgb_changed(self, channel: str, value: int) -> None:
        if self._updating:
            return
        r = self.rgb_inputs["R"].value() / 255.0
        g = self.rgb_inputs["G"].value() / 255.0
        b = self.rgb_inputs["B"].value() / 255.0
        descriptor = ColorDescriptor((r, g, b, 1.0))
        self._dispatch_descriptor(descriptor, commit=False)

    def _on_hex_edited(self) -> None:
        if self._updating:
            return
        text = self.hex_edit.text().strip()
        if not text:
            return
        if not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        if not color.isValid():
            self._apply_descriptor(self._current_descriptor_value)
            return
        descriptor = ColorDescriptor((color.redF(), color.greenF(), color.blueF(), 1.0))
        self._dispatch_descriptor(descriptor, commit=True)

    def _on_add_swatch(self) -> None:
        self.paletteRequested.emit("add", self._current_descriptor_value)
        self.colorCommitted.emit(self._current_descriptor_value)

    def _on_swatch_activated(self, descriptor: ColorDescriptor) -> None:
        self._dispatch_descriptor(descriptor, commit=True)

    def _on_swatch_deleted(self, swatch_id: str) -> None:
        self.paletteSwatchRemoved.emit(swatch_id)

    def _dispatch_descriptor(self, descriptor: ColorDescriptor, *, commit: bool) -> None:
        descriptor = self.mode_editor.adapt(descriptor)
        self._apply_descriptor(descriptor)
        self.colorSelected.emit(descriptor)
        if commit:
            self.colorCommitted.emit(descriptor)

    def _apply_descriptor(self, descriptor: ColorDescriptor) -> None:
        self._updating = True
        self._source_descriptor_value = descriptor
        descriptor = self.mode_editor.adapt(descriptor)
        self._current_descriptor_value = descriptor
        self.mode_editor.set_descriptor(descriptor)
        color = QColor.fromRgbF(*descriptor.rgba)
        hue = color.hsvHueF()
        if hue < 0:
            hue = self._hue
        self._hue = hue if hue >= 0 else 0.0
        self._saturation = color.hsvSaturationF()
        self._value = color.valueF()
        self.selector.set_hsv(self._hue, self._saturation, self._value)
        self._set_hsv_inputs(self._hue, self._saturation, self._value)
        self._set_rgb_inputs(color)
        self._set_hex_text(descriptor)
        self._updating = False

    def _set_hsv_inputs(self, hue: float, saturation: float, value: float) -> None:
        self._set_spin_value(self.hsv_inputs["H"], round((hue % 1.0) * 360))
        self._set_spin_value(self.hsv_inputs["S"], round(saturation * 100))
        self._set_spin_value(self.hsv_inputs["V"], round(value * 100))

    def _set_rgb_inputs(self, color: QColor) -> None:
        self._set_spin_value(self.rgb_inputs["R"], color.red())
        self._set_spin_value(self.rgb_inputs["G"], color.green())
        self._set_spin_value(self.rgb_inputs["B"], color.blue())

    def _set_hex_text(self, descriptor: ColorDescriptor) -> None:
        r, g, b, _ = descriptor.rgba
        text = f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"
        if self.hex_edit.text().upper() != text:
            self.hex_edit.blockSignals(True)
            self.hex_edit.setText(text)
            self.hex_edit.blockSignals(False)

    @staticmethod
    def _set_spin_value(spin: QSpinBox, value: int) -> None:
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    @staticmethod
    def _spin_width_for_chars(spin: QSpinBox, chars: int, suffix: str = "") -> int:
        metrics = QFontMetrics(spin.font())
        width = metrics.horizontalAdvance("0" * chars)
        if suffix:
            width += metrics.horizontalAdvance(suffix)
        return width + 16

    def _current_descriptor(self) -> ColorDescriptor:
        return self._current_descriptor_value


__all__ = [
    "ColorPanel",
    "ColorSelectorWidget",
    "ColorWheelWidget",
    "PaletteGridView",
    "SwatchButton",
    "SwatchTile",
    "ToneTriangleWidget",
]
