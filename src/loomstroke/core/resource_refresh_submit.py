"""Prepare and commit a linked image refresh on its original page."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from .commands_layer_lookup import find_layer
from .commands_layer_resource import SetLayerResourceCommand
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent, Events
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .resource_embed_command import _resource_consumer_layers
from .resource_refresh_command import _ResourceRefreshCommand, refreshable_layer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ConsumerBefore:
    image: Any
    cache_key: int
    metadata: Any
    record: Any
    version: int


class _ResourceRefreshScope(_LayerCommandContext):
    def __init__(
        self, canvas: Any, resource_id: str | None, *, layers: tuple[Any, ...] = ()
    ) -> None:
        if resource_id is not None:
            layers = _resource_consumer_layers(canvas, resource_id)
        super().__init__(canvas, layers)
        self.resource_id = resource_id
        codec = ResourceLayerMetadataCodec()
        self.before = tuple(
            _ConsumerBefore(
                layer.image,
                layer.image.cacheKey() if refreshable_layer(layer) else 0,
                layer.metadata,
                codec.read(layer.metadata),
                layer.texture_version,
            )
            for layer in self.layers
        )

    def matches(self) -> bool:
        if not self.current():
            return False
        if self.resource_id is not None:
            live = _resource_consumer_layers(self.canvas, self.resource_id)
            if {id(layer) for layer in live} != {id(layer) for layer in self.layers}:
                return False
        codec = ResourceLayerMetadataCodec()
        return all(
            refreshable_layer(layer)
            and layer.image is before.image
            and layer.image.cacheKey() == before.cache_key
            and layer.metadata is before.metadata
            and codec.read(layer.metadata) == before.record
            and layer.texture_version == before.version
            for layer, before in zip(self.layers, self.before, strict=True)
        )


def _prepare_resource_refresh(canvas, scope, source_record, consumers, image, updated, label):
    if not scope.current():
        return None, "resource_scope_changed"
    if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
        return None, "resource_native_unsupported"
    identifiers = tuple(consumer.layer_id for consumer in consumers)
    if (
        len(identifiers) != len(set(identifiers))
        or set(identifiers) != {layer.id for layer in scope.layers}
        or any(before.record != source_record for before in scope.before)
        or not scope.matches()
    ):
        return None, "resource_consumer_changed"
    return _ResourceRefreshCommand(scope, image, updated, label), ""


class _ResourceRefreshNotice(_LayerCommandContext):
    def __init__(self, canvas, layers, *, changes_pixels):
        super().__init__(canvas, layers)
        self.changes_pixels = changes_pixels

    def emit_document_change(self) -> None:
        if self.current():
            kind = "resource_refresh" if self.changes_pixels else "resource_metadata"
            self.canvas._emit_doc_changed(DocumentChangeEvent(change_type=kind, source="canvas"))
        for layer in self.layers:
            if not self.current():
                break
            if self.changes_pixels:
                self.canvas._publish_layer_event(
                    Events.LAYER_CHANGED,
                    layer=layer,
                    change_type="content",
                    new_value={
                        "rect": (0, 0, layer.image.width(), layer.image.height()),
                        "metadata": {},
                    },
                )
            else:
                self.canvas._publish_layer_event(
                    Events.LAYER_CHANGED, layer=layer, change_type="property_changed"
                )


def _capture_resource_refresh_restore_notice(canvas: Any, group: Any):
    if group is not None:
        for entry in group.entries:
            command = getattr(entry, "command", None)
            if type(command) is _ResourceRefreshCommand:
                layers = tuple(find_layer(canvas, identifier) for identifier in command.layer_ids)
                if all(refreshable_layer(layer) for layer in layers):
                    return _ResourceRefreshNotice(
                        canvas, layers, changes_pixels=command.changes_pixels
                    )
    return None


def _rollback_refresh(history, command, scope) -> bool:
    if (
        len(command.installed) != len(scope.layers)
        or not history.can_rollback(command)
        or not all(
            layer.image is (installed.image if command.changes_pixels else before.image)
            and layer.metadata is installed.metadata
            and layer.texture_version == before.version + int(command.changes_pixels)
            for layer, installed, before in zip(
                scope.layers, command.installed, scope.before, strict=True
            )
        )
    ):
        return False
    for layer, before in zip(scope.layers, scope.before, strict=True):
        layer.image, layer.metadata = before.image, before.metadata
        layer.texture_version = before.version
    command._executed = False
    history.restore()
    return True


def _submit_resource_refresh(canvas: Any, command: _ResourceRefreshCommand) -> bool:
    scope = command.scope
    if (
        scope is None
        or scope.canvas is not canvas
        or not scope.matches()
        or canvas._in_transaction
        or is_document_capture_blocked(canvas)
    ):
        return False
    history = _CommandHistoryReceipt(canvas, scope)
    notice = _ResourceRefreshNotice(canvas, scope.layers, changes_pixels=command.changes_pixels)
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    committed, recovered = False, False
    try:
        with batch:
            try:
                if not command.apply(canvas):
                    recovered = True
                    return False
                if not scope.current():
                    return False
                canvas._record_history_command(command.label, command)
                if not history.recorded(command):
                    raise RuntimeError("Resource refresh history was not recorded")
                committed = True
            except Exception:
                recovered = not command.installed or _rollback_refresh(history, command, scope)
                logger.exception("Resource refresh failed before commitment")
                return False
            try:
                if command.changes_pixels and scope.current():
                    canvas._invalidate_layer_cache()
                    canvas._recompose()
            except Exception:
                logger.exception("Resource refresh presentation failed after commitment")
            try:
                notice.emit_document_change()
            except Exception:
                logger.exception("Resource refresh notification failed after commitment")
        return True
    finally:
        command.finish()
        if committed or recovered or not scope.current():
            capture.release()


def _submit_set_layer_resource(canvas: Any, command: SetLayerResourceCommand) -> bool:
    layer = find_layer(canvas, command.layer_id)
    if type(command) is not SetLayerResourceCommand or not refreshable_layer(layer):
        return False
    if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
        return False
    try:
        scope = _ResourceRefreshScope(canvas, None, layers=(layer,))
        prepared = _ResourceRefreshCommand(
            scope, None, command.record, "Set Layer Resource", codec=command._codec
        )
        return _submit_resource_refresh(canvas, prepared)
    except Exception:
        logger.exception("Resource metadata preparation failed")
        return False
