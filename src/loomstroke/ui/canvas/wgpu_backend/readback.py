"""Readback helpers for wgpu offscreen textures."""

from __future__ import annotations

from typing import Any, cast

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.gpu.readback import (
    GpuReadbackService,
)
from loomstroke.gpu.readback import align_bytes_per_row as _common_align_bytes_per_row

from .types import WgpuRenderTargetHandle


def align_bytes_per_row(value: int, alignment: int = 256) -> int:
    if alignment == 256:
        return _common_align_bytes_per_row(value)
    if value <= 0:
        return alignment
    return ((value + alignment - 1) // alignment) * alignment


class WgpuReadback:
    """Read an offscreen texture into straight-alpha RGBA bytes."""

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._service = GpuReadbackService(bundle.queue)

    def read_rgba(self, target: WgpuRenderTargetHandle) -> np.ndarray:
        return self._service.read_rgba(cast(Any, target))

    def read_rgba_region(
        self,
        target: WgpuRenderTargetHandle,
        rect: QRect,
    ) -> tuple[np.ndarray, QRect] | None:
        """Read a clipped texture region into straight-alpha RGBA bytes."""

        return self._service.read_rgba_region(cast(Any, target), rect)
