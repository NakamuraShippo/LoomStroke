"""One prepared metadata-only Embed for the current page's resource consumers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .command_types import Command
from .commands_layer_lookup import find_layer
from .linked_assets.resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .page_history_memory import estimate_metadata_payload_bytes


def _resource_consumer_layers(canvas: Any, resource_id: str) -> tuple[Any, ...]:
    codec = ResourceLayerMetadataCodec()
    return tuple(
        layer
        for layer in canvas._flatten_layers()
        if (record := codec.read(layer.metadata)) is not None and record.resource_id == resource_id
    )


class _EmbedResourceConsumersCommand(Command):
    """Keep metadata references, never a layer's image or an external source."""

    def __init__(self, layers: tuple[Any, ...], record: Any) -> None:
        super().__init__()
        codec = ResourceLayerMetadataCodec()
        self.layer_ids = tuple(layer.id for layer in layers)
        if len(set(self.layer_ids)) != len(self.layer_ids):
            raise ValueError("Embed requires distinct consumer IDs")
        self.source_record = record
        self.before = tuple(layer.metadata for layer in layers)
        prepared = []
        for metadata in self.before:
            after = codec.write(metadata, record.embedded())
            # Own this operation's namespace; unrelated metadata stays shared.
            after[codec.NAMESPACE] = deepcopy(after[codec.NAMESPACE])
            prepared.append(after)
        self.after = tuple(prepared)
        self._prepared_targets = layers
        self._applied_once = False

    @property
    def retained_bytes(self) -> int:
        seen: set[int] = set()
        return sum(
            estimate_metadata_payload_bytes(value, seen=seen)
            for value in (*self.before, *self.after)
        )

    def _resolve(self, canvas: Any) -> tuple[Any, ...] | None:
        layers = tuple(find_layer(canvas, identifier) for identifier in self.layer_ids)
        if any(layer is None or layer.native_cmyk is not None for layer in layers):
            return None
        return layers

    def apply(self, canvas: Any) -> bool:
        layers = self._resolve(canvas)
        if layers is None or (
            not self._applied_once
            and any(
                live is not prepared
                for live, prepared in zip(layers, self._prepared_targets, strict=True)
            )
        ):
            return False
        if not self._applied_once and any(
            layer.metadata is not before
            or ResourceLayerMetadataCodec().read(layer.metadata) != self.source_record
            for layer, before in zip(layers, self.before, strict=True)
        ):
            return False
        if not self._applied_once:
            consumers = _resource_consumer_layers(canvas, self.source_record.resource_id)
            if len(consumers) != len(layers) or {layer.id for layer in consumers} != set(
                self.layer_ids
            ):
                return False
        for layer, metadata in zip(layers, self.after, strict=True):
            layer.metadata = metadata
        self._prepared_targets = ()
        self._applied_once = True
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layers = self._resolve(canvas)
        if layers is None:
            return False
        for layer, metadata in zip(layers, self.before, strict=True):
            layer.metadata = metadata
        self._executed = False
        return True
