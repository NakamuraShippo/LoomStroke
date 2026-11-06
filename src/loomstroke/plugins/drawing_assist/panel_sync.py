"""Refresh drawing-assist UI after page/document/history changes, with bounded work."""

from PySide6.QtCore import QObject, QTimer
from shiboken6 import isValid

from loomstroke.core.events import Events


class DrawingAssistPanelSync(QObject):
    def __init__(self, panel, controller, events):
        super().__init__(panel)
        self._panel, self._controller, self._events = panel, controller, events
        self._tokens = []
        self._handler = self._request_refresh  # EventBus keeps weak references.
        self._boundary_handler = self._boundary_refresh
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)
        subscribe = getattr(events, "on", None)
        if callable(subscribe):
            for event in (
                Events.PAGES_CHANGED,
                Events.ACTIVE_PAGE_CHANGED,
                Events.HISTORY_CHANGED,
                Events.DOC_LOADED,
                Events.DOC_CREATED,
                Events.DOC_CLOSED,
                Events.TOOL_CHANGED,
            ):
                handler = (
                    self._handler
                    if event in (Events.PAGES_CHANGED, Events.HISTORY_CHANGED)
                    else self._boundary_handler
                )
                self._tokens.append((event, subscribe(event, handler)))
        panel.destroyed.connect(self.close)

    def _request_refresh(self, _payload):
        self._controller.handles.cancel()
        if not self._timer.isActive():
            self._timer.start(0)

    def _boundary_refresh(self, payload):
        self._controller.clear_stroke_feedback()
        self._request_refresh(payload)

    def _refresh(self):
        state = (self._controller.page_id(), self._controller.state())
        if state != self._panel._displayed_state:
            self._controller.clear_stroke_feedback()
            self._panel.refresh()
            self._controller._repaint()

    def close(self, *_args):
        if isValid(self._timer):
            self._timer.stop()
        unsubscribe = getattr(self._events, "off", None)
        if callable(unsubscribe):
            for event, token in self._tokens:
                unsubscribe(event, token)
        self._tokens.clear()
