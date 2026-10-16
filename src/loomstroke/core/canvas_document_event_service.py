"""Document event dispatch helpers for SimpleCanvasService."""

from __future__ import annotations

from typing import Any

from .events import DocumentChangeEvent, Events


class CanvasDocumentEventService:
    """Keep document event dispatch rules out of the canvas facade."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def emit_doc_changed(self, payload: DocumentChangeEvent) -> None:
        if getattr(self._owner, "_suppress_doc_events", 0):
            return
        event_bus = getattr(self._owner, "_event_bus", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(Events.DOC_CHANGED, payload)
            return
        emit = getattr(event_bus, "emit", None)
        if callable(emit):
            emit(Events.DOC_CHANGED, payload)
        self._owner._emit_history_changed()
