"""Prepare layer/vector/selection transforms without mutating live state."""

from __future__ import annotations

import math
from copy import deepcopy

from loomstroke.core.command_layer_access import _layer_transform
from loomstroke.core.commands import LayerTransform, TransformLayerCommand
from loomstroke.core.geometry import Affine2D
from loomstroke.plugins.shape_manager.ops.selection_ops import (
    rotate_object,
    scale_object,
    translate_object,
)
from loomstroke.plugins.text_tool.common import extract_text_content
from loomstroke.plugins.text_tool.editing_metadata import replace_edited_text
from loomstroke.plugins.vector_tools.common import compute_vector_bounds, ensure_vector_element_ids

from .editing_common import finite_number, invalid


def pair(value, name, default):
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise invalid(f"'{name}' requires two numbers.", name)
    return tuple(finite_number(part, name, minimum=-1e6, maximum=1e6) for part in value)


def parameters(args, bounds):
    x0, y0, x1, y1 = bounds
    width, height = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    translation = pair(args.get("translation"), "translation", (0.0, 0.0))
    if "position" in args:
        x, y = pair(args["position"], "position", (x0, y0))
        translation = (x - x0 + translation[0], y - y0 + translation[1])
    scale = pair(args.get("scale"), "scale", (1.0, 1.0))
    if "size" in args:
        target_width, target_height = pair(args["size"], "size", (width, height))
        if target_width <= 0 or target_height <= 0:
            raise invalid("Transform size must be positive.", "size")
        scale = (scale[0] * target_width / width, scale[1] * target_height / height)
    sx, sy = scale
    sx *= -1 if args.get("flipHorizontal", False) else 1
    sy *= -1 if args.get("flipVertical", False) else 1
    if abs(sx) < 1e-6 or abs(sy) < 1e-6 or max(abs(sx), abs(sy)) > 1000:
        raise invalid("Transform scale must be nonzero and at most 1000.", "scale")
    pivot = pair(args.get("pivot"), "pivot", ((x0 + x1) / 2, (y0 + y1) / 2))
    angle = finite_number(
        args.get("rotationDegrees", 0), "rotationDegrees", minimum=-36000, maximum=36000
    )
    return translation, (sx, sy), pivot, math.radians(angle)


def affine(translation, scale, pivot, angle):
    dx, dy = translation
    sx, sy = scale
    px, py = pivot
    cos, sin = math.cos(angle), math.sin(angle)
    a, b, c, d = cos * sx, -sin * sy, sin * sx, cos * sy
    return Affine2D(a, b, c, d, dx + px - a * px - b * py, dy + py - c * px - d * py)


def transformed_object(obj, args):
    bounds = compute_vector_bounds(obj, include_stroke=False)
    if bounds is None:
        raise invalid("The object has no transform bounds.", "objectId")
    translation, scale, pivot, angle = parameters(args, bounds)
    if isinstance(obj.meta.get("text_tool"), dict):
        if scale[0] < 0 or scale[1] < 0:
            raise invalid(
                "Editable text glyph mirror is unsupported by the existing text renderer; "
                "rasterize the text layer before mirroring.",
                "flipHorizontal",
            )
        # The existing text transform controller changes the saved frame,
        # retaining all annotation/range metadata through replace_edited_text.
        payload = obj.meta["text_tool"]
        frame = payload.get("frame", payload)
        x, y, width, height = (
            float(frame.get(key, 1 if key in {"width", "height"} else 0))
            for key in ("x", "y", "width", "height")
        )
        transform = affine(translation, scale, pivot, angle)
        cx, cy = transform.apply(x + width / 2, y + height / 2)
        rect = (
            cx - width * abs(scale[0]) / 2,
            cy - height * abs(scale[1]) / 2,
            width * abs(scale[0]),
            height * abs(scale[1]),
        )
        rotation = float(
            payload.get("transform", {}).get("rotation_deg", payload.get("rotation_deg", 0))
        ) + math.degrees(angle)
        result = replace_edited_text(
            obj, extract_text_content(payload), rect=rect, rotation_deg=rotation
        )
    else:
        result = scale_object(
            deepcopy(obj), pivot, *scale, keep_stroke_width=args.get("keepStrokeWidth", True)
        )
        result = rotate_object(result, pivot, angle)
        result = translate_object(result, *translation)
    return ensure_vector_element_ids(result)


def layer_bounds(layer):
    transform = _layer_transform(layer).to_affine()
    width, height = layer.image.width(), layer.image.height()
    corners = (
        transform.apply(0, 0),
        transform.apply(width, 0),
        transform.apply(0, height),
        transform.apply(width, height),
    )
    return (
        min(point[0] for point in corners),
        min(point[1] for point in corners),
        max(point[0] for point in corners),
        max(point[1] for point in corners),
    )


def layer_command(layer, args):
    current = _layer_transform(layer)
    bounds = layer_bounds(layer)
    translation, scale, pivot, angle = parameters(args, bounds)
    delta = affine(translation, scale, pivot, angle)
    old = current.to_affine()
    composed = Affine2D(
        delta.a * old.a + delta.b * old.c,
        delta.a * old.b + delta.b * old.d,
        delta.c * old.a + delta.d * old.c,
        delta.c * old.b + delta.d * old.d,
        delta.a * old.tx + delta.b * old.ty + delta.tx,
        delta.c * old.tx + delta.d * old.ty + delta.ty,
    )
    column_dot = composed.a * composed.b + composed.c * composed.d
    column_scale = math.hypot(composed.a, composed.c) * math.hypot(composed.b, composed.d)
    if abs(column_dot) > 1e-9 * max(column_scale, 1e-12):
        raise invalid(
            "The requested transform requires shear, which existing raster layer transforms "
            "cannot store; rasterize the transformed layer before non-uniform scaling.",
            "scale",
        )
    return TransformLayerCommand(LayerTransform.from_affine(composed), layer_id=layer.id)
