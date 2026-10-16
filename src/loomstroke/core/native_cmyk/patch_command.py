"""Native tile history with a derived RGB preview updated only for the dirty patch."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.command_types import Command, DirtyRegion

from .pixels import NativeCmykPixels


class NativeCmykPatchCommand(Command):
    def __init__(
        self,
        layer_id: str,
        rect: tuple[int, int, int, int],
        before: NativeCmykPixels,
        after: NativeCmykPixels,
    ) -> None:
        super().__init__()
        self.layer_id = layer_id
        self.rect = rect
        self.before, self.after = before, after

    @property
    def retained_bytes(self) -> int:
        return sum(p.inks.nbytes + p.alpha.nbytes for p in (self.before, self.after))

    def apply(self, canvas: Any) -> bool:
        return self._write(canvas, self.after, True)

    def revert(self, canvas: Any) -> bool:
        return self._executed and self._write(canvas, self.before, False)

    def _write(self, canvas: Any, pixels: NativeCmykPixels, executed: bool) -> bool:
        layer = canvas._get_layer_by_id(self.layer_id)
        if layer is None or layer.native_cmyk is None or layer.image is None:
            return False
        # Convert before mutating the source, so an ICC/preview failure is atomic.
        preview = ChannelPixelPolicy.image(pixels.preview(), straight=True)
        painter = QPainter(layer.image)
        if not painter.isActive():
            return False
        try:
            layer.native_cmyk.patch(self.rect, pixels)
        except ValueError:
            painter.end()
            raise
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(self.rect[0], self.rect[1], preview)
        painter.end()
        layer.texture_version += 1
        x, y, width, height = self.rect
        self._dirty_region = DirtyRegion(x + layer.offset_x, y + layer.offset_y, width, height)
        self._executed = executed
        canvas._emit_layer_content_changed(
            layer,
            QRect(self._dirty_region.x, self._dirty_region.y, width, height),
            {"command": "native_cmyk_patch"},
        )
        canvas._recompose_region(QRect(self._dirty_region.x, self._dirty_region.y, width, height))
        return True
