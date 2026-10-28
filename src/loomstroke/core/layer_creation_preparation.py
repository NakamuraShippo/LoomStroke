"""Prepare a single new layer or duplicate payload before publishing it."""

from __future__ import annotations

import copy
import time
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage


def prepare_layer(
    canvas: Any, make_layer: Any, name: str, layer_type: str, vector_kind: str
) -> Any:
    kind = layer_type if layer_type in {"raster", "vector", "mask", "3d"} else "raster"
    native = getattr(canvas, "_native_cmyk_service", None)
    if native is None and any(
        getattr(layer, "native_cmyk", None) is not None
        for layer in canvas._layer_tree().flatten_layers()
    ):
        native = canvas.native_cmyk()
    if (
        native is not None
        and native.profile() is not None
        and kind not in ("raster", "mask", "vector")
    ):
        raise ValueError("Native CMYK requires raster layers")
    image = None
    if kind != "vector":
        image = QImage(canvas._width, canvas._height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
    layer = make_layer(
        id=f"layer-{len(canvas._layers)}-{time.time_ns()}",
        name=name,
        visible=True,
        opacity=1.0,
        image=image,
        layer_type=kind,
        vector_kind=vector_kind if kind == "vector" else "draw",
    )
    if native is not None:
        native.initialize_layer(layer)
    return layer


def duplicate_payload(source: Any, default_blend_mode: Any) -> dict[str, Any]:
    """Preserve the standard duplicate's existing fields; all copies happen first."""
    values = {
        "visible": getattr(source, "visible", True),
        "opacity": float(getattr(source, "opacity", 1.0)),
        "locked": bool(getattr(source, "locked", False)),
        "blend_mode": getattr(source, "blend_mode", default_blend_mode),
        "offset_x": int(getattr(source, "offset_x", 0)),
        "offset_y": int(getattr(source, "offset_y", 0)),
    }
    image = getattr(source, "image", None)
    if image is not None and hasattr(image, "copy"):
        values["image"] = image.copy()
    native = getattr(source, "native_cmyk", None)
    if native is not None:
        values["native_cmyk"] = native.copy()
    vectors = getattr(source, "vector_objects", None)
    if vectors:
        values["vector_objects"] = copy.deepcopy(vectors)
    metadata = getattr(source, "metadata", None)
    if isinstance(metadata, dict):
        values["metadata"] = copy.deepcopy(metadata)
    return values
