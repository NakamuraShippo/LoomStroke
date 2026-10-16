"""Vector snapshot helpers."""

from __future__ import annotations

from typing import Any

from loomstroke.core.canvas_models import VectorLayerSnapshot, VectorStateSnapshot


def build_vector_state_snapshot(canvas: Any) -> VectorStateSnapshot:
    layers: list[VectorLayerSnapshot] = []
    for layer in _iter_layers(getattr(canvas, "_layers", [])):
        if getattr(layer, "layer_type", "raster") != "vector":
            continue
        layers.append(
            VectorLayerSnapshot(
                layer_id=str(getattr(layer, "id", "")),
                vector_kind=str(getattr(layer, "vector_kind", "draw")),
                vector_objects=tuple(getattr(layer, "vector_objects", []) or ()),
            )
        )
    return VectorStateSnapshot(
        layers=tuple(layers),
        preview_layer_id=getattr(canvas, "_vector_preview_layer_id", None),
        preview_object=getattr(canvas, "_vector_preview", None),
        selection=getattr(canvas, "_vector_selection", None),
    )


def _iter_layers(layers: list[Any]) -> list[Any]:
    result: list[Any] = []
    for layer in layers:
        result.append(layer)
        children = getattr(layer, "children", None)
        if children:
            result.extend(_iter_layers(children))
    return result
