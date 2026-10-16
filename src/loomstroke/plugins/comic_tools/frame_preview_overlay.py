"""Non-document boundary and hidden-content outlines; never included in export."""

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class FramePreviewOverlay(QWidget):
    def __init__(self, workflow, parent, controls=None):
        super().__init__(parent)
        self.workflow = workflow
        from .frame_ghost_painter import FrameGhostPainter  # noqa: PLC0415

        self.ghosts = FrameGhostPainter()
        self.canvas_widget = getattr(parent, "legacy_canvas", parent)
        self.gesture = None
        if controls is not None:
            from .frame_composition_gesture import FrameCompositionGesture  # noqa: PLC0415

            self.gesture = FrameCompositionGesture(self, controls)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._surface_mode = None
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self.refresh)
        parent.installEventFilter(self)
        self._root = parent.window()
        if self._root is not parent:
            self._root.installEventFilter(self)
        signal = getattr(parent, "viewport_changed", None)
        if signal:
            signal.connect(self.refresh)

    def refresh(self, *args):
        if not self.workflow.repair:
            self.ghosts.clear()
            if self.gesture and self.gesture.enabled:
                self.gesture.enable(False)
        self._sync_surface()
        self.setVisible(
            bool(self.workflow.repair or self.workflow.split or self.workflow.template)
            and self.parentWidget().isVisible()
        )
        self.raise_()
        self.update()

    def _sync_surface(self):
        parent = self.parentWidget()
        native = bool(getattr(parent, "native_surface_active", False))
        input_enabled = bool(self.gesture and self.gesture.enabled)
        mode = native, input_enabled
        if mode != self._surface_mode:
            # An alien transparent child copies the parent's Qt backing store,
            # which does not contain the wgpu surface. Use independent alpha
            # composition, as the host's native tool overlay does.
            flags = Qt.WindowType.Widget
            if native:
                flags = (
                    Qt.WindowType.Tool
                    | Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.NoDropShadowWindowHint
                )
                if not input_enabled:
                    flags |= Qt.WindowType.WindowTransparentForInput
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not input_enabled)
            self._surface_mode = mode
        rect = parent.rect()
        if native:
            rect = QRect(parent.mapToGlobal(QPoint()), parent.size())
        self.setGeometry(rect)

    def set_input_enabled(self, enabled):
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.refresh()

    def canvas_origin(self):
        return self.mapFromGlobal(self.canvas_widget.mapToGlobal(QPoint()))

    def eventFilter(self, watched, event):
        if watched in (self.parentWidget(), self._root) and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.WindowStateChange,
        }:
            self._geometry_timer.start(0)
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        preview = self.workflow.repair or self.workflow.split or self.workflow.template
        if preview is None:
            return
        painter = QPainter(self)
        if self.isWindow():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor(40, 160, 255, 220), 1.5, Qt.PenStyle.DashLine))
        viewport = self.canvas_widget.viewport
        origin = self.canvas_origin()

        def polygon(points):
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(*viewport.canvas_to_widget(*point)) + QPointF(origin)
                        for point in points
                    ]
                )
            )

        def rectangle(rect):
            x, y, w, h = rect
            polygon(((x, y), (x + w, y), (x + w, y + h), (x, y + h)))

        rects = (
            preview.rects.values()
            if self.workflow.repair
            else (child.rect for child in preview.children)
        )
        if getattr(preview, "polygons", None):
            for shape in preview.polygons.values():
                polygon(shape.points)
        else:
            for rect in rects:
                rectangle(rect)
        if self.workflow.repair:
            selected = self.gesture.selected() if self.gesture and self.gesture.enabled else None
            painter.setPen(QPen(QColor(240, 130, 45, 220), 1.5, Qt.PenStyle.DotLine))
            for item in preview.diagnostics():
                unit = preview.items[item.item_id][1]
                if item.show_ghost and unit.bounds.bounds and item.item_id != selected:
                    self.ghosts.paint(painter, preview, item.item_id, viewport, origin)
                    rectangle(unit.bounds.bounds)
            if selected:
                if any(
                    item.item_id == selected and item.show_ghost for item in preview.diagnostics()
                ):
                    self.ghosts.paint(painter, preview, selected, viewport, origin)
                painter.setPen(QPen(QColor(40, 160, 255, 240), 1.5))
                painter.drawRect(self.gesture.widget_rect(selected))
                handle = self.gesture.handle_rect(selected)
                if not handle.isEmpty():
                    painter.fillRect(handle, QColor(40, 160, 255, 240))
        painter.end()

    def mousePressEvent(self, event):
        if not self.gesture or not self.gesture.press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.gesture or not self.gesture.move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.gesture or not self.gesture.release(event):
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.gesture and self.gesture.enabled and event.key() == Qt.Key.Key_Escape:
            self.workflow.cancel()
            self.gesture.controls.refresh()
            event.accept()
        else:
            super().keyPressEvent(event)
