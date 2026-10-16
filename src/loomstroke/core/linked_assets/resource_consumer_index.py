"""resource_id -> record + consuming layers, derived from layer metadata (CT-18 §7.1)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .resource_consumer import ResourceConsumer
from .resource_layer_metadata_codec import ResourceLayerMetadataCodec
from .resource_record import ResourceRecord

__all__ = ["ResourceConsumerIndex"]

LayerEntry = tuple[str, str, str, Mapping[str, Any] | None]
"""``(page_id, layer_id, layer_name, layer_metadata)``."""


class ResourceConsumerIndex:
    def __init__(self) -> None:
        self._records: dict[str, ResourceRecord] = {}
        self._consumers: dict[str, list[ResourceConsumer]] = {}

    @classmethod
    def build(
        cls, entries: Iterable[LayerEntry], codec: ResourceLayerMetadataCodec | None = None
    ) -> ResourceConsumerIndex:
        codec = codec or ResourceLayerMetadataCodec()
        index = cls()
        for page_id, layer_id, layer_name, metadata in entries:
            record = codec.read(metadata)
            if record is not None:
                index.add(record, ResourceConsumer(page_id, layer_id, layer_name))
        return index

    def add(self, record: ResourceRecord, consumer: ResourceConsumer) -> None:
        self._records.setdefault(record.resource_id, record)
        self._consumers.setdefault(record.resource_id, []).append(consumer)

    def records(self) -> tuple[ResourceRecord, ...]:
        return tuple(
            sorted(self._records.values(), key=lambda r: (r.display_name.lower(), r.resource_id))
        )

    def record(self, resource_id: str) -> ResourceRecord | None:
        return self._records.get(resource_id)

    def consumers_for(self, resource_id: str) -> tuple[ResourceConsumer, ...]:
        return tuple(self._consumers.get(resource_id, ()))

    def consumers_on_page(self, resource_id: str, page_id: str) -> tuple[ResourceConsumer, ...]:
        return tuple(c for c in self._consumers.get(resource_id, ()) if c.page_id == page_id)

    def resource_ids(self) -> tuple[str, ...]:
        return tuple(record.resource_id for record in self.records())

    def __len__(self) -> int:
        return len(self._records)
