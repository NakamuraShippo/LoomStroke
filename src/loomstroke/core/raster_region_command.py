"""Local raster patches with document-space dirties and reversible placement."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from .channels.channel_pixel_policy import ChannelPixelPolicy
from .command_layer_access import _layer_transform, _resolve_layer_by_id
from .command_types import Command, DirtyRegion
from .raster_edit_geometry import RasterEditGeometry


def preserve_pixel_policy(
    canvas: Any, layer: Any, before: QImage, after: QImage, *, erase: bool = False
) -> QImage:
    if layer.alpha_lock:
        pixels = ChannelPixelPolicy.pixels(after)
        pixels[:, :, 3] = ChannelPixelPolicy.pixels(before)[:, :, 3]
        np.minimum(pixels[:, :, :3], pixels[:, :, 3:4], out=pixels[:, :, :3])
        after = ChannelPixelPolicy.image(pixels)
    channels = canvas.channels() if hasattr(canvas, "channels") else None
    if channels is not None and channels.selected != (0, 1, 2):
        after = ChannelPixelPolicy.merge(before, after, channels.selected, erase=erase)
    return after


def mix_coverage(before: QImage, after: QImage, coverage: np.ndarray) -> QImage:
    a, b = ChannelPixelPolicy.pixels(before), ChannelPixelPolicy.pixels(after)
    amount = coverage.astype(np.uint32)[:, :, None]
    pixels = (a.astype(np.uint32) * (255 - amount) + b.astype(np.uint32) * amount + 127) // 255
    return ChannelPixelPolicy.image(pixels.astype(np.uint8))


class RasterRegionCommand(Command):
    def __init__(
        self, layer: Any, rect: QRect, before: QImage, after: QImage, *, kind: str = "paint"
    ) -> None:
        super().__init__()
        self.layer_id, self.rect = layer.id, QRect(rect)
        self.before, self.after = before, after
        self.transform = _layer_transform(layer)
        self.size = layer.image.size()
        self.kind = kind
        geometry = RasterEditGeometry.capture(layer)
        dirty = geometry.document_rect(rect)
        self._dirty_region = DirtyRegion(dirty.x(), dirty.y(), dirty.width(), dirty.height())

    @property
    def retained_bytes(self):
        return self.before.sizeInBytes() + self.after.sizeInBytes()

    def write_pixels(self, layer: Any, *, before: bool) -> bool:
        if layer is None or layer.image is None or layer.image.size() != self.size:
            return False
        if _layer_transform(layer) != self.transform:
            return False
        painter = QPainter(layer.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        try:
            painter.drawImage(self.rect.topLeft(), self.before if before else self.after)
        finally:
            painter.end()
        if hasattr(layer, "mark_dirty"):
            layer.mark_dirty(self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height())
        return True

    def _write(self, canvas: Any, *, before: bool) -> bool:
        layer = _resolve_layer_by_id(canvas, self.layer_id)
        if not self.write_pixels(layer, before=before):
            return False
        layer.texture_version += 1
        dirty = self._dirty_region
        rect = QRect(dirty.x, dirty.y, dirty.width, dirty.height)
        canvas._recompose_region(rect)
        canvas._emit_layer_content_changed(layer, rect, {"command": self.kind})
        return True

    def apply(self, canvas: Any) -> bool:
        if self.before == self.after or not self._write(canvas, before=False):
            return False
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or not self._write(canvas, before=True):
            return False
        self._executed = False
        return True
