"""Undoable explicit RGB-to-native conversion; no reverse inference from previews."""

from __future__ import annotations

from typing import Any

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.command_types import Command
from loomstroke.io.file_io import ColorProfile

from .buffer import NativeCmykBuffer
from .capabilities import NativeCmykCapabilities
from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


class ConvertNativeCmykCommand(Command):
    def __init__(self, profile: bytes, *, source_profile: bytes | None = None) -> None:
        super().__init__()
        self.profile = CmykColorTransform.validate_profile(bytes(profile))
        self.source_profile = source_profile
        self._states: list[tuple[Any, Any, Any, NativeCmykBuffer, Any]] = []
        self._original_profile = None

    def apply(self, canvas: Any) -> bool:
        if canvas._in_transaction:
            return False
        layers = canvas._layer_tree().flatten_layers()
        if not self._states:
            self._original_profile = getattr(canvas, "color_profile", None)
            for layer in layers:
                NativeCmykCapabilities.validate_layer(layer)
                if layer.native_cmyk is not None:
                    raise ValueError("The document is already CMYK")
            states = []
            for layer in layers:
                if layer.layer_type != "raster" or layer.image is None:
                    continue
                rgba = ChannelPixelPolicy.pixels(layer.image, straight=True)
                inks, alpha = CmykColorTransform.from_rgb(
                    rgba, self.profile, source_profile=self.source_profile
                )
                native = NativeCmykPixels(inks, alpha, self.profile)
                preview = ChannelPixelPolicy.image(native.preview(), straight=True).convertToFormat(
                    layer.image.format()
                )
                states.append(
                    (
                        layer.id,
                        layer.image.copy(),
                        layer.native_cmyk,
                        NativeCmykBuffer(native),
                        preview,
                    )
                )
            self._states = states
        current = {layer.id: layer for layer in layers}
        if not self._states or any(identifier not in current for identifier, *_ in self._states):
            return False
        for identifier, _image, _native, buffer, preview in self._states:
            layer = current[identifier]
            layer.native_cmyk = buffer
            layer.image = preview.copy()
            layer.texture_version += 1
        self._executed = True
        canvas.set_color_profile(ColorProfile.srgb())
        self._notify(canvas)
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        current = {layer.id: layer for layer in canvas._layer_tree().flatten_layers()}
        if any(identifier not in current for identifier, *_ in self._states):
            return False
        for identifier, image, native, _buffer, _preview in self._states:
            layer = current[identifier]
            layer.native_cmyk = native
            layer.image = image.copy()
            layer.texture_version += 1
        self._executed = False
        canvas.set_color_profile(self._original_profile)
        self._notify(canvas)
        return True

    @staticmethod
    def _notify(canvas: Any) -> None:
        canvas.channels().reset()
        canvas._invalidate_layer_cache()
        for layer in canvas._layer_tree().flatten_layers():
            if layer.layer_type == "raster":
                canvas._emit_layer_content_changed(
                    layer, layer.image.rect(), {"command": "convert_native_cmyk"}
                )
        canvas._recompose()
