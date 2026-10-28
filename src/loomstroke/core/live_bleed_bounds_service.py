"""Read current-page bounds without persisting or closing a history group."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from .binding_danger_geometry import transformed_bounds
from .canvas_layer_content_bounds_service import CanvasLayerContentBoundsService
from .command_layer_access import _layer_transform
from .document_capture_guard import is_document_capture_blocked
from .layer_command_commit_context import _LayerCommandContext
from .preflight.layer_preflight_snapshot import LayerPreflightSnapshot


class LiveBleedBoundsService:
    def __init__(self, canvas: Any) -> None:
        self.canvas = canvas

    def capture(self, budget: int) -> tuple[tuple[LayerPreflightSnapshot, ...] | None, int]:
        scope = _LayerCommandContext(self.canvas)
        if (
            is_document_capture_blocked(self.canvas)
            or getattr(self.canvas, "_in_transaction", False)
            or not hasattr(self.canvas, "_layers")
        ):
            return None, budget
        result = []

        def visit(layers, visible=True, verified=True):
            nonlocal budget
            for layer in layers:
                shown = visible and bool(layer.visible) and float(layer.opacity) > 0
                bounds, checked = None, not shown or layer.layer_type == "group"
                image = getattr(layer, "image", None)
                unmasked = (
                    verified
                    and not getattr(layer, "clipping", False)
                    and not (
                        getattr(layer, "layer_mask", None) is not None
                        and getattr(layer, "mask_enabled", True)
                    )
                )
                if (
                    shown
                    and unmasked
                    and layer.layer_type == "raster"
                    and not getattr(layer, "filter_type", None)
                    and getattr(layer, "native_cmyk", None) is None
                    and image is not None
                    and image.format()
                    in (
                        QImage.Format.Format_ARGB32,
                        QImage.Format.Format_ARGB32_Premultiplied,
                        QImage.Format.Format_RGB32,
                        QImage.Format.Format_RGBA8888,
                        QImage.Format.Format_RGBA8888_Premultiplied,
                        QImage.Format.Format_RGBX8888,
                    )
                    and image.width() * image.height() <= budget
                ):
                    height, width = image.height(), image.width()
                    budget -= width * height
                    view = (
                        np.frombuffer(image.constBits(), np.uint8)
                        .reshape(height, image.bytesPerLine())[:, : width * 4]
                        .reshape(height, width, 4)
                    )
                    local = CanvasLayerContentBoundsService.bounds_for_alpha(view[:, :, 3])
                    checked = True
                    if local is not None:
                        x, y, w, h = local
                        left, top, right, bottom = transformed_bounds(
                            (x, y, x + w, y + h), _layer_transform(layer).to_affine()
                        )
                        bounds = (
                            math.floor(left),
                            math.floor(top),
                            math.ceil(right) - math.floor(left),
                            math.ceil(bottom) - math.floor(top),
                        )
                result.append(
                    LayerPreflightSnapshot(
                        layer.id,
                        layer.name,
                        layer.layer_type,
                        visible=shown,
                        opacity=float(layer.opacity),
                        bleed_content_bounds=bounds,
                        bleed_bounds_checked=checked,
                    )
                )
                visit(getattr(layer, "children", ()) or (), shown, unmasked)

        visit(self.canvas._layers)
        if not scope.current():
            return None, budget
        return tuple(result), budget
