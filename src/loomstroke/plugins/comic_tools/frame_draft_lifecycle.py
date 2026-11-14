"""Discard pending comic edits when their document or page changes."""

from loomstroke.core.events import Events
from loomstroke.core.history_failure_reporting import history_failure_event_is_current


class FrameDraftLifecycle:
    def __init__(self, workflow):
        self.workflow = workflow
        self.bus = None
        self.tokens = []
        self.on_discard = None
        self.handler = self.changed  # EventBus holds weak references.
        self.bind()

    def bind(self):
        bus = getattr(self.workflow.tool.host_api, "events", None)
        if self.bus is bus and self.tokens:
            return
        self._unsubscribe()
        self.bus = bus
        subscribe = getattr(self.bus, "on", None) or getattr(self.bus, "subscribe", None)
        if callable(subscribe):
            for event in (
                Events.ACTIVE_PAGE_CHANGED,
                Events.DOC_CHANGED,
                Events.DOC_LOADED,
                Events.DOC_CREATED,
                Events.DOC_CLOSED,
            ):
                self.tokens.append((event, subscribe(event, self.handler)))

    def changed(self, _payload=None):
        if not history_failure_event_is_current(_payload):
            return
        tool = self.workflow.tool
        if getattr(tool, "_drag", None) is not None:
            tool._drag = None
            tool._refresh_canvas()
        if self.workflow.draft is not None:
            self.workflow.cancel()
            if self.on_discard is not None:
                self.on_discard()
            self.workflow.tool._notify_panel()

    def dispose(self):
        self.workflow.cancel()
        self._unsubscribe()
        self.on_discard = None

    def _unsubscribe(self):
        unsubscribe = getattr(self.bus, "off", None) or getattr(self.bus, "unsubscribe", None)
        if callable(unsubscribe):
            for event, token in self.tokens:
                unsubscribe(event, token)
        self.tokens.clear()
        self.bus = None
