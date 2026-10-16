"""First-touch tile capture, independent of growing stroke bounding-box area."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .raster import crop, paste

MAX_REGION_BYTES = 128 * 1024 * 1024
TILE = 128


def check_size(rect: QRect) -> None:
    if rect.width() * rect.height() * 4 > MAX_REGION_BYTES:
        raise ValueError("Recontour region exceeds 128 MiB")


class Preimage:
    def __init__(self) -> None:
        self.rect = QRect()
        self.tiles: dict[tuple[int, int], QImage] = {}
        self.mask = QImage()

    def capture(self, layer: Any, rect: QRect, *, mask: bool) -> None:
        union = self.rect.united(rect) if not self.rect.isEmpty() else QRect(rect)
        check_size(union)
        self.rect = union
        if mask:
            if self.mask.isNull():
                self.mask = crop(layer, rect, mask=True)
            return
        for y in range(rect.top() // TILE, rect.bottom() // TILE + 1):
            for x in range(rect.left() // TILE, rect.right() // TILE + 1):
                if (x, y) not in self.tiles:
                    if (len(self.tiles) + 1) * TILE * TILE * 4 > MAX_REGION_BYTES:
                        raise ValueError("Recontour tile budget exceeded")
                    self.tiles[x, y] = crop(layer, QRect(x * TILE, y * TILE, TILE, TILE))

    def materialize(self, layer: Any, *, mask: bool) -> QImage:
        if mask:
            return self.mask
        image = crop(layer, self.rect)
        for (x, y), tile in self.tiles.items():
            paste(image, tile, x * TILE - self.rect.x(), y * TILE - self.rect.y())
        self.tiles.clear()
        return image
