"""Mutable native layer; mutations and snapshots have distinct ownership."""

from __future__ import annotations

from collections import deque
from threading import RLock

from .pixels import NativeCmykPixels


class NativeCmykBuffer:
    def __init__(self, snapshot: NativeCmykPixels) -> None:
        self.inks = snapshot.inks.copy()
        self.alpha = snapshot.alpha.copy()
        self.profile = snapshot.profile
        self.version = 0
        self._dirty = deque(maxlen=64)
        self._lock = RLock()

    def snapshot(self) -> NativeCmykPixels:
        with self._lock:
            return NativeCmykPixels(self.inks, self.alpha, self.profile)

    def copy(self) -> NativeCmykBuffer:
        return NativeCmykBuffer(self.snapshot())

    def patch(self, rect: tuple[int, int, int, int], pixels: NativeCmykPixels) -> None:
        x, y, width, height = rect
        if (
            x < 0
            or y < 0
            or width < 1
            or height < 1
            or x + width > self.inks.shape[1]
            or y + height > self.inks.shape[0]
            or pixels.inks.shape != (height, width, 4)
            or pixels.inks.dtype != self.inks.dtype
            or pixels.profile != self.profile
        ):
            raise ValueError("Native CMYK patch does not match the layer")
        with self._lock:
            self.inks[y : y + height, x : x + width] = pixels.inks
            self.alpha[y : y + height, x : x + width] = pixels.alpha
            self.version += 1
            self._dirty.append((self.version, rect))

    def dirty_since(self, version: int):
        if version == self.version:
            return ()
        if not self._dirty or version < self._dirty[0][0] - 1:
            return None
        return tuple(rect for revision, rect in self._dirty if revision > version)

    def region(self, rect: tuple[int, int, int, int]) -> NativeCmykPixels:
        x, y, width, height = rect
        with self._lock:
            return NativeCmykPixels(
                self.inks[y : y + height, x : x + width],
                self.alpha[y : y + height, x : x + width],
                self.profile,
            )
