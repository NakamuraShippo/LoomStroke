"""Borrow confirmed layer inputs for a synchronous preflight scan.

Only scalar facts survive the scan. Images and masks are not copied into
the persistence representation, and image-less layers allocate no canvas.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ..core.layer_transform_metadata import capture_layer_transform_metadata


def live_preflight_layers(layers: list[Any]) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=layer.id,
            name=layer.name,
            visible=bool(layer.visible),
            opacity=float(layer.opacity),
            locked=bool(layer.locked),
            layer_type=layer.layer_type,
            image=layer.image,
            vector_objects=list(getattr(layer, "vector_objects", ()) or ()),
            children=live_preflight_layers(getattr(layer, "children", ()) or ()),
            effect_type=getattr(layer, "effect_type", None),
            position=(layer.offset_x, layer.offset_y),
            metadata=capture_layer_transform_metadata(layer),
        )
        for layer in layers
    ]
