"""Adapter selection helpers for shared GPU consumers."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping
from enum import Enum

RENDER_BACKEND_ENV = "LOOMSTROKE_RENDER_BACKEND"


class GpuBackendPreference(str, Enum):
    """Normalized render backend requests."""

    AUTO = "auto"
    WGPU = "wgpu"
    OPENGL = "opengl"


def is_wgpu_importable() -> bool:
    """Return whether the optional ``wgpu`` package is importable."""

    return importlib.util.find_spec("wgpu") is not None


def requested_render_backend(
    env: Mapping[str, str] | None = None,
) -> GpuBackendPreference:
    """Return the normalized backend requested by the environment.

    Retired ``opengl`` aliases are treated as ``auto`` so old launch scripts
    do not become fatal. Unknown values also fall back to ``auto``.
    """

    source = env if env is not None else os.environ
    raw = source.get(RENDER_BACKEND_ENV, "").strip().lower()
    if raw in {"opengl", "gl", "legacy"}:
        return GpuBackendPreference.AUTO
    if raw in {"wgpu", "webgpu", "wgpu-bitmap", "wgpu_bitmap", "wgpu-native", "wgpu_native"}:
        return GpuBackendPreference.WGPU
    return GpuBackendPreference.AUTO
