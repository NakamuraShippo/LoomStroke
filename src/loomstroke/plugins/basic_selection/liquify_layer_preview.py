"""Build a liquified RGB layer for display in its original stacking position."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.command_types import LayerTransform, SelectionRect
from loomstroke.core.selection_clipboard import SelectionPayload
from loomstroke.core.selection_projective_composite import composite_projective_qimage


@dataclass(slots=True)
class LiquifyLayerFrame:
    layer_id: str
    image: QImage
    offset_x: int
    offset_y: int
    transform: LayerTransform

    @classmethod
    def capture(cls, layer: Any) -> LiquifyLayerFrame:
        # Qt shares the original storage until the provisional cut detaches it.
        image = layer.image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return cls(layer.id, image, layer.offset_x, layer.offset_y, layer.transform)

    def compose(
        self,
        payload: SelectionPayload,
        pixels: np.ndarray,
        mask: np.ndarray,
        bounds: SelectionRect,
        *,
        binary: bool,
    ) -> LiquifyLayerFrame:
        result = replace(self, image=QImage(self.image))
        if not composite_projective_qimage(
            result.image,
            payload,
            pixels,
            mask,
            bounds,
            offset=(self.offset_x, self.offset_y),
            binary=binary,
            duplicate=False,
            additive=True,
        ):
            raise RuntimeError("Unable to compose liquify preview")
        return result

    def apply_to_layers(self, layers: tuple) -> tuple:
        def visit(layer):
            if layer.id == self.layer_id:
                return replace(
                    layer,
                    image=self.image,
                    texture_version=-self.image.cacheKey(),
                    offset_x=self.offset_x,
                    offset_y=self.offset_y,
                    transform=self.transform,
                    initial_dirty_rect=None,
                    initial_upload_key=None,
                )
            if layer.children:
                return replace(layer, children=[visit(child) for child in layer.children])
            return layer

        return tuple(visit(layer) for layer in layers)


class LiquifyLayerPreview:
    def __init__(
        self, original: LiquifyLayerFrame, canvas: Any, bounds: SelectionRect, whole: bool
    ):
        self.original = self.current = original
        self.base = replace(original)
        if not whole:
            # Expand only the private backing image just as the selection command does.
            expand = getattr(canvas, "_ensure_layer_image_bounds", None)
            if callable(expand):
                expand(self.base, QRect(*bounds))
