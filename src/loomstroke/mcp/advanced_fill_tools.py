"""Large-image fill operations publish a detached worker result as one command."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.commands import UpdateSelectionMaskCommand

from .advanced_fill_render import render_fill as _render
from .brush_tools import _schema
from .composite_snapshot import composite_image
from .editing_common import current_revision, edit_operation, invalid
from .fill_preparation import ensure_edit_unchanged
from .fill_tool_arguments import FILL_SCHEMA, PATTERN_SCHEMA, color, integer, point
from .fill_tools import _editable_layer, _pattern
from .layer_edit_command import LayerEditCommand
from .types import McpStructuredError, McpToolSpec, ReasonCode

_PROPERTIES = {**FILL_SCHEMA["properties"], **PATTERN_SCHEMA["properties"]}
_PROPERTIES.update(
    {
        "connectivity": {"enum": [4, 8]},
        "referenceMode": {"enum": ["layer", "merged"]},
        "rotation": {"type": "number"},
    }
)
TOOLS = (
    McpToolSpec(
        "loomstroke_fill_advanced",
        "Flood/color/pattern fill up to 16,777,216 pixels using layer or visible-composite "
        "reference, 4/8 connectivity and arbitrary pattern rotation. "
        "CPU preparation/blending stays on a worker; "
        "publish one undoable command. Supply color or one pattern source.",
        _schema(_PROPERTIES, ["layerId", "seed"], edit=True),
    ),
)


def _array(image: QImage) -> np.ndarray:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.width(), 4).copy()


def _arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "seed": tuple(map(int, point(arguments["seed"], "seed", integer=True))),
        "tolerance": integer(arguments.get("tolerance", 8), "tolerance", 0, 255),
        "gapClose": integer(arguments.get("gapClose", 0), "gapClose", 0, 10),
        "connectivity": arguments.get("connectivity", 8),
        "referenceMode": arguments.get("referenceMode", "layer"),
        "target": arguments.get("target", "active"),
        "layerName": arguments.get("layerName", "Fill"),
        "rotation": arguments.get("rotation", 0),
        "scale": arguments.get("scale", 1),
        "offset": tuple(
            map(int, point(arguments.get("offset", {"x": 0, "y": 0}), "offset", integer=True))
        ),
    }
    sources = [key for key in ("color", "patternAssetId", "patternPreset") if key in arguments]
    if len(sources) != 1:
        raise invalid("Provide exactly one color, patternAssetId or patternPreset.")
    values.update({key: arguments[key] for key in sources})
    if "color" in values:
        values["color"] = color(values["color"], "color")
        if any(key in arguments for key in ("rotation", "scale", "offset")):
            raise invalid("Rotation, scale and offset require a pattern source.")
    return values


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    parsed = _arguments(arguments)
    async with edit_operation(service, name, arguments) as canvas:
        layer = _editable_layer(canvas, arguments["layerId"])
        width, height = canvas.get_canvas_size()
        if width * height > 16_777_216 or layer.image.width() * layer.image.height() > 16_777_216:
            raise invalid("Fill canvas/reference may contain at most 16,777,216 pixels.")
        x, y = parsed["seed"]
        if not 0 <= x < width or not 0 <= y < height:
            raise invalid("The fill seed lies outside the canvas.")
        revision = current_revision(service)
        target = _array(layer.image)
        if parsed["referenceMode"] == "merged":
            reference = _array(await composite_image(service))
            reference_offset = (0, 0)
        else:
            reference, reference_offset = target.copy(), (int(layer.offset_x), int(layer.offset_y))
        mask, bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        selection = (
            UpdateSelectionMaskCommand._mask_to_numpy(mask, width, height).copy()
            if mask is not None
            else None
        )
        pattern = _pattern(service, parsed) if "color" not in parsed else None
        snapshot = {
            "canvasSize": (width, height),
            "layerId": layer.id,
            "reference": reference,
            "referenceOffset": reference_offset,
            "target": target,
            "targetOffset": (int(layer.offset_x), int(layer.offset_y)),
            "selection": selection,
            "selectionBounds": bounds,
            "alphaLock": layer.alpha_lock,
        }
        try:
            result = await asyncio.to_thread(_render, snapshot, parsed, pattern)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
        ensure_edit_unchanged(service, canvas, layer, revision)
        _editable_layer(canvas, layer.id)
        if not result["changed"]:
            return {"changed": False, "layerId": layer.id}
        rgba = result["rgba"]
        image = QImage(
            rgba.data,
            rgba.shape[1],
            rgba.shape[0],
            rgba.strides[0],
            QImage.Format.Format_RGBA8888,
        ).copy()

        def publish(owner: Any) -> dict[str, Any]:
            target_layer = (
                owner._get_layer_by_id(owner.add_layer(parsed["layerName"]).id)
                if parsed["target"] == "new_layer"
                else layer
            )
            target_layer.image = image
            target_layer.offset_x, target_layer.offset_y = result["offset"]
            return {"layerId": target_layer.id}

        command = LayerEditCommand([layer.id], publish)
        if not canvas.submit(command):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "The fill result could not be published."
            )
        return {
            "changed": True,
            "layerId": command.result["layerId"],
            "revision": current_revision(service),
            "undoable": True,
        }
