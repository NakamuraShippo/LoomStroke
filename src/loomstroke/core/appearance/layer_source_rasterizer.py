"""Read a layer's raw pixels for appearance baking without editing its source."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtGui import QImage


def appearance_source_image(layer: Any, width: int, height: int) -> QImage | None:
    """Return raw raster pixels or rasterize committed vector objects.

    Layer effects, transforms, masks and opacity are outside the existing
    appearance bake operation. Object-level text layout and geometry are drawn
    by the normal core rasterizer. A scratch canvas excludes live previews and
    keeps caches, selections and source object metadata isolated from the bake.
    """
    if getattr(layer, "layer_type", "raster") != "vector":
        image = getattr(layer, "image", None)
        return image if isinstance(image, QImage) and not image.isNull() else None
    objects = list(getattr(layer, "vector_objects", ()) or ())
    if not objects:
        return None

    from ..canvas_layer_model import _Layer  # noqa: PLC0415
    from ..canvas_service_simple import SimpleCanvasService  # noqa: PLC0415

    scratch = SimpleCanvasService(width=max(1, width), height=max(1, height))
    source = _Layer(
        id=str(getattr(layer, "id", "appearance-source")),
        name="appearance-source",
        layer_type="vector",
        vector_objects=deepcopy(objects),
    )
    return scratch._rasterize_vector_layer(source)  # noqa: SLF001
