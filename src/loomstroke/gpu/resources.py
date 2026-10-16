"""Backend-agnostic GPU resource registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass(slots=True)
class GpuResourceRecord:
    """One resource tracked by the common runtime."""

    id: str
    kind: str
    handle: Any
    estimated_bytes: int = 0
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_monotonic: float = field(default_factory=monotonic)
    last_used_monotonic: float = field(default_factory=monotonic)


class GpuResourceRegistry:
    """Track shared runtime resources without owning their backend type."""

    def __init__(self) -> None:
        self._records: dict[str, GpuResourceRecord] = {}

    def register(
        self,
        *,
        id: str,
        kind: str,
        handle: Any,
        estimated_bytes: int = 0,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GpuResourceRecord:
        record = GpuResourceRecord(
            id=str(id),
            kind=str(kind),
            handle=handle,
            estimated_bytes=max(0, int(estimated_bytes)),
            label=label,
            metadata=dict(metadata) if metadata else {},
        )
        self._records[record.id] = record
        return record

    def get(self, id: str) -> GpuResourceRecord | None:
        record = self._records.get(id)
        if record is not None:
            record.last_used_monotonic = monotonic()
        return record

    def release(self, id: str) -> bool:
        record = self._records.pop(id, None)
        if record is None:
            return False
        destroy = getattr(record.handle, "destroy", None)
        if callable(destroy):
            destroy()
        return True

    def clear(self) -> None:
        for resource_id in tuple(self._records):
            self.release(resource_id)

    def estimated_bytes(self) -> int:
        return sum(record.estimated_bytes for record in self._records.values())

    def snapshot(self) -> tuple[GpuResourceRecord, ...]:
        return tuple(self._records.values())

    def __len__(self) -> int:
        return len(self._records)
