"""Evaluation hook for future 2D native wgpu surface presentation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from loomstroke.gpu import (
    GpuBackendPreference,
    GpuRuntime,
    GpuRuntimeUnavailableError,
    GpuTargetDescriptor,
    GpuTargetFactory,
    requested_render_backend,
)


@dataclass(frozen=True, slots=True)
class NativeSurfaceEvaluation:
    """Result of evaluating whether direct/native 2D presentation can run."""

    requested_backend: GpuBackendPreference
    descriptor: GpuTargetDescriptor
    common_runtime_available: bool
    fallback_required: bool
    reason: str | None = None


def evaluate_native_surface(
    *,
    width: int,
    height: int,
    runtime: GpuRuntime | None = None,
    env: Mapping[str, str] | None = None,
) -> NativeSurfaceEvaluation:
    """Evaluate common-runtime readiness for a future native 2D surface."""

    requested = requested_render_backend(env)
    descriptor = GpuTargetFactory.native_surface_descriptor(width, height)
    if requested == GpuBackendPreference.OPENGL:
        return NativeSurfaceEvaluation(
            requested_backend=requested,
            descriptor=descriptor,
            common_runtime_available=False,
            fallback_required=True,
            reason="retired OpenGL backend requested; use wgpu native or bitmap",
        )

    runtime = runtime or GpuRuntime(backend_preference=requested)
    try:
        runtime.get_context()
    except GpuRuntimeUnavailableError as exc:
        return NativeSurfaceEvaluation(
            requested_backend=requested,
            descriptor=descriptor,
            common_runtime_available=False,
            fallback_required=True,
            reason=str(exc),
        )

    return NativeSurfaceEvaluation(
        requested_backend=requested,
        descriptor=descriptor,
        common_runtime_available=True,
        fallback_required=False,
    )
