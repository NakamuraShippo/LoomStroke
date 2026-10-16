"""Shared wgpu runtime used by 2D and 3D consumers."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from .adapter_policy import (
    GpuBackendPreference,
    is_wgpu_importable,
    requested_render_backend,
)
from .diagnostics import GpuDiagnostics
from .failure_policy import GpuFailurePolicy
from .pipelines import GpuPipelineLibrary
from .resources import GpuResourceRegistry


class GpuRuntimeUnavailableError(RuntimeError):
    """Raised when the requested common GPU runtime cannot be created."""


@dataclass(slots=True)
class GpuDeviceContext:
    """wgpu handles shared across runtime consumers."""

    wgpu: Any
    adapter: Any
    device: Any
    queue: Any
    adapter_info: dict[str, Any] = field(default_factory=dict)


class GpuRuntime:
    """Lazy owner for adapter, device, queue, registries, and diagnostics."""

    def __init__(
        self,
        *,
        backend_preference: GpuBackendPreference | None = None,
        power_preference: str = "high-performance",
        diagnostics: GpuDiagnostics | None = None,
        failure_policy: GpuFailurePolicy | None = None,
    ) -> None:
        self.backend_preference = backend_preference or requested_render_backend()
        self.power_preference = power_preference
        self.diagnostics = diagnostics or GpuDiagnostics(backend_name="wgpu")
        self.failure_policy = failure_policy or GpuFailurePolicy()
        self.resources = GpuResourceRegistry()
        self.pipelines = GpuPipelineLibrary()
        self._context: GpuDeviceContext | None = None

    @staticmethod
    def import_wgpu() -> Any:
        if not is_wgpu_importable():
            raise GpuRuntimeUnavailableError("optional dependency 'wgpu' is not installed")
        try:
            return importlib.import_module("wgpu")
        except Exception as exc:  # pragma: no cover - depends on local install state
            raise GpuRuntimeUnavailableError(
                f"optional dependency 'wgpu' could not be imported: {exc}"
            ) from exc

    def is_available(self) -> bool:
        try:
            self.get_context()
        except GpuRuntimeUnavailableError as exc:
            self.diagnostics.record_failure(str(exc))
            return False
        return True

    def get_context(self) -> GpuDeviceContext:
        if self._context is not None:
            return self._context
        if self.backend_preference == GpuBackendPreference.OPENGL:
            raise GpuRuntimeUnavailableError(
                "LOOMSTROKE_RENDER_BACKEND=opengl requests the retired OpenGL backend"
            )

        wgpu = self.import_wgpu()
        try:
            adapter = wgpu.gpu.request_adapter_sync(power_preference=self.power_preference)
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise GpuRuntimeUnavailableError(f"wgpu adapter probe failed: {exc}") from exc
        if adapter is None:
            raise GpuRuntimeUnavailableError("wgpu adapter is unavailable")

        try:
            device = adapter.request_device_sync(required_features=[], required_limits={})
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise GpuRuntimeUnavailableError(f"wgpu device creation failed: {exc}") from exc

        adapter_info = self._adapter_info(adapter)
        self._context = GpuDeviceContext(
            wgpu=wgpu,
            adapter=adapter,
            device=device,
            queue=device.queue,
            adapter_info=adapter_info,
        )
        self.diagnostics.record_adapter(adapter_info)
        return self._context

    def release(self) -> None:
        self.resources.clear()
        self.pipelines.invalidate()
        self._context = None

    @staticmethod
    def _adapter_info(adapter: Any) -> dict[str, Any]:
        request_info = getattr(adapter, "request_adapter_info", None)
        if callable(request_info):
            try:
                info = request_info()
                if isinstance(info, dict):
                    return dict(info)
            except Exception:
                return {}
        info = getattr(adapter, "info", None)
        return dict(info) if isinstance(info, dict) else {}
