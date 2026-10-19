"""First-touch tile capture, independent of growing stroke bounding-box area."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .limits import MAX_REGION_BYTES
from .raster import StrokeImage, crop, paste, stroke_pixel_bytes

TILE = 128


def check_size(rect: QRect, *, bytes_per_pixel: int = 4) -> None:
    if rect.width() * rect.height() * bytes_per_pixel > MAX_REGION_BYTES:
        raise ValueError("Recontour region exceeds 128 MiB")


class Preimage:
    def __init__(self) -> None:
        self.rect = QRect()
        self.tiles: dict[tuple[int, int], StrokeImage] = {}
        self.mask: StrokeImage = QImage()

    def capture(self, layer: Any, rect: QRect, *, mask: bool) -> None:
        union = self.rect.united(rect) if not self.rect.isEmpty() else QRect(rect)
        pixel_bytes = stroke_pixel_bytes(layer, mask=mask)
        check_size(union, bytes_per_pixel=pixel_bytes)
        self.rect = union
        if mask:
            if self.mask.isNull():
                self.mask = crop(layer, rect, mask=True)
            return
        for y in range(rect.top() // TILE, rect.bottom() // TILE + 1):
            for x in range(rect.left() // TILE, rect.right() // TILE + 1):
                if (x, y) not in self.tiles:
                    if (len(self.tiles) + 1) * TILE * TILE * pixel_bytes > MAX_REGION_BYTES:
                        raise ValueError("Recontour tile budget exceeded")
                    self.tiles[x, y] = crop(layer, QRect(x * TILE, y * TILE, TILE, TILE))

    def materialize(self, layer: Any, *, mask: bool) -> StrokeImage:
        if mask:
            return self.mask
        image = crop(layer, self.rect)
        for (x, y), tile in self.tiles.items():
            paste(image, tile, x * TILE - self.rect.x(), y * TILE - self.rect.y())
        self.tiles.clear()
        return image
