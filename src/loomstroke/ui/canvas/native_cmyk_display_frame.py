"""Worker-owned immutable display frames with lazy compatibility images."""

from collections.abc import Iterable
from typing import Any

from PySide6.QtGui import QColor, QImage

from .native_cmyk_display_tiles import NativeCmykDisplayTiles


class NativeCmykDisplayFrame(list[Any]):
    def __init__(
        self,
        patches: Iterable[Any],
        image: QImage | None,
        key: Any,
        *,
        surface: NativeCmykDisplayTiles | None = None,
    ) -> None:
        super().__init__(patches)
        self._image = image
        self.surface = surface
        self.key = key

    @property
    def image(self) -> QImage | None:
        if self._image is None and self.surface is not None:
            self._image = self.surface.image()
        return self._image

    def pixel_color(self, x: int, y: int) -> QColor:
        if self.surface is not None:
            return self.surface.pixel_color(x, y)
        if self._image is not None:
            color: QColor = self._image.pixelColor(x, y)
            return color
        return QColor(0, 0, 0, 0)


class NativeCmykDisplayCache:
    def __init__(self) -> None:
        self._surface: NativeCmykDisplayTiles | None = None
        self._context: Any = None
        self._sequence = 0

    def publish(
        self,
        width: int,
        height: int,
        patches: list[Any],
        key: Any,
        *,
        inherit_contexts: tuple[Any, ...] = (),
    ) -> NativeCmykDisplayFrame:
        context = key[0] if isinstance(key, tuple) else key
        if (
            self._surface is None
            or (self._surface.width, self._surface.height) != (width, height)
            or (self._context != context and self._context not in inherit_contexts)
        ):
            self._surface = NativeCmykDisplayTiles(width, height)
        self._sequence += 1
        self._surface = self._surface.updated(patches, self._sequence)
        self._context = context
        return NativeCmykDisplayFrame(patches, None, key, surface=self._surface)
