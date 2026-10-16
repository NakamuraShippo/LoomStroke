"""Shared CanvasChangeset shape geometry backed by the existing Shape Manager."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loomstroke.plugins.vector_tools.common import (
    Anchor,
    VectorObject,
    VectorPath,
    VectorStyle,
    normalize_rgb,
)
from loomstroke.plugins.vector_tools.parametric_shapes import (
    build_parametric_shape_object,
    get_basic_shape_definition,
    list_basic_shape_definitions,
)


def shape_type_from_operation(operation: Mapping[str, Any]) -> str:
    return str(operation.get("shapeType") or "rect")


def supported_shape_types() -> tuple[str, ...]:
    # ``focus_lines`` is the existing Shape Manager compatibility name.
    return ("rect", "focus_lines", *(item.shape_id for item in list_basic_shape_definitions()))


def is_supported_shape_type(shape_type: str) -> bool:
    return shape_type == "rect" or get_basic_shape_definition(shape_type) is not None


def build_canvas_shape(operation: Mapping[str, Any], *, object_id: str) -> VectorObject:
    """Build the requested shape while preserving the legacy rectangle contract."""

    shape_type = shape_type_from_operation(operation)
    if not is_supported_shape_type(shape_type):
        raise ValueError(f"unsupported_shape_type:{shape_type}")
    x, y, width, height = _shape_rect(operation.get("rect"))
    style = operation.get("style")
    style = dict(style) if isinstance(style, Mapping) else {}
    fill = normalize_rgb(style.get("fillColor"))
    vector_style = VectorStyle(
        stroke_color=normalize_rgb(style.get("strokeColor") or style.get("color")) or (17, 17, 17),
        stroke_width=_number(style.get("strokeWidth"), 2.0),
        fill_enabled=fill is not None,
        fill_color=fill,
        join="round",
        cap="round",
    )
    meta = {
        "canvas_agent": {
            "schemaVersion": "canvas_agent_vector_object.v1",
            "role": "shape",
            "operationId": operation.get("operationId"),
            "shapeType": shape_type,
        }
    }
    if shape_type == "rect":
        return VectorObject(
            id=object_id,
            paths=[
                VectorPath(
                    anchors=[
                        Anchor((x, y)),
                        Anchor((x + width, y)),
                        Anchor((x + width, y + height)),
                        Anchor((x, y + height)),
                    ],
                    closed=True,
                )
            ],
            style=vector_style,
            meta=meta,
        )
    obj = build_parametric_shape_object(
        shape_type,
        (x, y, x + width, y + height),
        style=vector_style,
        object_id=object_id,
        extra_meta=meta,
    )
    if obj is None:
        raise ValueError(f"shape_geometry_unavailable:{shape_type}")
    return obj


def _shape_rect(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, Mapping):
        parts = (value.get("x"), value.get("y"), value.get("width"), value.get("height"))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        parts = tuple(value[:4]) if len(value) >= 4 else ()
    else:
        parts = ()
    if len(parts) != 4:
        return (0.0, 0.0, 1.0, 1.0)
    return (
        _number(parts[0], 0.0),
        _number(parts[1], 0.0),
        max(1.0, _number(parts[2], 1.0)),
        max(1.0, _number(parts[3], 1.0)),
    )


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
