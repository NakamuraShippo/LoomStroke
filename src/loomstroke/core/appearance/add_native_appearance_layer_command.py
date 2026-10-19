"""Add a baked native appearance without converting its ink samples through RGB."""

from __future__ import annotations

from typing import Any

from ..channels.channel_pixel_policy import ChannelPixelPolicy
from ..native_cmyk.buffer import NativeCmykBuffer
from ..native_cmyk.pixels import NativeCmykPixels
from .add_appearance_layer_command import AddAppearanceLayerCommand


class AddNativeAppearanceLayerCommand(AddAppearanceLayerCommand):
    def __init__(self, name: str, pixels: NativeCmykPixels) -> None:
        super().__init__(name)
        self._native_pixels = pixels

    def apply(self, canvas: Any) -> bool:
        first = self._undo_remove is None
        if not super().apply(canvas):
            return False
        if first:
            layer = canvas._get_layer_by_id(self._layer_id)
            layer.native_cmyk = NativeCmykBuffer(self._native_pixels)
            layer.image = ChannelPixelPolicy.image(self._native_pixels.preview(), straight=True)
            layer.texture_version += 1
        return True
