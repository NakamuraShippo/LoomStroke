"""Keep unpublished tile damage across bounded asynchronous frame batches."""

from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import QRect

from .native_cmyk_frame_snapshot import split_regions


class NativeCmykDirtyQueue:
    def __init__(self):
        self.serial = 0
        self._tiles = {}

    def clear(self):
        self._tiles.clear()

    def merge(self, regions: Iterable[QRect | tuple[int, int, int, int]]) -> None:
        self.serial += 1
        for region in split_regions(regions):
            key = region[0] // 256, region[1] // 256
            previous = self._tiles.pop(key, None)
            rect = QRect(*region)
            if previous is not None:
                rect = rect.united(QRect(*previous[0]))
            self._tiles[key] = (
                (rect.x(), rect.y(), rect.width(), rect.height()),
                self.serial,
            )

    def batch(self, layers):
        # Four tiles normally; reduce further for many-layer / 16-bit sources.
        bytes_per_pixel = sum(
            layer.native_cmyk.inks.dtype.itemsize * 5
            for layer in layers
            if layer.visible and layer.native_cmyk is not None
        )
        limit = max(1, min(4, (16 * 1024 * 1024) // max(1, 256 * 256 * bytes_per_pixel)))
        return tuple(entry[0] for entry in list(self._tiles.values())[:limit])

    def prioritize(self, regions: Iterable[QRect | tuple[int, int, int, int]]) -> None:
        first: dict[tuple[int, int], Any] = {}
        for x, y, _width, _height in split_regions(regions):
            key = x // 256, y // 256
            if key in self._tiles:
                first[key] = self._tiles.pop(key)
        self._tiles = {**first, **self._tiles}

    def acknowledge(self, frame):
        for region, _pixels in frame:
            key = region[0] // 256, region[1] // 256
            pending = self._tiles.get(key)
            if pending is not None and pending[1] <= frame.key[2]:
                del self._tiles[key]

    def __bool__(self):
        return bool(self._tiles)
