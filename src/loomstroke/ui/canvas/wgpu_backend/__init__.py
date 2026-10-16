"""Optional wgpu offscreen rendering backend.

The package is intentionally importable without the optional ``wgpu`` wheel.
Device creation happens lazily through :class:`WgpuDeviceManager`.
"""

from __future__ import annotations

from .device_manager import WgpuDeviceManager, is_wgpu_importable
from .native_surface import NativeSurfaceEvaluation, evaluate_native_surface
from .offscreen_renderer import WgpuOffscreenRenderer
from .types import (
    WgpuBackendError,
    WgpuDeviceLostError,
    WgpuRenderError,
    WgpuRenderResult,
    WgpuShaderError,
    WgpuTextureError,
    WgpuUnavailableError,
)

__all__ = [
    "NativeSurfaceEvaluation",
    "WgpuBackendError",
    "WgpuDeviceLostError",
    "WgpuDeviceManager",
    "WgpuOffscreenRenderer",
    "WgpuRenderError",
    "WgpuRenderResult",
    "WgpuShaderError",
    "WgpuTextureError",
    "WgpuUnavailableError",
    "evaluate_native_surface",
    "is_wgpu_importable",
]
