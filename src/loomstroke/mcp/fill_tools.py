"""Command-backed MCP fill operations."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.commands import ApplyImageCommand
from loomstroke.core.fill.commands_fill import (
    FloodFillCommand,
    FloodFillToNewLayerCommand,
    PatternFillCommand,
)
from loomstroke.core.layer_tree_service import LayerTreeQueryService
from loomstroke.plugins.basic_gradient.gradient_command import _render_gradient
from loomstroke.plugins.pattern_fill.pattern_fill_tool import PatternFillPlugin

from .editing_common import current_revision, edit_operation, invalid
from .fill_preparation import ensure_edit_unchanged, prepare_fill
from .fill_tool_arguments import FILL_SCHEMA, GRADIENT_SCHEMA, PATTERN_SCHEMA, parse_arguments
from .types import McpStructuredError, McpToolSpec, ReasonCode

_MAX_RENDER_PIXELS = 16_777_216
TOOLS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        "loomstroke_fill",
        "Flood fill a raster layer at a canvas-coordinate seed; honor selection and alpha lock. "
        "Color is #RRGGBB, #RRGGBBAA, or RGBA8. "
        "Supports active/new_layer targets and layer reference, connectivity=8 only. "
        "Canvas and reference layer must each be at most 1,048,576 pixels.",
        FILL_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_draw_gradient",
        "Draw a linear/radial gradient using 2-5 ordered stops at positions 0 through 1. "
        "Honor selection, layer offset, alpha lock, and undo; render at most 16,777,216 pixels.",
        GRADIENT_SCHEMA,
    ),
    McpToolSpec(
        "loomstroke_fill_pattern",
        "Flood fill using one registered PNG asset or built-in pattern preset. "
        "Honor selection/alpha lock; scale 0.1-10, integer offset, rotation=0 only. "
        "Canvas/reference: at most 1,048,576 pixels each; connectivity=8 only. "
        "The scaled pattern must not exceed 16,777,216 pixels.",
        PATTERN_SCHEMA,
    ),
)


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_arguments(name, arguments)
    async with edit_operation(service, name, arguments) as canvas:
        layer = _editable_layer(canvas, parsed["layerId"])
        if name == "loomstroke_draw_gradient":
            return await _gradient(service, canvas, layer, parsed)
        seed_x, seed_y = map(int, parsed["seed"])
        if not (0 <= seed_x < canvas._width and 0 <= seed_y < canvas._height):
            raise invalid("'seed' must be inside the canvas.", "seed")
        command: Any
        if name == "loomstroke_fill":
            command_type = (
                FloodFillToNewLayerCommand if parsed["target"] == "new_layer" else FloodFillCommand
            )
            options = {"layer_name": parsed["layerName"]} if parsed["target"] == "new_layer" else {}
            command = command_type(
                layer.id,
                seed_x,
                seed_y,
                tuple(channel / 255 for channel in parsed["color"]),
                parsed["tolerance"],
                parsed["gapClose"],
                connectivity=parsed["connectivity"],
                **options,
            )
        else:
            pattern = _pattern(service, parsed)
            command = PatternFillCommand(
                layer.id,
                seed_x,
                seed_y,
                pattern,
                parsed["tolerance"],
                parsed["gapClose"],
                pattern_scale=parsed["scale"],
                pattern_offset=tuple(map(int, parsed["offset"])),
                connectivity=parsed["connectivity"],
            )
        if not await prepare_fill(service, canvas, layer, command):
            return _result(service, name, layer.id, False)
        _editable_layer(canvas, layer.id)
        applied = bool(canvas.submit(command))
        target_id = command.target_layer_id or layer.id
        return _result(service, name, target_id, applied)


def _editable_layer(canvas: Any, layer_id: str) -> Any:
    tree = LayerTreeQueryService(canvas)
    layer = tree.find_layer_by_id_tree(layer_id)
    if layer is None:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND, "Layer was not found.", details={"layerId": layer_id}
        )
    ancestor = layer
    while ancestor is not None:
        if ancestor.locked:
            raise McpStructuredError(
                ReasonCode.CONFLICT, "The target layer or its parent is locked."
            )
        ancestor = tree.find_layer_by_id_tree(ancestor.parent_id) if ancestor.parent_id else None
    if layer.layer_type != "raster" or layer.mask_edit_mode:
        raise invalid("The target must be a raster layer outside mask edit mode.", "layerId")
    if layer.image is None or layer.image.isNull():
        raise invalid("The target layer has no raster image.", "layerId")
    return layer


async def _gradient(
    service: Any, canvas: Any, layer: Any, parsed: dict[str, Any]
) -> dict[str, Any]:
    width, height = layer.image.width(), layer.image.height()
    if width * height > _MAX_RENDER_PIXELS:
        raise invalid("Gradient target exceeds the 16,777,216-pixel limit.", "layerId")
    before_revision = current_revision(service)
    x0, y0 = parsed["start"]
    x1, y1 = parsed["end"]
    stops = parsed["stops"]
    gradient = await asyncio.to_thread(
        _render_gradient,
        width,
        height,
        x0 - layer.offset_x,
        y0 - layer.offset_y,
        x1 - layer.offset_x,
        y1 - layer.offset_y,
        stops[0][1],
        stops[-1][1],
        parsed["type"],
        parsed["dither"],
        stops=stops[1:-1],
    )
    ensure_edit_unchanged(service, canvas, layer, before_revision)
    _editable_layer(canvas, layer.id)
    command = ApplyImageCommand(
        gradient,
        position=(layer.offset_x, layer.offset_y),
        metadata={"target_layer_id": layer.id, "source": "mcp_gradient"},
    )
    applied = bool(canvas.submit(command))
    return _result(service, "loomstroke_draw_gradient", layer.id, applied)


def _pattern(service: Any, parsed: dict[str, Any]) -> np.ndarray:
    if "patternPreset" in parsed:
        pattern = PatternFillPlugin()._patterns[parsed["patternPreset"]].copy()
    else:
        image = service._qimage_from_asset(parsed["patternAssetId"])
        if image.width() * image.height() > _MAX_RENDER_PIXELS:
            raise invalid("Pattern asset exceeds the 16,777,216-pixel limit.", "patternAssetId")
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pattern = (
            np.frombuffer(image.constBits(), dtype=np.uint8)
            .reshape(image.height(), image.width(), 4)
            .copy()
        )
    height, width = pattern.shape[:2]
    scaled_width, scaled_height = (
        max(1, int(width * parsed["scale"])),
        max(1, int(height * parsed["scale"])),
    )
    if scaled_width * scaled_height > _MAX_RENDER_PIXELS:
        raise invalid("Scaled pattern exceeds the 16,777,216-pixel limit.", "scale")
    return pattern


def _result(service: Any, name: str, layer_id: str, applied: bool) -> dict[str, Any]:
    return {
        "status": "applied" if applied else "no_change",
        "operation": name,
        "layerId": layer_id,
        "revision": current_revision(service),
    }
