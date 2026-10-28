"""Commit prepared Embed metadata before publishing any successful mutation."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from .commands_layer_lookup import find_layer
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent, Events
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .resource_embed_command import _EmbedResourceConsumersCommand, _resource_consumer_layers

logger = logging.getLogger(__name__)


class _EmbedScope(_LayerCommandContext):
    def emit_document_change(self) -> None:
        if self.current():
            self.canvas._emit_doc_changed(
                DocumentChangeEvent(change_type="resource_embed", source="canvas")
            )


class _ResourceEmbedRejected(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _capture_embed_scope(canvas: Any, resource_id: str) -> _EmbedScope:
    scope = _EmbedScope(canvas)
    scope.layers = _resource_consumer_layers(canvas, resource_id)
    return scope


def _prepare_resource_embed(canvas: Any, scope: _EmbedScope, record: Any, consumers: Any):
    if not scope.current():
        raise _ResourceEmbedRejected("resource_scope_changed")
    if any(layer.native_cmyk is not None for layer in canvas._layer_tree().flatten_layers()):
        raise _ResourceEmbedRejected("resource_native_unsupported")
    identifiers = tuple(consumer.layer_id for consumer in consumers)
    expected = {layer.id: layer for layer in scope.layers}
    live_consumers = _resource_consumer_layers(canvas, record.resource_id)
    if (
        len(identifiers) != len(set(identifiers))
        or set(identifiers) != set(expected)
        or len(live_consumers) != len(expected)
        or any(expected.get(layer.id) is not layer for layer in live_consumers)
    ):
        raise _ResourceEmbedRejected("resource_consumer_changed")
    layers = tuple(find_layer(canvas, identifier) for identifier in identifiers)
    if any(layer is None or layer.native_cmyk is not None for layer in layers):
        raise _ResourceEmbedRejected("resource_consumer_unavailable")
    codec = ResourceLayerMetadataCodec()
    if any(codec.read(layer.metadata) != record for layer in layers):
        raise _ResourceEmbedRejected("resource_consumer_changed")
    command = _EmbedResourceConsumersCommand(layers, record)
    if not scope.current() or not all(
        find_layer(canvas, layer.id) is layer
        and layer.metadata is before
        and codec.read(layer.metadata) == record
        for layer, before in zip(layers, command.before, strict=True)
    ):
        raise _ResourceEmbedRejected("resource_scope_changed")
    return command


def _rollback_embed_metadata(history, command, layers) -> bool:
    if not history.can_rollback(command) or not all(
        layer.metadata is after for layer, after in zip(layers, command.after, strict=True)
    ):
        return False
    for layer, before in zip(layers, command.before, strict=True):
        layer.metadata = before
    command._prepared_targets, command._applied_once, command._executed = layers, False, False
    history.restore()
    return True


def _capture_embed_restore_notice(canvas: Any, group: Any) -> _EmbedScope | None:
    if group is not None and any(
        type(getattr(entry, "command", None)) is _EmbedResourceConsumersCommand
        for entry in group.entries
    ):
        return _EmbedScope(canvas)
    return None


def _submit_resource_embed(canvas: Any, command: _EmbedResourceConsumersCommand) -> bool:
    if canvas._in_transaction or is_document_capture_blocked(canvas):
        return False
    layers = tuple(find_layer(canvas, identifier) for identifier in command.layer_ids)
    scope = _EmbedScope(canvas, tuple(layer for layer in layers if layer is not None))
    if not layers or any(layer is None for layer in layers) or not scope.current():
        return False
    history = _CommandHistoryReceipt(canvas, scope)
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    committed, recovered = False, False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = True  # Prepared default apply has not changed metadata.
                    return False
                if not scope.current():
                    return False
                canvas._record_history_command("Embed Resource", command)
                if not history.recorded(command):
                    raise RuntimeError("Embed history commitment was not confirmed")
                committed = True
            except Exception:
                recovered = _rollback_embed_metadata(history, command, layers)
                logger.exception("Resource Embed failed before commitment")
                return False
            try:
                scope.emit_document_change()
                capture.release()
                for layer in layers:
                    if not scope.current():
                        break
                    canvas._publish_layer_event(
                        Events.LAYER_CHANGED, layer=layer, change_type="property_changed"
                    )
            except Exception:
                logger.exception("Resource Embed notification failed after commitment")
        return True
    finally:
        if committed or recovered or not scope.current():
            capture.release()
