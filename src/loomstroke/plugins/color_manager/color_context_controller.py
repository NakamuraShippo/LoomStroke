"""Follow document/page color contexts without work on each stroke event."""

from functools import partial

from PySide6.QtCore import QObject, QTimer, Signal

from loomstroke.core.events import Events
from loomstroke.core.native_cmyk.color_descriptor import set_canvas_foreground


class ColorContextController(QObject):
    refreshRequested = Signal()

    def __init__(self, panel, host):
        super().__init__(panel)
        self._panel = panel
        self._host = host
        self._context = None
        self._handlers = []
        self._refresh_color = True
        self._reset_tab = False
        self._subscriptions = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        self.refreshRequested.connect(self._queue_refresh)
        bus = getattr(host, "events", None)
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if callable(subscribe):
            for event in (
                Events.DOC_CREATED,
                Events.DOC_LOADED,
                Events.DOC_CLOSED,
                Events.DOC_CHANGED,
                Events.ACTIVE_PAGE_CHANGED,
                Events.LAYER_ADDED,
                Events.LAYER_REMOVED,
                Events.LAYER_CHANGED,
            ):
                handler = partial(self._on_event, event)
                self._handlers.append(handler)
                self._subscriptions.append((bus, event, subscribe(event, handler)))
        panel.destroyed.connect(self.dispose)
        self.refresh()

    def _on_event(self, event, payload):
        change_type = getattr(payload, "change_type", None)
        if event == Events.LAYER_CHANGED:
            value = getattr(payload, "new_value", None)
            metadata = value.get("metadata", {}) if isinstance(value, dict) else {}
            if metadata.get("command") != "convert_native_cmyk":
                return
        if event == Events.DOC_CHANGED and change_type not in (None, "document.color_mode"):
            return
        if event in (Events.DOC_CREATED, Events.DOC_LOADED, Events.ACTIVE_PAGE_CHANGED):
            self._refresh_color = True
        if event in (Events.DOC_CREATED, Events.DOC_LOADED):
            self._reset_tab = True
        self.refreshRequested.emit()

    def _queue_refresh(self):
        if not self._timer.isActive():
            self._timer.start(0)

    def refresh(self):
        canvas = getattr(self._host, "canvas", None)
        getter = getattr(canvas, "native_cmyk", None)
        profile = getter().profile() if callable(getter) else None
        context = (id(canvas), profile)
        if context != self._context or self._refresh_color:
            self._context = context
            self._refresh_color = False
            snapshot = self._host.get_color_snapshot()
            set_canvas_foreground(canvas, snapshot.current)
            self._panel.set_cmyk_profile(profile, reset_mode=self._reset_tab)
            self._reset_tab = False
            self._panel.set_current_color(snapshot)

    def dispose(self):
        self._timer.stop()
        for bus, event, token in self._subscriptions:
            off = getattr(bus, "off", None) or getattr(bus, "unsubscribe", None)
            if callable(token):
                token()
            elif callable(off):
                off(event, token)
        self._subscriptions.clear()
        self._handlers.clear()
