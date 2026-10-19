"""Immutable display mosaics copy changed tiles instead of the complete canvas."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy


@dataclass(frozen=True)
class _Tile:
    image: QImage
    revision: int


class NativeCmykDisplayTiles:
    SIZE = 256

    def __init__(
        self,
        width: int,
        height: int,
        *,
        tiles: dict[tuple[int, int], _Tile] | None = None,
        family: object | None = None,
        sequence: int = 0,
    ) -> None:
        self.width, self.height = width, height
        self.tiles: Mapping[tuple[int, int], _Tile] = MappingProxyType(
            tiles if tiles is not None else {}
        )
        self.family = object() if family is None else family
        self.sequence = sequence

    def updated(
        self, patches: Iterable[tuple[tuple[int, int, int, int], Any]], sequence: int
    ) -> NativeCmykDisplayTiles:
        tiles = dict(self.tiles)
        changed: dict[tuple[int, int], QImage] = {}
        for region, rgba in patches:
            rect = QRect(*region).intersected(QRect(0, 0, self.width, self.height))
            if rect.isEmpty():
                continue
            patch = ChannelPixelPolicy.image(rgba, straight=True)
            for ty in range(rect.y() // self.SIZE, rect.bottom() // self.SIZE + 1):
                for tx in range(rect.x() // self.SIZE, rect.right() // self.SIZE + 1):
                    key = tx, ty
                    image = changed.get(key)
                    x, y = tx * self.SIZE, ty * self.SIZE
                    if image is None:
                        previous = tiles.get(key)
                        if previous is not None:
                            image = previous.image.copy()
                        else:
                            image = QImage(
                                min(self.SIZE, self.width - x),
                                min(self.SIZE, self.height - y),
                                QImage.Format.Format_ARGB32_Premultiplied,
                            )
                            image.fill(Qt.GlobalColor.transparent)
                        changed[key] = image
                    painter = QPainter(image)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    painter.drawImage(region[0] - x, region[1] - y, patch)
                    painter.end()
        tiles.update({key: _Tile(image, sequence) for key, image in changed.items()})
        return NativeCmykDisplayTiles(
            self.width, self.height, tiles=tiles, family=self.family, sequence=sequence
        )

    def pixel_color(self, x: int, y: int) -> QColor:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return QColor(0, 0, 0, 0)
        tile = self.tiles.get((x // self.SIZE, y // self.SIZE))
        return (
            tile.image.pixelColor(x % self.SIZE, y % self.SIZE)
            if tile is not None
            else QColor(0, 0, 0, 0)
        )

    def image(self) -> QImage:
        image = QImage(self.width, self.height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for (tx, ty), tile in self.tiles.items():
            painter.drawImage(tx * self.SIZE, ty * self.SIZE, tile.image)
        painter.end()
        return image
