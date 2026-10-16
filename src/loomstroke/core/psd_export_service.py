"""Capture PSD export pixels and hierarchy without altering live layers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtGui import QImage, QPainter, QTransform

from loomstroke.io.psd_document_data import PsdDocumentData
from loomstroke.io.psd_document_writer import PsdDocumentWriter
from loomstroke.io.psd_layer_node import PsdLayerNode

from .commands import _layer_transform


class PsdExportService:
    @classmethod
    def export(cls, canvas, path: Path) -> None:
        width, height = canvas.get_canvas_size()
        roots = list(canvas._layers)
        nodes = tuple(cls._node(canvas, layer, width, height) for layer in roots)
        if not nodes:
            raise ValueError("No layers to export.")
        PsdDocumentWriter.write(path, PsdDocumentData(width, height, nodes))

    @classmethod
    def _node(cls, canvas, layer, width, height) -> PsdLayerNode:
        values = dict(
            name=layer.name,
            opacity=layer.opacity,
            visible=layer.visible,
            blend_mode=layer.blend_mode.value,
            clipping=layer.clipping,
        )
        if layer.layer_type == "group":
            return PsdLayerNode(
                **values,
                group=True,
                mask=(
                    cls._array(layer.layer_mask)[:, :, 0] if layer.layer_mask is not None else None
                ),
                mask_enabled=layer.mask_enabled,
                children=tuple(cls._node(canvas, child, width, height) for child in layer.children),
            )
        if layer.layer_type in {"effect", "filter", "ai_effect"}:
            raise ValueError(
                "PSD export cannot preserve this filter layer. Export a flattened image instead."
            )
        image = (
            canvas._rasterize_vector_layer(layer) if layer.layer_type == "vector" else layer.image
        )
        if image is None or layer.layer_type == "3d":
            raise ValueError("A layer could not be rendered for PSD export.")
        if layer.layer_type == "vector":
            return PsdLayerNode(**values, image=cls._array(image), offset=(0, 0))
        transform = _layer_transform(layer).to_affine()
        if any(
            abs(value) > 1e-9
            for value in (transform.b, transform.c, transform.a - 1, transform.d - 1)
        ):
            output = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(0)
            painter = QPainter(output)
            painter.setTransform(
                QTransform(
                    transform.a, transform.c, transform.b, transform.d, transform.tx, transform.ty
                )
            )
            painter.drawImage(0, 0, image)
            painter.end()
            image, offset = output, (0, 0)
            if layer.layer_mask is not None and layer.mask_enabled:
                raise ValueError("PSD export cannot preserve a transformed layer mask.")
        else:
            offset = round(transform.tx), round(transform.ty)
        mask = None
        if layer.layer_mask is not None:
            mask = cls._array(layer.layer_mask)[:, :, 0]
        return PsdLayerNode(
            **values,
            image=cls._array(image),
            offset=offset,
            mask=mask,
            mask_enabled=layer.mask_enabled,
        )

    @staticmethod
    def _array(image: QImage) -> np.ndarray:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        return (
            np.frombuffer(converted.constBits(), np.uint8)
            .reshape(height, converted.bytesPerLine())[:, : width * 4]
            .reshape(height, width, 4)
            .copy()
        )
