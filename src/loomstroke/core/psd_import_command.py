"""Append a fully prepared PSD hierarchy as one reversible tree operation."""

from __future__ import annotations

import uuid

import numpy as np
from PySide6.QtGui import QImage

from .canvas_layer_model import _Layer
from .commands import AddLayerCommand, BlendMode


class PsdImportCommand(AddLayerCommand):
    def __init__(self, document) -> None:
        super().__init__("PSD Import")
        self.layers = [self._layer(node) for node in document.layers]
        self.before = None
        self.active_before = None

    @classmethod
    def _layer(cls, node, parent_id=None):
        layer = _Layer(
            id=f"layer-{uuid.uuid4().hex}",
            name=node.name,
            layer_type="group" if node.group else "raster",
            opacity=node.opacity,
            visible=node.visible,
            blend_mode=BlendMode(node.blend_mode),
            offset_x=node.offset[0],
            offset_y=node.offset[1],
            parent_id=parent_id,
            clipping=node.clipping,
        )
        if node.image is not None:
            rgba = np.ascontiguousarray(node.image, dtype=np.uint8)
            height, width = rgba.shape[:2]
            layer.image = (
                QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
                .copy()
                .convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            )
        if node.mask is not None:
            mask = np.ascontiguousarray(node.mask, dtype=np.uint8)
            height, width = mask.shape
            layer.layer_mask = QImage(
                mask.data, width, height, width, QImage.Format.Format_Grayscale8
            ).copy()
            layer.mask_enabled = node.mask_enabled
        layer.children = [cls._layer(child, layer.id) for child in node.children]
        return layer

    @property
    def created_ids(self):
        def walk(layers):
            return [
                identifier for layer in layers for identifier in [layer.id, *walk(layer.children)]
            ]

        return walk(self.layers)

    def apply(self, canvas) -> bool:
        if not self.layers:
            return False
        if self.before is None:
            self.before = list(canvas._layers)
            self.active_before = canvas.current_layer().id
        canvas.replace_layers([*self.before, *self.layers])
        canvas.set_active_layer(self.created_ids[-1])
        return True

    def revert(self, canvas) -> bool:
        canvas.replace_layers(self.before)
        if self.active_before:
            canvas.set_active_layer(self.active_before)
        return True
