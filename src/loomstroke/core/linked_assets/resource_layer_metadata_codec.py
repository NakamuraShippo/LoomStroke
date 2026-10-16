"""Layer metadata namespace for resource-backed raster layers (CT-18 §9).

Phase A keeps the ``.lstroke`` schema untouched: the record rides in the
existing per-layer ``metadata`` dict under ``loomstroke.comictools.resource``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from .resource_record import ResourceRecord

__all__ = ["ResourceLayerMetadataCodec"]


class ResourceLayerMetadataCodec:
    NAMESPACE: ClassVar[str] = "loomstroke.comictools.resource"
    SCHEMA_VERSION: ClassVar[int] = 1

    def read(self, metadata: Mapping[str, Any] | None) -> ResourceRecord | None:
        if not isinstance(metadata, Mapping):
            return None
        payload = metadata.get(self.NAMESPACE)
        if not isinstance(payload, Mapping):
            return None
        version = payload.get("schema_version")
        if not isinstance(version, int) or version < 1 or version > self.SCHEMA_VERSION:
            return None
        return ResourceRecord.from_dict(payload.get("record"))

    def write(self, metadata: Mapping[str, Any] | None, record: ResourceRecord) -> dict[str, Any]:
        merged = dict(metadata) if isinstance(metadata, Mapping) else {}
        merged[self.NAMESPACE] = {
            "schema_version": self.SCHEMA_VERSION,
            "record": record.to_dict(),
        }
        return merged

    def strip(self, metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(metadata, Mapping):
            return None
        stripped = {key: value for key, value in metadata.items() if key != self.NAMESPACE}
        return stripped or None
