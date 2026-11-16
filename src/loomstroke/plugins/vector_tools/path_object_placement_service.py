"""Detached raster/vector layer copies for path object placement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QTransform

from loomstroke.core.command_layer_access import _layer_transform
from loomstroke.core.geometry import Affine2D
from loomstroke.core.path_text.sources import vector_contours

from ..sdk.assist_vector_objects import transformed_copy
from .common import compute_vector_bounds


def _compose(left: Affine2D, right: Affine2D) -> Affine2D:
    return Affine2D(
        left.a * right.a + left.b * right.c,
        left.a * right.b + left.b * right.d,
        left.c * right.a + left.d * right.c,
        left.c * right.b + left.d * right.d,
        left.a * right.tx + left.b * right.ty + left.tx,
        left.c * right.tx + left.d * right.ty + left.ty,
    )


def _from_qt(value: QTransform) -> Affine2D:
    return Affine2D(value.m11(), value.m21(), value.m12(), value.m22(), value.dx(), value.dy())


def _to_qt(value: Affine2D) -> QTransform:
    return QTransform(value.a, value.c, value.b, value.d, value.tx, value.ty)


def target_contours(layer: Any) -> list[tuple[str, int, tuple, dict]]:
    """List usable contours of every object in the selected vector layer."""
    if layer is None or layer.layer_type != "vector":
        return []
    affine = _layer_transform(layer).to_affine()
    result = []
    for obj in layer.vector_objects:
        visible_obj = obj if affine == Affine2D.identity() else transformed_copy(obj, affine)
        for index, (rect, path) in enumerate(vector_contours(visible_obj)):
            result.append((obj.id, index, rect, path))
    return result


@dataclass(slots=True)
class PlacementSource:
    bounds: tuple[float, float, float, float]
    image: QImage | None = None
    image_origin: tuple[int, int] = (0, 0)
    image_affine: Affine2D | None = None
    vector_objects: list[Any] | None = None

    @property
    def width(self) -> float:
        return max(1.0, self.bounds[2] - self.bounds[0])

    @property
    def center(self) -> tuple[float, float]:
        return ((self.bounds[0] + self.bounds[2]) / 2, (self.bounds[1] + self.bounds[3]) / 2)


def capture_source(layer: Any) -> PlacementSource | None:
    """Snapshot visible source content without changing the source layer."""
    if layer is None:
        return None
    affine = _layer_transform(layer).to_affine()
    if layer.layer_type == "vector":
        objects = [transformed_copy(obj, affine) for obj in layer.vector_objects]
        boxes = [compute_vector_bounds(obj) for obj in objects]
        boxes = [box for box in boxes if box is not None]
        if not boxes:
            return None
        bounds = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
        return PlacementSource(bounds, vector_objects=objects)
    if layer.layer_type != "raster" or layer.image is None or layer.image.isNull():
        return None
    image = layer.image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(image.constBits(), np.uint8).reshape(
        image.height(), image.bytesPerLine()
    )
    alpha = pixels[:, : image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, 3]
    ys, xs = np.flatnonzero(alpha.any(axis=1)), np.flatnonzero(alpha.any(axis=0))
    if not len(xs):
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    corners = [affine.apply(x, y) for x in (x0, x1) for y in (y0, y1)]
    bounds = (
        min(p[0] for p in corners),
        min(p[1] for p in corners),
        max(p[0] for p in corners),
        max(p[1] for p in corners),
    )
    return PlacementSource(
        bounds,
        image=image.copy(x0, y0, x1 - x0, y1 - y0),
        image_origin=(x0, y0),
        image_affine=affine,
    )


def place_source(
    source: PlacementSource, frames: list[QTransform], canvas_size: tuple[int, int]
) -> QImage | list[Any]:
    """Build a new layer payload; vector copies keep editable anchors."""
    cx, cy = source.center
    center_shift = Affine2D.translation(-cx, -cy)
    if source.vector_objects is not None:
        return [
            transformed_copy(obj, _compose(_from_qt(frame), center_shift))
            for frame in frames
            for obj in source.vector_objects
        ]
    assert source.image is not None and source.image_affine is not None
    output = QImage(*canvas_size, QImage.Format.Format_ARGB32_Premultiplied)
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    local = _compose(
        center_shift,
        _compose(source.image_affine, Affine2D.translation(*source.image_origin)),
    )
    for frame in frames:
        painter.setWorldTransform(_to_qt(_compose(_from_qt(frame), local)))
        painter.drawImage(0, 0, source.image)
    painter.end()
    return output
