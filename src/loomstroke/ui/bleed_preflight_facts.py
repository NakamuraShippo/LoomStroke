"""Bounded, read-only raster coverage facts for the bleed rule."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..core.binding_danger_geometry import transformed_bounds
from ..core.bleed_geometry import MAX_BLEED_PIXELS
from ..core.canvas_layer_content_bounds_service import CanvasLayerContentBoundsService
from ..core.layer_transform_metadata import read_layer_transform


class BleedBoundsBudget:
    def __init__(self, pixels: int = MAX_BLEED_PIXELS) -> None:
        self.remaining = pixels

    def collect(
        self, layers: list[Any], *, visible: bool = True, verified: bool = True
    ) -> dict[str, tuple[Any, bool]]:
        result = {}
        for layer in layers:
            shown = visible and bool(layer.visible) and float(layer.opacity) > 0
            layer_id = str(layer.id)
            if not shown or layer.layer_type == "group":
                result[layer_id] = (None, True)
            elif (
                not verified
                or getattr(layer, "clipping", False)
                or (
                    getattr(layer, "layer_mask_image", None) is not None
                    and getattr(layer, "mask_enabled", True)
                )
            ):
                result[layer_id] = (None, False)
            elif layer.layer_type == "vector":
                result[layer_id] = (None, not bool(getattr(layer, "vector_objects", ())))
            elif layer.layer_type != "raster" or getattr(layer, "effect_type", None):
                result[layer_id] = (None, False)
            else:
                result[layer_id] = self._raster(layer)
            child_verified = verified and not (
                getattr(layer, "layer_mask_image", None) is not None
                and getattr(layer, "mask_enabled", True)
            )
            result.update(
                self.collect(
                    getattr(layer, "children", None) or [], visible=shown, verified=child_verified
                )
            )
        return result

    def _raster(self, layer: Any) -> tuple[Any, bool]:
        native = getattr(layer, "native_cmyk", None)
        image = getattr(layer, "image", None)
        if native is not None:
            alpha = native.alpha
        elif isinstance(image, np.ndarray) and image.ndim == 3 and image.shape[2] == 4:
            alpha = image[:, :, 3]
        else:
            return None, False
        if alpha.ndim != 2 or alpha.size > self.remaining:
            return None, False
        self.remaining -= alpha.size
        bounds = CanvasLayerContentBoundsService.bounds_for_alpha(alpha)
        if bounds is None:
            return None, True
        x, y, width, height = bounds
        affine = read_layer_transform(
            getattr(layer, "metadata", None), getattr(layer, "position", (0, 0))
        ).to_affine()
        left, top, right, bottom = transformed_bounds((x, y, x + width, y + height), affine)
        return (
            math.floor(left),
            math.floor(top),
            math.ceil(right) - math.floor(left),
            math.ceil(bottom) - math.floor(top),
        ), True
