"""Owned sparse ink regions for one coherent asynchronous display job."""

from collections.abc import Iterable
from contextlib import nullcontext
from types import SimpleNamespace

from PySide6.QtCore import QRect

from loomstroke.core.native_cmyk.pixels import NativeCmykPixels


class NativeCmykFrameSnapshot:
    def __init__(self, native, regions, offset_x, offset_y):
        self.profile = native.profile
        self.inks = SimpleNamespace(shape=native.inks.shape, dtype=native.inks.dtype)
        self._regions = {}
        height, width = native.inks.shape[:2]
        bounds = QRect(offset_x, offset_y, width, height)
        # All regions are captured on the owning UI thread, before a worker runs.
        with getattr(native, "_lock", nullcontext()):
            for region in regions:
                rect = QRect(*region).intersected(bounds)
                if rect.isEmpty():
                    continue
                key = (rect.x() - offset_x, rect.y() - offset_y, rect.width(), rect.height())
                if hasattr(native, "region"):
                    self._regions[key] = native.region(key)
                else:
                    x, y, w, h = key
                    self._regions[key] = NativeCmykPixels(
                        native.inks[y : y + h, x : x + w],
                        native.alpha[y : y + h, x : x + w],
                        self.profile,
                    )

    def region(self, rect):
        if rect in self._regions:
            return self._regions[rect]
        x, y, width, height = rect
        for (sx, sy, sw, sh), pixels in self._regions.items():
            if sx <= x and sy <= y and x + width <= sx + sw and y + height <= sy + sh:
                return NativeCmykPixels(
                    pixels.inks[y - sy : y - sy + height, x - sx : x - sx + width],
                    pixels.alpha[y - sy : y - sy + height, x - sx : x - sx + width],
                    self.profile,
                )
        raise ValueError("CMYK worker requested pixels outside its owned frame regions")


def split_regions(
    regions: Iterable[QRect | tuple[int, int, int, int]], size: int = 256
) -> tuple[tuple[int, int, int, int], ...]:
    """Group exact dirty rectangles by fixed canvas tile without joining distant edits."""
    tiles: dict[tuple[int, int], QRect] = {}
    for region in regions:
        rect = QRect(*region) if not isinstance(region, QRect) else region
        if rect.isEmpty():
            continue
        for ty in range(rect.y() // size, rect.bottom() // size + 1):
            for tx in range(rect.x() // size, rect.right() // size + 1):
                clipped = rect.intersected(QRect(tx * size, ty * size, size, size))
                key = tx, ty
                tiles[key] = tiles[key].united(clipped) if key in tiles else clipped
    return tuple((r.x(), r.y(), r.width(), r.height()) for r in tiles.values())
