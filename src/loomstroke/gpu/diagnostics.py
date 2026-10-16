"""Diagnostics collected by the shared GPU runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass(frozen=True, slots=True)
class GpuDiagnosticsSnapshot:
    """Immutable diagnostic view safe for UI/tests/logging."""

    backend_name: str
    adapter_info: dict[str, Any] = field(default_factory=dict)
    resource_count: int = 0
    estimated_resource_bytes: int = 0
    readback_count: int = 0
    last_readback_ms: float = 0.0
    failure_count: int = 0
    last_failure: str | None = None
    last_updated_monotonic: float = 0.0


class GpuDiagnostics:
    """Small, backend-agnostic diagnostics accumulator."""

    def __init__(self, *, backend_name: str = "gpu") -> None:
        self._backend_name = backend_name
        self._adapter_info: dict[str, Any] = {}
        self._resource_count = 0
        self._estimated_resource_bytes = 0
        self._readback_count = 0
        self._last_readback_ms = 0.0
        self._failure_count = 0
        self._last_failure: str | None = None
        self._last_updated = 0.0

    def record_adapter(self, adapter_info: dict[str, Any]) -> None:
        self._adapter_info = dict(adapter_info)
        self._touch()

    def record_resources(self, *, count: int, estimated_bytes: int) -> None:
        self._resource_count = max(0, int(count))
        self._estimated_resource_bytes = max(0, int(estimated_bytes))
        self._touch()

    def record_readback(self, elapsed_ms: float) -> None:
        self._readback_count += 1
        self._last_readback_ms = max(0.0, float(elapsed_ms))
        self._touch()

    def record_failure(self, message: str) -> None:
        self._failure_count += 1
        self._last_failure = str(message)
        self._touch()

    def snapshot(self) -> GpuDiagnosticsSnapshot:
        return GpuDiagnosticsSnapshot(
            backend_name=self._backend_name,
            adapter_info=dict(self._adapter_info),
            resource_count=self._resource_count,
            estimated_resource_bytes=self._estimated_resource_bytes,
            readback_count=self._readback_count,
            last_readback_ms=self._last_readback_ms,
            failure_count=self._failure_count,
            last_failure=self._last_failure,
            last_updated_monotonic=self._last_updated,
        )

    def _touch(self) -> None:
        self._last_updated = monotonic()
