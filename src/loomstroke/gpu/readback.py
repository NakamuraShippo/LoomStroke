"""Common GPU readback helpers."""

from __future__ import annotations

import time
from typing import Any, Protocol, cast

import numpy as np
from PySide6.QtCore import QRect

from .diagnostics import GpuDiagnostics


class _ReadbackTarget(Protocol):
    texture: object
    width: int
    height: int


def align_bytes_per_row(row_bytes: int) -> int:
    """Align a texture row pitch to wgpu's 256-byte readback rule."""

    return ((int(row_bytes) + 255) // 256) * 256


class GpuReadbackService:
    """Read common RGBA/map textures back to CPU arrays."""

    def __init__(self, queue: object, *, diagnostics: GpuDiagnostics | None = None) -> None:
        self._queue = queue
        self._diagnostics = diagnostics

    def read_rgba(self, target: _ReadbackTarget) -> np.ndarray:
        started = time.perf_counter()
        result = self._read(target, QRect(0, 0, int(target.width), int(target.height)))
        self._record((time.perf_counter() - started) * 1000.0)
        return result

    def read_rgba_region(
        self,
        target: _ReadbackTarget,
        rect: QRect,
    ) -> tuple[np.ndarray, QRect] | None:
        clipped = QRect(rect).intersected(QRect(0, 0, int(target.width), int(target.height)))
        if clipped.isEmpty():
            return None
        started = time.perf_counter()
        result = self._read(target, clipped)
        self._record((time.perf_counter() - started) * 1000.0)
        return result, clipped

    def _read(self, target: _ReadbackTarget, rect: QRect) -> np.ndarray:
        width = int(rect.width())
        height = int(rect.height())
        bytes_per_row = align_bytes_per_row(width * 4)
        read_texture = cast(Any, self._queue).read_texture
        raw = read_texture(
            {
                "texture": target.texture,
                "origin": (int(rect.x()), int(rect.y()), 0),
            },
            {
                "offset": 0,
                "bytes_per_row": bytes_per_row,
                "rows_per_image": height,
            },
            (width, height, 1),
        )
        padded = np.frombuffer(raw, dtype=np.uint8).reshape((height, bytes_per_row))
        packed = padded[:, : width * 4].reshape((height, width, 4)).copy()
        return self._unpremultiply_alpha(packed)

    def _record(self, elapsed_ms: float) -> None:
        if self._diagnostics is not None:
            self._diagnostics.record_readback(elapsed_ms)

    @staticmethod
    def _unpremultiply_alpha(rgba: np.ndarray) -> np.ndarray:
        alpha = rgba[..., 3:4].astype(np.uint16)
        nonzero = alpha[..., 0] > 0
        if not np.any(nonzero):
            return rgba
        rgb = rgba[..., :3].astype(np.uint16)
        rgb[nonzero] = np.minimum((rgb[nonzero] * 255 + alpha[nonzero] // 2) // alpha[nonzero], 255)
        rgba[..., :3] = rgb.astype(np.uint8)
        return rgba
