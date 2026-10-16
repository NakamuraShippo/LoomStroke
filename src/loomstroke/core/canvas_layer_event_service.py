"""Layer event publishing helpers for SimpleCanvasService."""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QRect

from loomstroke.core.event_bus import get_event_bus
from loomstroke.host_api import LayerRef

from .events import DocumentChangeEvent, Events, LayerChangeEvent


class CanvasLayerEventService:
    """Keep layer event payload construction out of the canvas facade."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def publish_layer_event(
        self,
        event_name: Any,
        *,
        layer: Any,
        change_type: str,
        old_value: Any = None,
        new_value: Any = None,
    ) -> None:
        """Publish a layer change event on the owner and fallback event buses."""
        pending = getattr(self._owner, "_panel_edit_events", None)
        if pending is not None:
            pending.append(
                (
                    event_name,
                    {
                        "layer": layer,
                        "change_type": change_type,
                        "old_value": old_value,
                        "new_value": new_value,
                    },
                )
            )
            return
        owner_bus = getattr(self._owner, "_event_bus", None)
        buses: list[Any] = []
        if owner_bus:
            buses.append(owner_bus)
        fallback_bus = get_event_bus()
        if fallback_bus is not owner_bus:
            buses.append(fallback_bus)
        if not buses:
            return

        payload = LayerChangeEvent(
            layer_id=layer.id,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
            source="canvas",
            timestamp=time.time(),
        )

        for bus in buses:
            publish = getattr(bus, "publish", None)
            if callable(publish):
                publish(event_name, payload)
                continue
            emit = getattr(bus, "emit", None)
            if callable(emit):
                emit(event_name, payload)

    def emit_layer_selected(self, previous: Any | None, current: Any) -> None:
        """Publish the active layer selection event."""
        self._owner._publish_layer_event(
            Events.LAYER_SELECTED,
            layer=current,
            change_type="selected",
            old_value=LayerRef(id=previous.id, name=previous.name) if previous else None,
            new_value=LayerRef(id=current.id, name=current.name),
        )

    def emit_layer_content_changed(
        self,
        layer: Any,
        rect: QRect,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event_bus = getattr(self._owner, "_event_bus", None)
        if not event_bus:
            return
        if getattr(self._owner, "_in_transaction", False):
            return
        timestamp = time.time()
        layer_payload = LayerChangeEvent(
            layer_id=layer.id,
            change_type="content",
            old_value=None,
            new_value={
                "rect": (rect.x(), rect.y(), rect.width(), rect.height()),
                "metadata": metadata or {},
            },
            source="canvas",
            timestamp=timestamp,
        )
        doc_payload = DocumentChangeEvent(
            change_type="apply_image",
            affected_area=(rect.x(), rect.y(), rect.width(), rect.height()),
            layer_id=layer.id,
            source="canvas",
            timestamp=timestamp,
        )
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(Events.LAYER_CHANGED, layer_payload)
            self._owner._emit_doc_changed(doc_payload)
            return
        emit = getattr(event_bus, "emit", None)
        if callable(emit):
            emit(Events.LAYER_CHANGED, layer_payload)
            self._owner._emit_doc_changed(doc_payload)
