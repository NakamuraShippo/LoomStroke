"""Screen-space drawing-return hits, independent of the active drawing tool."""

import logging

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QKeySequence, QPainter, QRegion, QShortcut
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from loomstroke.core.comic_panels.drawing_state import drawing_states, panel_input_busy
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.panel_gate import availability
from loomstroke.core.events import Events

from .frame_controls import ts
from .frame_view_geometry import panel_view_rects

logger = logging.getLogger(__name__)


class FrameGuides(QWidget):
    def __init__(self, controls, parent):
        super().__init__(parent)
        self.controls = controls
        self.workflow = controls.workflow
        self.canvas_widget = getattr(parent, "legacy_canvas", parent)
        self._buttons = {}
        self._preedit = False
        self._tokens = []
        self._handlers = []
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self.refresh)
        self._closed = False
        self._native_mode = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        parent.installEventFilter(self)
        self._root = parent.window()
        if self._root is not parent:
            self._root.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        signal = getattr(parent, "viewport_changed", None)
        if signal:
            signal.connect(self.schedule_refresh)
        bus = self.workflow.tool.host_api.events
        self._bus = bus
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if callable(subscribe):
            for name, handler in (
                (Events.DOC_CHANGED, self.schedule_refresh),
                (Events.HISTORY_CHANGED, self.schedule_refresh),
                (Events.LAYER_SELECTED, self.layer_selected),
            ):
                self._handlers.append(handler)
                self._tokens.append((name, subscribe(name, handler)))
        shortcuts = getattr(self.workflow.tool.host_api, "shortcuts", None)
        if shortcuts:
            sequence = shortcuts.register_action(
                "comic.draw_here",
                label="Draw here",
                category="Comic",
                owner="loomstroke.comic_tools",
            )
            self._shortcut = QShortcut(QKeySequence(sequence or ""), parent)
            self._shortcut.activated.connect(self.draw_at_cursor)
        self.refresh()

    def schedule_refresh(self, *args):
        if not self._closed and not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def layer_selected(self, *args):
        canvas = self.workflow.tool._canvas()
        target = drawing_states(canvas).get(canvas.current_layer().id)
        if (
            target
            and target.drawable
            and target.visible
            and not target.locked
            and not target.mask_editing
        ):
            try:
                self.workflow.targets.remember(self.workflow.page_id(), target.panel_id, target)
            except PanelFrameError as exc:
                logger.debug("Drawing target not recorded without an active page: %s", exc)
        self.schedule_refresh()

    def draw(self, panel_id):
        self.controls.run(lambda: self.workflow.draw_here(panel_id, input_busy=self._preedit))

    def draw_at_cursor(self):
        pos = self.canvas_widget.mapFromGlobal(QCursor.pos())
        point = self.canvas_widget.viewport.widget_to_canvas(pos.x(), pos.y())
        panel_id = self.workflow.tool.controller.panel_at(self.workflow.state(), point)
        if panel_id:
            self.draw(panel_id)

    def refresh(self):
        if self._closed:
            return
        self._sync_surface()
        try:
            state = self.workflow.state()
        except PanelFrameError:
            self.hide()
            return
        self.controls.refresh_edges()
        query = self.workflow.tool._canvas()._layer_tree()
        ids = set()
        for pid, record in state.panel_records.items():
            frame = read_frame_layer(record.metadata)
            if frame and availability(query, query.find_layer_by_id_tree(frame.panel_group_id))[0]:
                ids.add(pid)
        for panel_id in set(self._buttons) - ids:
            self._buttons.pop(panel_id).deleteLater()
        mask = QRegion()
        origin = self.canvas_widget.mapTo(self.parentWidget(), QPoint(0, 0))
        rects = panel_view_rects(self.workflow, state)
        for panel_id in ids:
            button = self._buttons.get(panel_id)
            if button is None:
                button = QPushButton(self)
                button.clicked.connect(lambda checked=False, pid=panel_id: self.draw(pid))
                self._buttons[panel_id] = button
            button.setText(ts("Draw here"))
            button.setEnabled(self.workflow.draft is None)
            button.setAccessibleName(f"{panel_id}: {ts('Draw here')}")
            x, y = self.canvas_widget.viewport.canvas_to_widget(*rects[panel_id][:2])
            button.setGeometry(round(x) + origin.x() + 4, round(y) + origin.y() + 4, 86, 24)
            button.show()
            mask |= QRegion(button.geometry())
        self.setMask(mask)
        self.setVisible(bool(ids) and self.parentWidget().isVisible())
        self.raise_()

    def _sync_surface(self):
        parent = self.parentWidget()
        native = bool(getattr(parent, "native_surface_active", False))
        if native != self._native_mode:
            flags = Qt.WindowType.Widget
            if native:
                flags = (
                    Qt.WindowType.Tool
                    | Qt.WindowType.FramelessWindowHint
                    | Qt.WindowType.NoDropShadowWindowHint
                )
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, native)
            self._native_mode = native
        rect = parent.rect()
        if native:
            rect = QRect(parent.mapToGlobal(QPoint()), parent.size())
        self.setGeometry(rect)

    def paintEvent(self, event):
        if self.isWindow():
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.end()
        super().paintEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and self.image_double_click(obj, event):
            return True
        if event.type() == QEvent.Type.InputMethod:
            self._preedit = bool(event.preeditString())
        if obj in (self.parentWidget(), self._root) and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
            QEvent.Type.WindowStateChange,
        }:
            self.schedule_refresh()
        if event.type() == QEvent.Type.LanguageChange:
            self.schedule_refresh()
        return False

    def image_double_click(self, obj, event):
        surfaces = (
            self.canvas_widget,
            self.parentWidget(),
            getattr(self.parentWidget(), "_native_surface", None),
            getattr(self.parentWidget(), "_overlay_surface", None),
        )
        active_id = getattr(self.canvas_widget, "_active_tool_id", lambda: None)
        if (
            obj not in surfaces
            or event.button() != Qt.MouseButton.LeftButton
            or event.modifiers() != Qt.KeyboardModifier.NoModifier
            or active_id() != "comic_tool"
            or self._preedit
            or panel_input_busy(self.workflow.tool._canvas())
        ):
            return False
        from .panel_edit_gate import require_editable_parts  # noqa: PLC0415
        from .panel_image_hit import imported_image_at  # noqa: PLC0415

        pos = self.canvas_widget.mapFromGlobal(event.globalPosition().toPoint())
        point = self.canvas_widget.viewport.widget_to_canvas(pos.x(), pos.y())
        try:
            state = self.workflow.state()
        except PanelFrameError as exc:
            logger.debug("Image composition unavailable: %s", exc)
            return False
        panel_id = self.workflow.tool.controller.panel_at(state, point)
        item_id = imported_image_at(
            state, panel_id, point, page_size=self.workflow.tool._page_size()
        )
        if item_id is None:
            return False

        def adjust():
            require_editable_parts(state, (item_id,))
            self.controls.repairs.adjust_image(item_id)

        self.controls.run(adjust)
        return True

    def dispose(self):
        if self._closed:
            return
        self._closed = True
        self._refresh_timer.stop()
        QApplication.instance().removeEventFilter(self)
        self.parentWidget().removeEventFilter(self)
        if self._root is not self.parentWidget():
            self._root.removeEventFilter(self)
        off = getattr(self._bus, "off", None) or getattr(self._bus, "unsubscribe", None)
        if callable(off):
            for name, token in self._tokens:
                off(name, token)
        self._tokens.clear()
        self._handlers.clear()
        self.hide()
        self.deleteLater()
