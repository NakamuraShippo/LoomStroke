"""Bounded source/document mapping and clip sampling for placed raster edits."""

from __future__ import annotations

import math
from dataclasses import astuple, dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QTransform

from .comic_panels.membership import PART_KEY
from .command_layer_access import _layer_transform, _resolve_layer_by_id
from .command_types import LayerTransform
from .geometry import Affine2D
from .mask_coverage import mask_coverage_alpha8


def mapped_rect(mapping: Affine2D, rect: QRect) -> QRect:
    corners = [
        mapping.apply(x, y)
        for x in (rect.x(), rect.x() + rect.width())
        for y in (rect.y(), rect.y() + rect.height())
    ]
    left, top = math.floor(min(x for x, _ in corners)), math.floor(min(y for _, y in corners))
    right, bottom = math.ceil(max(x for x, _ in corners)), math.ceil(max(y for _, y in corners))
    return QRect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class RasterEditGeometry:
    mapping: Affine2D
    width: int
    height: int

    @classmethod
    def capture(cls, layer: Any) -> RasterEditGeometry:
        transform = _layer_transform(layer)
        if not all(math.isfinite(v) for v in astuple(transform)):
            raise ValueError("Raster editing requires a finite placement transform.")
        mapping = transform.to_affine()
        if mapping.inverse() is None or getattr(layer, "_preview_transform", None) is not None:
            raise ValueError("Raster editing requires a confirmed, invertible placement transform.")
        return cls(mapping, layer.image.width(), layer.image.height())

    def source_rect(self, document_rect: QRect) -> QRect:
        inverse = self.mapping.inverse()
        assert inverse is not None
        return (
            mapped_rect(inverse, document_rect)
            .adjusted(-1, -1, 1, 1)
            .intersected(QRect(0, 0, self.width, self.height))
        )

    def document_rect(self, source_rect: QRect) -> QRect:
        return mapped_rect(self.mapping, source_rect)

    def painter_transform(self, rect: QRect) -> QTransform:
        inv = self.mapping.inverse()
        assert inv is not None
        return QTransform(inv.a, inv.c, inv.b, inv.d, inv.tx - rect.x(), inv.ty - rect.y())

    def document_grid(self, rect: QRect) -> tuple[np.ndarray, np.ndarray]:
        y, x = np.mgrid[rect.y() : rect.y() + rect.height(), rect.x() : rect.x() + rect.width()]
        x, y = x.astype(np.float64) + 0.5, y.astype(np.float64) + 0.5
        m = self.mapping
        return m.a * x + m.b * y + m.tx, m.c * x + m.d * y + m.ty


def uses_placed_pixels(canvas: Any, layer: Any) -> bool:
    if not isinstance(getattr(layer, "image", None), QImage) or layer.layer_type != "raster":
        return False
    if _layer_transform(layer) != LayerTransform.identity():
        return True
    return any(
        (node.metadata or {}).get(PART_KEY, {}).get("role") == "content"
        for node in raster_ancestors(canvas, layer)
    )


def raster_ancestors(canvas: Any, layer: Any):
    seen = set()
    while layer is not None:
        if layer.id in seen:
            raise ValueError("The raster layer ancestry contains a cycle.")
        seen.add(layer.id)
        yield layer
        parent = getattr(layer, "parent_id", None)
        layer = _resolve_layer_by_id(canvas, parent) if parent else None
        if parent and layer is None:
            raise ValueError("The raster layer parent is missing.")


def clip_signature(canvas: Any, layer: Any) -> tuple:
    return tuple(
        (
            node.id,
            node.parent_id,
            node.visible,
            node.locked,
            node.mask_enabled,
            node.layer_mask.cacheKey() if isinstance(node.layer_mask, QImage) else None,
        )
        for node in raster_ancestors(canvas, layer)
    )


def sample_mask(mask: QImage, x: np.ndarray, y: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Sample only the required mask patch; don't convert a full-page mask per dab."""
    mx = np.floor(x * mask.width() / size[0]).astype(np.int64)
    my = np.floor(y * mask.height() / size[1]).astype(np.int64)
    valid = (mx >= 0) & (my >= 0) & (mx < mask.width()) & (my < mask.height())
    result = np.zeros(x.shape, dtype=np.uint8)
    if not valid.any():
        return result
    left, right = int(mx[valid].min()), int(mx[valid].max())
    top, bottom = int(my[valid].min()), int(my[valid].max())
    alpha = mask_coverage_alpha8(mask.copy(QRect(left, top, right - left + 1, bottom - top + 1)))
    rows = np.frombuffer(alpha.constBits(), np.uint8).reshape(alpha.height(), alpha.bytesPerLine())
    result[valid] = rows[my[valid] - top, mx[valid] - left]
    return result


def edit_coverage(
    canvas: Any,
    layer: Any,
    geometry: RasterEditGeometry,
    rect: QRect,
    selection: QImage | None = None,
) -> np.ndarray:
    x, y = geometry.document_grid(rect)
    size = canvas.get_canvas_size()
    result = ((x >= 0) & (y >= 0) & (x < size[0]) & (y < size[1])).astype(np.uint8) * 255
    for node in raster_ancestors(canvas, layer):
        if not node.visible or node.locked:
            return np.zeros_like(result)
        mask = node.layer_mask
        if isinstance(mask, QImage) and not mask.isNull() and node.mask_enabled:
            coverage = sample_mask(mask, x, y, size)
            result = ((result.astype(np.uint16) * coverage + 127) // 255).astype(np.uint8)
    if selection is not None:
        coverage = sample_mask(selection, x, y, size)
        result = ((result.astype(np.uint16) * coverage + 127) // 255).astype(np.uint8)
    return result
