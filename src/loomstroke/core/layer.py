from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .commands import BlendMode as _CommandBlendMode

RGBA8 = tuple[int, int, int, int]
FloatArray = NDArray[np.float32]
UInt8Array = NDArray[np.uint8]


@dataclass
class _DirtyRegion:
    x: int
    y: int
    width: int
    height: int

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


class Layer:
    """Lightweight layer representation used by the compatibility canvas."""

    def __init__(
        self,
        *,
        name: str,
        width: int,
        height: int,
        fill_color: RGBA8 | None = None,
    ) -> None:
        self.id: str = str(uuid.uuid4())
        self.name: str = name
        self.width: int = width
        self.height: int = height
        self.opacity: float = 1.0
        self.visible: bool = True
        self.locked: bool = False
        self.blend_mode: str = "normal"
        self.buffer: FloatArray = np.zeros((height, width, 4), dtype=np.float32)
        if fill_color is not None:
            r, g, b, a = fill_color
            self.buffer[:, :, 0] = float(r)
            self.buffer[:, :, 1] = float(g)
            self.buffer[:, :, 2] = float(b)
            self.buffer[:, :, 3] = float(a)
        self._dirty_region: _DirtyRegion | None = None

    def clone(self, *, name: str | None = None) -> Layer:
        copy = Layer(name=name or self.name, width=self.width, height=self.height)
        copy.opacity = self.opacity
        copy.visible = self.visible
        copy.locked = self.locked
        copy.blend_mode = self.blend_mode
        copy.buffer = self.buffer.copy()
        copy._dirty_region = self._dirty_region
        return copy

    def mark_dirty(self, x: int, y: int, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return
        rect = _DirtyRegion(x, y, width, height)
        if self._dirty_region is None:
            self._dirty_region = rect
            return
        prev = self._dirty_region
        new_x = min(prev.x, x)
        new_y = min(prev.y, y)
        new_x2 = max(prev.x + prev.width, x + width)
        new_y2 = max(prev.y + prev.height, y + height)
        self._dirty_region = _DirtyRegion(new_x, new_y, new_x2 - new_x, new_y2 - new_y)

    @property
    def dirty_region(self) -> tuple[int, int, int, int] | None:
        return None if self._dirty_region is None else self._dirty_region.to_tuple()

    def clear_dirty(self) -> None:
        self._dirty_region = None

    def get_image_data(
        self,
        *,
        dtype: type[np.uint8] | type[np.float32] = np.uint8,
        copy: bool = True,
    ) -> UInt8Array | FloatArray:
        """Return the layer image buffer as a numpy array in the requested dtype."""

        data: NDArray[Any] = self.buffer
        if copy:
            data = data.copy()

        if dtype is np.uint8 and data.dtype != np.uint8:
            data = np.clip(data, 0.0, 255.0).astype(np.uint8)
        elif dtype is np.float32 and data.dtype != np.float32:
            data = data.astype(np.float32)
        return data

    def set_image_data(self, data: NDArray[Any]) -> None:
        """Replace the buffer contents with the provided image array."""

        if data.ndim != 3:
            raise ValueError("image data must be HxWxC array")
        if data.shape[2] not in (3, 4):
            raise ValueError("image data must have 3 or 4 channels")

        arr = np.asarray(data, dtype=np.float32)
        if arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255.0, dtype=np.float32)
            arr = np.concatenate([arr, alpha], axis=2)

        self.buffer = arr
        self.height, self.width = arr.shape[:2]
        self.clear_dirty()


BlendMode = _CommandBlendMode

__all__ = ["BlendMode", "Layer"]
