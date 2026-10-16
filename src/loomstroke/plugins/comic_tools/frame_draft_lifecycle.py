"""Discard detached comic drafts when their document or page changes."""

from loomstroke.core.events import Events


class FrameDraftLifecycle:
    def __init__(self, workflow):
        self.workflow = workflow
        self.bus = getattr(workflow.tool.host_api, "events", None)
        self.tokens = []
        self.on_discard = None
        self.handler = self.changed  # EventBus holds weak references.
        subscribe = getattr(self.bus, "on", None) or getattr(self.bus, "subscribe", None)
        if callable(subscribe):
            for event in (Events.ACTIVE_PAGE_CHANGED, Events.DOC_CHANGED):
                self.tokens.append((event, subscribe(event, self.handler)))

    def changed(self, _payload=None):
        if self.workflow.draft is not None:
            self.workflow.cancel()
            if self.on_discard is not None:
                self.on_discard()
            self.workflow.tool._notify_panel()

    def dispose(self):
        self.workflow.cancel()
        unsubscribe = getattr(self.bus, "off", None) or getattr(self.bus, "unsubscribe", None)
        if callable(unsubscribe):
            for event, token in self.tokens:
                unsubscribe(event, token)
        self.tokens.clear()
        self.on_discard = None
