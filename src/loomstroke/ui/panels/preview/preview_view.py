"""
Preview view component - handles UI rendering and user interaction.

Part of the View/Presenter pattern split for preview panel.
Per Master.md A3 specifications.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.typing import Signal0, Signal1, Signal2

from ....core.event_bus_extensions import get_extended_event_bus
from ....core.events import Events
from ...translations import TranslationBinder

logger = logging.getLogger(__name__)


class PreviewGraphicsView(QGraphicsView):
    """Custom graphics view for image preview with zoom support."""

    zoom_changed = Signal(float)
    pan_requested = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.zoom_factor = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 5.0
        self._dragging = False
        self._last_drag_position = QPointF()

        # Configure view
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Set background
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Handle wheel events for zooming."""
        # Preview should remain fixed and always fit to panel size.
        event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handle left button drag start."""
        scene = self.scene()
        if (
            event.button() == Qt.MouseButton.LeftButton
            and scene is not None
            and len(scene.items()) > 0
        ):
            self._dragging = True
            self._last_drag_position = self.mapToScene(event.pos())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handle drag movement."""
        if not self._dragging:
            super().mouseMoveEvent(event)
            return

        current_pos = self.mapToScene(event.pos())
        delta = current_pos - self._last_drag_position
        if delta.x() != 0 or delta.y() != 0:
            self.pan_requested.emit(float(delta.x()), float(delta.y()))
        self._last_drag_position = current_pos
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Handle left button drag end."""
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._last_drag_position = QPointF()
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def fit_in_view(self, emit_signal: bool = True) -> None:
        """Fit the scene in view."""
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        # Calculate actual zoom factor
        transform = self.transform()
        self.zoom_factor = transform.m11()
        if emit_signal:
            self.zoom_changed.emit(self.zoom_factor)

    def reset_zoom(self, emit_signal: bool = False) -> None:
        """Reset zoom to 100%."""
        self.resetTransform()
        self.zoom_factor = 1.0
        if emit_signal:
            self.zoom_changed.emit(self.zoom_factor)


class PreviewView(QWidget):
    """
    View component for the preview panel.

    Handles UI rendering and user interactions only.
    Business logic is delegated to the presenter.
    """

    # User interaction signals
    zoom_value_changed = Signal(float)
    pan_requested = Signal(float, float)
    fit_requested = Signal()
    reset_zoom_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        """Initialize the preview view."""
        super().__init__(parent)

        self.pixmap_item: QGraphicsPixmapItem | None = None
        self._viewport_rect_item: QGraphicsRectItem | None = None
        self._event_bus = get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._no_image_text = self._ts("No image")
        self._image_size_template = self._ts("{width}x{height}")
        self._translation_binder.register(self._set_no_image_text, self._no_image_text)
        self._translation_binder.register(self._set_image_size_template, self._image_size_template)

        self._setup_ui()
        self._connect_signals()
        self._subscribe_events()

    def _setup_ui(self) -> None:
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Hidden info label (referenced by display_image / clear_display)
        self.info_label = QLabel(self._no_image_text)
        self.info_label.setVisible(False)

        self._setup_graphics_view(layout)
        self._setup_zoom_controls(layout)

    def _setup_graphics_view(self, layout: QVBoxLayout) -> None:
        """Set up scene and graphics view."""
        self.scene = QGraphicsScene()
        self.graphics_view = PreviewGraphicsView()
        self.graphics_view.setScene(self.scene)
        layout.addWidget(self.graphics_view)

    def _setup_zoom_controls(self, layout: QVBoxLayout) -> None:
        """Set up zoom controls."""
        # Zoom slider row: [Zoom:] [slider] [100%]
        zoom_layout = QHBoxLayout()
        zoom_text = self._ts("Zoom:")
        zoom_label = QLabel(zoom_text)
        self._translation_binder.register(zoom_label.setText, zoom_text)
        zoom_layout.addWidget(zoom_label)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(10, 500)  # 10% to 500%
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.zoom_slider.setTickInterval(50)
        zoom_layout.addWidget(self.zoom_slider)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(50)
        zoom_layout.addWidget(self.zoom_label)
        layout.addLayout(zoom_layout)

        # Button row: [Fit] [100%]
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)

        fit_text = self._ts("Fit")
        self.fit_btn = QPushButton(fit_text)
        self._translation_binder.register(self.fit_btn.setText, fit_text)
        btn_layout.addWidget(self.fit_btn)

        self.reset_zoom_btn = QPushButton("100%")
        btn_layout.addWidget(self.reset_zoom_btn)

        layout.addLayout(btn_layout)

    def _connect_signals(self) -> None:
        """Connect internal widget signals."""
        # Graphics view signals
        self.graphics_view.zoom_changed.connect(self._on_view_zoom_changed)
        self.graphics_view.pan_requested.connect(self._on_view_pan_requested)

        # Zoom controls
        self.zoom_slider.valueChanged.connect(self._on_slider_changed)
        self.fit_btn.clicked.connect(self.fit_requested.emit)
        self.reset_zoom_btn.clicked.connect(self.reset_zoom_requested.emit)

    def _subscribe_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        self._event_tokens.append(self._event_bus.on(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: object) -> None:
        self.refresh_translations()

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        if self.pixmap_item is None:
            self.info_label.setText(self._no_image_text)

    def _set_no_image_text(self, text: str) -> None:
        self._no_image_text = text

    def _set_image_size_template(self, text: str) -> None:
        self._image_size_template = text

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    @Slot(float)
    def _on_view_zoom_changed(self, zoom: float) -> None:
        """Handle zoom change from graphics view."""
        # Preview is fixed to 100% fitting.
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(100)
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText("100%")

    @Slot(float, float)
    def _on_view_pan_requested(self, delta_x: float, delta_y: float) -> None:
        self.pan_requested.emit(delta_x, delta_y)

    @Slot(int)
    def _on_slider_changed(self, value: int) -> None:
        """Handle slider value change."""
        zoom = max(0.1, min(5.0, value / 100.0))
        self.zoom_label.setText(f"{int(zoom * 100)}%")
        self.zoom_value_changed.emit(zoom)

    def display_image(self, pixmap: QPixmap, width: int, height: int) -> None:
        """
        Display an image in the view.

        Args:
            pixmap: QPixmap to display
            width: Image width
            height: Image height
        """
        # Clear scene
        self.scene.clear()
        self._viewport_rect_item = None

        # Add pixmap
        self.pixmap_item = self.scene.addPixmap(pixmap)
        if self.pixmap_item is not None:
            self.pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene.setSceneRect(QRectF(pixmap.rect()))

        # Update info
        self.info_label.setText(self._image_size_template.format(width=width, height=height))
        self.set_viewport_rect(None)

        # Keep preview fixed to panel size at all times.
        if self.pixmap_item is not None:
            self.graphics_view.fit_in_view(emit_signal=False)

    def resizeEvent(self, event: Any) -> None:
        """Keep preview scaled to the viewport on panel resize."""
        super().resizeEvent(event)
        if self.pixmap_item is not None:
            self.graphics_view.fit_in_view(emit_signal=False)

    def clear_display(self) -> None:
        """Clear the image display."""
        self.scene.clear()
        self._viewport_rect_item = None
        self.pixmap_item = None
        self.info_label.setText(self._no_image_text)
        self.set_viewport_rect(None)

    def fit_to_view(self) -> None:
        """Fit image to view."""
        if self.pixmap_item:
            self.graphics_view.fit_in_view(emit_signal=False)

    def reset_zoom(self) -> None:
        """Reset zoom to 100%."""
        self.graphics_view.reset_zoom()

    def update_info_text(self, text: str) -> None:
        """
        Update the info label text.

        Args:
            text: Text to display
        """
        self.info_label.setText(text)

    def set_zoom_value(self, zoom: float) -> None:
        """
        Set the zoom value programmatically.

        Args:
            zoom: Zoom factor (1.0 = 100%)
        """
        clamped_zoom = max(0.1, min(5.0, float(zoom)))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(clamped_zoom * 100))
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{round(clamped_zoom * 100)}%")

    def set_viewport_rect(self, rect: QRectF | None) -> None:
        """Set mini-map-like viewport rectangle."""
        if rect is None or rect.isEmpty():
            if self._viewport_rect_item is not None:
                self._viewport_rect_item.setVisible(False)
            return

        if self._viewport_rect_item is None:
            pen = QPen(QColor(0, 120, 255, 220))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.0)
            brush = QBrush(QColor(0, 120, 255, 36))
            self._viewport_rect_item = self.scene.addRect(rect, pen, brush)
            self._viewport_rect_item.setZValue(10)
            self._viewport_rect_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._viewport_rect_item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,
                False,
            )
            self._viewport_rect_item.setAcceptHoverEvents(False)
        else:
            self._viewport_rect_item.setRect(rect)
            self._viewport_rect_item.setVisible(True)


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    PreviewGraphicsView.zoom_changed = cast(  # type: ignore[assignment]
        Signal1[float], PreviewGraphicsView.zoom_changed
    )
    PreviewGraphicsView.pan_requested = cast(  # type: ignore[assignment]
        Signal2[float, float], PreviewGraphicsView.pan_requested
    )
    PreviewView.zoom_value_changed = cast(  # type: ignore[assignment]
        Signal1[float], PreviewView.zoom_value_changed
    )
    PreviewView.pan_requested = cast(  # type: ignore[assignment]
        Signal2[float, float], PreviewView.pan_requested
    )
    PreviewView.fit_requested = cast(Signal0, PreviewView.fit_requested)  # type: ignore[assignment]
    PreviewView.reset_zoom_requested = cast(  # type: ignore[assignment]
        Signal0, PreviewView.reset_zoom_requested
    )
