"""Lossless canvas geometry and premultiplied resampling of native ink planes."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .buffer import NativeCmykBuffer
from .pixels import NativeCmykPixels


class NativeCmykGeometry:
    @staticmethod
    def capture(layers):
        return {
            layer.id: layer.native_cmyk.snapshot()
            for layer in layers
            if getattr(layer, "native_cmyk", None) is not None
        }

    @staticmethod
    def restore(layer, snapshot):
        pixels = getattr(snapshot, "native_layers", {}).get(layer.id)
        if pixels is not None:
            layer.native_cmyk = NativeCmykBuffer(pixels)

    @staticmethod
    def flip(layer, horizontal, vertical):
        native = getattr(layer, "native_cmyk", None)
        if native is None:
            return
        axes = tuple(axis for axis, enabled in ((1, horizontal), (0, vertical)) if enabled)
        layer.native_cmyk = NativeCmykBuffer(
            NativeCmykPixels(
                np.flip(native.inks, axes), np.flip(native.alpha, axes), native.profile
            )
        )

    @staticmethod
    def rotate(layer, angle):
        native = getattr(layer, "native_cmyk", None)
        if native is not None:
            layer.native_cmyk = NativeCmykBuffer(
                NativeCmykPixels(
                    np.rot90(native.inks, -angle // 90),
                    np.rot90(native.alpha, -angle // 90),
                    native.profile,
                )
            )

    @staticmethod
    def resized(layer, width, height, shift_x, shift_y):
        native = getattr(layer, "native_cmyk", None)
        if native is None:
            return None
        inks = np.zeros((height, width, 4), dtype=native.inks.dtype)
        alpha = np.zeros((height, width), dtype=native.alpha.dtype)
        x, y = layer.offset_x + shift_x, layer.offset_y + shift_y
        left, top = max(0, x), max(0, y)
        right = min(width, x + native.inks.shape[1])
        bottom = min(height, y + native.inks.shape[0])
        if left < right and top < bottom:
            source = np.s_[top - y : bottom - y, left - x : right - x]
            target = np.s_[top:bottom, left:right]
            inks[target], alpha[target] = native.inks[source], native.alpha[source]
        return NativeCmykBuffer(NativeCmykPixels(inks, alpha, native.profile))

    @staticmethod
    def resample(layer, width, height, mode):
        from loomstroke.core.image_size.interpolation import InterpolationMode  # noqa: PLC0415
        from loomstroke.core.image_size.raster_resample_service import (  # noqa: PLC0415
            RasterResampleService,
        )

        native = getattr(layer, "native_cmyk", None)
        if native is None:
            return
        kernel = RasterResampleService._FILTERS[InterpolationMode.normalize(mode)]
        layer.native_cmyk = NativeCmykGeometry.sample_buffer(
            native,
            lambda image: image.resize((width, height), kernel),
            nearest=kernel == Image.Resampling.NEAREST,
        )

    @staticmethod
    def sample_buffer(native, sample, *, nearest):
        """Apply a common raster sampling operation to inks and alpha."""
        maximum = np.iinfo(native.inks.dtype).max

        def plane(values):
            return np.asarray(
                sample(Image.fromarray(values.astype(np.float32))),
                dtype=np.float64,
            )

        def encode(values):
            return np.rint(np.clip(values, 0, maximum)).astype(native.inks.dtype)

        if nearest:
            # Preserve hidden ink in transparent pixels for nearest-neighbour copies.
            alpha = plane(native.alpha)
            inks = np.stack([encode(plane(native.inks[:, :, index])) for index in range(4)], axis=2)
        else:
            source_alpha = native.alpha.astype(np.float64) / maximum
            # Negative-lobe kernels can overshoot alpha. Unpremultiply by
            # the same filtered alpha before clamping output coverage;
            # clamping first changes constant plate colours at opaque edges.
            alpha = plane(source_alpha)
            visible = alpha > 0
            inks = np.empty((*alpha.shape, 4), dtype=native.inks.dtype)
            for index in range(4):
                filtered = plane(native.inks[:, :, index] * source_alpha)
                np.divide(filtered, alpha, out=filtered, where=visible)
                filtered[~visible] = 0
                inks[:, :, index] = encode(filtered)
            alpha *= maximum
        return NativeCmykBuffer(NativeCmykPixels(inks, encode(alpha), native.profile))
