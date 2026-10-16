"""Shared GPU runtime primitives for 2D and 3D render consumers."""

from .adapter_policy import GpuBackendPreference, is_wgpu_importable, requested_render_backend
from .diagnostics import GpuDiagnostics, GpuDiagnosticsSnapshot
from .failure_policy import GpuFailureDecision, GpuFailurePolicy
from .pipelines import GpuPipelineKey, GpuPipelineLibrary
from .readback import GpuReadbackService
from .resources import GpuResourceRecord, GpuResourceRegistry
from .runtime import GpuDeviceContext, GpuRuntime, GpuRuntimeUnavailableError
from .targets import GpuTargetDescriptor, GpuTargetFactory, GpuTargetHandle

__all__ = [
    "GpuBackendPreference",
    "GpuDeviceContext",
    "GpuDiagnostics",
    "GpuDiagnosticsSnapshot",
    "GpuFailureDecision",
    "GpuFailurePolicy",
    "GpuPipelineKey",
    "GpuPipelineLibrary",
    "GpuReadbackService",
    "GpuResourceRecord",
    "GpuResourceRegistry",
    "GpuRuntime",
    "GpuRuntimeUnavailableError",
    "GpuTargetDescriptor",
    "GpuTargetFactory",
    "GpuTargetHandle",
    "is_wgpu_importable",
    "requested_render_backend",
]
