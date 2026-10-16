"""Exact right-angle page mappings for retained frame sources."""

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QTransform

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .models import PanelFrameError


def page_matrix(mode, width, height):
    values = {
        "flip_h": (-1, 0, 0, 1, width, 0),
        "flip_v": (1, 0, 0, -1, 0, height),
        "flip_hv": (-1, 0, 0, -1, width, height),
        "rot180": (-1, 0, 0, -1, width, height),
        "rot90": (0, -1, 1, 0, height, 0),
        "rot-90": (0, 1, -1, 0, 0, width),
    }
    if mode not in values:
        raise PanelFrameError("unsupported frame page transform")
    return Affine2D(*values[mode])


def reversed_winding(matrix):
    return matrix.a * matrix.d - matrix.b * matrix.c < 0


def mapped_rect(rect, matrix):
    x, y, w, h = rect
    points = [matrix.apply(a, b) for a in (x, x + w) for b in (y, y + h)]
    xs, ys = zip(*points, strict=True)
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def mapped_side(side, matrix):
    normals = {"top": (0, -1), "right": (1, 0), "bottom": (0, 1), "left": (-1, 0)}
    x, y = normals[side]
    mapped = (matrix.a * x + matrix.b * y, matrix.c * x + matrix.d * y)
    return next(key for key, value in normals.items() if value == mapped)


def mapped_corner(corner, matrix):
    sides = corner.split("_")
    mapped = [mapped_side(side, matrix) for side in sides]
    vertical = next(side for side in mapped if side in {"top", "bottom"})
    horizontal = next(side for side in mapped if side in {"left", "right"})
    return f"{vertical}_{horizontal}"


def mapped_angle(angle, matrix):
    x, y = math.cos(angle), math.sin(angle)
    result = math.atan2(matrix.c * x + matrix.d * y, matrix.a * x + matrix.b * y)
    return result + math.pi if reversed_winding(matrix) else result


def _multiply(left, right):
    return Affine2D(
        left.a * right.a + left.b * right.c,
        left.a * right.b + left.b * right.d,
        left.c * right.a + left.d * right.c,
        left.c * right.b + left.d * right.d,
        *left.apply(right.tx, right.ty),
    )


def conjugate_transform(transform, matrix):
    if transform == LayerTransform.identity():
        return transform
    return LayerTransform.from_affine(
        _multiply(_multiply(matrix, transform.to_affine()), matrix.inverse())
    )


def transform_mask(image, mode):
    if mode in {"transpose", "anti_transpose"}:
        orientation = Qt.Orientation.Vertical if mode == "transpose" else Qt.Orientation.Horizontal
        return transform_mask(image.flipped(orientation), "rot90")
    if mode.startswith("flip_"):
        return image.flipped(
            {
                "flip_h": Qt.Orientation.Horizontal,
                "flip_v": Qt.Orientation.Vertical,
                "flip_hv": Qt.Orientation.Horizontal | Qt.Orientation.Vertical,
            }[mode]
        )
    matrix = QTransform()
    matrix.rotate({"rot90": 90, "rot-90": -90, "rot180": 180}[mode])
    return image.transformed(matrix, Qt.TransformationMode.FastTransformation)
