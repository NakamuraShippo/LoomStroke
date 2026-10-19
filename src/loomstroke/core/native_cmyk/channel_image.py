"""Derived channel inspection images; never edits or separates RGB previews."""

import numpy as np
from PySide6.QtCore import Qt

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy


class NativeCmykChannelImage:
    @staticmethod
    def image(native, channel, max_extent=None):
        if hasattr(native, "region"):
            height, width = native.inks.shape[:2]
            native = native.region((0, 0, width, height))
        index = {"c": 0, "m": 1, "y": 2, "k": 3}.get(channel)
        values = native.alpha if channel == "transparency" else native.inks[:, :, index]
        if max_extent is not None:
            stride = max(1, max(values.shape) // max_extent)
            values = values[::stride, ::stride]
        if values.dtype == np.uint16:
            values = ((values.astype(np.uint32) + 128) // 257).astype(np.uint8)
        # Photoshop-style plates: white has no ink, black has full ink.
        if channel != "transparency":
            values = 255 - values
        rgba = np.full((*values.shape, 4), 255, np.uint8)
        rgba[:, :, :3] = values[:, :, None]
        image = ChannelPixelPolicy.image(rgba, straight=True)
        if max_extent is not None:
            image = image.scaled(
                max_extent,
                max_extent,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return image
