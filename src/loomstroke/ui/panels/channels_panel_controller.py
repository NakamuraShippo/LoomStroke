"""Bind channel panel actions to canvas services and coalesce event refreshes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from loomstroke.core.events import Events


class ChannelsPanelController(QObject):
    refresh_requested = Signal()
    language_changed = Signal()

    def __init__(self, panel: Any, host: Any = None, event_bus: Any = None) -> None:
        super().__init__(panel)
        self.panel = panel
        self.host = None
        self._subscriptions: list[tuple[Any, str, Any]] = []
        self._handler = self._on_event
        self._language_handler = self._on_language_event
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(50)
        self._timer.timeout.connect(panel.refresh)
        self.refresh_requested.connect(self._queue_refresh)
        self.language_changed.connect(self._refresh_language)
        self._event_bus = event_bus
        self.set_host(host)

    @property
    def service(self) -> Any:
        canvas = getattr(self.host, "canvas", None)
        getter = getattr(canvas, "channels", None)
        return getter() if callable(getter) else None

    def set_host(self, host: Any) -> None:
        self.dispose()
        self.host = host
        buses = [self._event_bus, getattr(host, "events", None)]
        seen: set[int] = set()
        for bus in buses:
            if bus is None or id(bus) in seen:
                continue
            seen.add(id(bus))
            subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
            if not callable(subscribe):
                continue
            for event in (
                Events.LAYER_CHANGED,
                Events.LAYER_ADDED,
                Events.LAYER_REMOVED,
                Events.LAYER_SELECTED,
                Events.DOC_CHANGED,
                Events.DOC_LOADED,
                Events.DOC_CREATED,
                Events.ACTIVE_PAGE_CHANGED,
                Events.LANGUAGE_CHANGED,
            ):
                handler = (
                    self._language_handler if event == Events.LANGUAGE_CHANGED else self._handler
                )
                self._subscriptions.append((bus, event, subscribe(event, handler)))

    def dispose(self) -> None:
        self._timer.stop()
        for bus, event, token in self._subscriptions:
            if callable(token):
                token()
                continue
            unsubscribe = getattr(bus, "off", None) or getattr(bus, "unsubscribe", None)
            if callable(unsubscribe):
                unsubscribe(event, token)
        self._subscriptions.clear()

    def _on_event(self, _payload: Any) -> None:
        self.refresh_requested.emit()

    def _queue_refresh(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _on_language_event(self, _payload: Any) -> None:
        self.language_changed.emit()

    def _refresh_language(self) -> None:
        self.panel._signature = None
        self.refresh_requested.emit()

    def run(self, action: Callable[[Any], Any], *, refresh: bool = True) -> Any:
        service = self.service
        if service is None:
            return None
        try:
            result = action(service)
        except ValueError as exc:
            self.panel.show_error(str(exc))
            return None
        if refresh:
            self.panel.refresh()
        else:
            self.refresh_requested.emit()
        return result
