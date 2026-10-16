"""Project raster sampling data and visible mask coverage into document space."""

from __future__ import annotations

import math
from dataclasses import astuple
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTransform

from .command_layer_access import (
    _coerce_layer_transform,
    _layer_offset,
    _layer_transform,
    _resolve_layer_by_id,
)
from .command_types import LayerTransform
from .mask_coverage import mask_coverage_alpha8


def image_rgba_copy(image: QImage) -> np.ndarray:
    """Read straight RGBA without detaching or modifying the source image."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())
    return rows[:, : rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4).copy()


def raster_selection_reference(
    canvas: Any, layer: Any, source: np.ndarray, width: int, height: int
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return placed RGBA and selectable coverage, both at document dimensions.

    Affine translation already includes the mirrored layer offset. Group masks
    clip children in document coordinates; group transforms do not transform
    children in the renderer. Transparent source pixels remain selectable,
    while pixels outside the source footprint or enabled masks do not.
    """
    if source.ndim != 3 or source.shape[2] not in (3, 4) or not source.size:
        return None
    transform = _layer_transform(layer)
    preview = getattr(layer, "_preview_transform", None)
    if preview is not None:
        transform = _coerce_layer_transform(preview, layer)
        if transform == LayerTransform.identity():
            transform = transform.with_translation(*_layer_offset(layer))
    if not all(math.isfinite(value) for value in astuple(transform)):
        return None
    affine = transform.to_affine()
    if affine.inverse() is None:
        return None
    mapping = QTransform(affine.a, affine.c, affine.b, affine.d, affine.tx, affine.ty)
    data = np.ascontiguousarray(source, dtype=np.uint8)
    source_height, source_width = data.shape[:2]
    image_format = (
        QImage.Format.Format_RGBA8888 if data.shape[2] == 4 else QImage.Format.Format_RGB888
    )
    image = QImage(data.data, source_width, source_height, data.strides[0], image_format)
    placed = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    placed.fill(Qt.GlobalColor.transparent)
    painter = QPainter(placed)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setTransform(mapping)
    painter.drawImage(0, 0, image)
    painter.end()

    footprint = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    footprint.fill(Qt.GlobalColor.transparent)
    painter = QPainter(footprint)
    painter.setTransform(mapping)
    painter.fillRect(0, 0, source_width, source_height, Qt.GlobalColor.white)
    painter.end()
    coverage = image_rgba_copy(footprint)[:, :, 3]

    # Do not let hidden pixels connect separate visible regions during fill.
    node = layer
    visited: set[int] = set()
    while node is not None:
        if id(node) in visited:
            return None
        visited.add(id(node))
        mask = getattr(node, "layer_mask", None)
        if isinstance(mask, QImage) and not mask.isNull() and getattr(node, "mask_enabled", True):
            alpha = mask_coverage_alpha8(mask)
            if alpha.width() != width or alpha.height() != height:
                alpha = alpha.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio)
            rows = np.frombuffer(alpha.constBits(), np.uint8).reshape(height, alpha.bytesPerLine())
            coverage = ((coverage.astype(np.uint16) * rows[:, :width] + 127) // 255).astype(
                np.uint8
            )
        parent_id = getattr(node, "parent_id", None)
        node = _resolve_layer_by_id(canvas, parent_id) if parent_id else None
    return image_rgba_copy(placed), coverage
