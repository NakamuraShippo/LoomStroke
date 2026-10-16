"""Decode nested PSD groups, raw layer opacity, masks, and clipping."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .psd_document_data import PsdDocumentData
from .psd_layer_node import PsdLayerNode


class PsdDocumentReader:
    MAX_LAYERS = 2048
    MAX_DEPTH = 64
    MAX_PIXELS = 128_000_000

    def __init__(self, validate_image=None) -> None:
        self._validate = validate_image
        self._count = 0
        self._pixels = 0

    def read(self, path: Path) -> PsdDocumentData:
        from psd_tools import PSDImage  # noqa: PLC0415

        self._count, self._pixels = 0, 0
        psd = PSDImage.open(path)
        self._check(path, psd.width, psd.height)
        self._width, self._height = psd.width, psd.height
        return PsdDocumentData(
            psd.width, psd.height, tuple(self._node(layer, path, 0) for layer in psd)
        )

    def _check(self, path: Path, width: int, height: int) -> None:
        if width <= 0 or height <= 0 or width * height > self.MAX_PIXELS:
            raise ValueError("PSD image dimensions exceed the import limit")
        if self._validate is not None:
            self._validate(path, width, height)

    def _node(self, layer, path: Path, depth: int) -> PsdLayerNode:
        self._count += 1
        if depth > self.MAX_DEPTH or self._count > self.MAX_LAYERS:
            raise ValueError("PSD hierarchy exceeds the import limit")
        blend = getattr(layer.blend_mode, "name", "NORMAL").lower()
        values = dict(
            name=layer.name,
            blend_mode=blend,
            opacity=layer.opacity / 255.0,
            visible=bool(layer.visible),
            clipping=bool(layer.clipping),
        )
        if layer.is_group():
            if layer.has_effects() or layer.has_vector_mask():
                raise ValueError("PSD group effects require a flattened appearance import")
            mask, enabled = None, True
            if layer.has_mask():
                source = layer.mask
                self._pixels += self._width * self._height
                if self._pixels > self.MAX_PIXELS:
                    raise ValueError("PSD decoded layers exceed the import budget")
                mask = Image.new("L", (self._width, self._height), source.background_color)
                mask.paste(source.topil(), (source.left, source.top))
                mask, enabled = np.asarray(mask).copy(), not source.disabled
            return PsdLayerNode(
                **values,
                group=True,
                mask=mask,
                mask_enabled=enabled,
                children=tuple(self._node(child, path, depth + 1) for child in layer),
            )
        self._check(path, max(1, layer.width), max(1, layer.height))
        self._pixels += max(1, layer.width) * max(1, layer.height)
        if self._pixels > self.MAX_PIXELS:
            raise ValueError("PSD decoded layers exceed the import budget")
        image = layer.topil()
        warnings = ()
        has_special = layer.has_effects() or layer.has_vector_mask() or image is None
        if has_special:
            opacity, visible = layer.opacity, layer.visible
            try:
                layer.opacity, layer.visible = 255, True
                image = layer.composite(force=True)
            finally:
                layer.opacity, layer.visible = opacity, visible
            warnings = ("PSD appearance was baked into raster pixels.",)
        if image is None:
            raise ValueError(f"PSD layer could not be rendered: {layer.name}")
        image = image.convert("RGBA")
        mask_data, mask_enabled = None, True
        if layer.has_mask() and not has_special:
            mask = layer.mask
            mask_image = Image.new("L", image.size, mask.background_color)
            mask_image.paste(mask.topil(), (mask.left - layer.left, mask.top - layer.top))
            mask_data, mask_enabled = np.asarray(mask_image).copy(), not mask.disabled
        return PsdLayerNode(
            **values,
            image=np.asarray(image).copy(),
            offset=(layer.left, layer.top),
            mask=mask_data,
            mask_enabled=mask_enabled,
            warnings=warnings,
        )
