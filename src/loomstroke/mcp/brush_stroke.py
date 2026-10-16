"""Command-backed pen/eraser strokes for an explicit raster layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.core.canvas_history_port import PageHistoryBundle
from loomstroke.core.commands import BrushSettings, EraseCommand, PaintCommand, StrokePoint

from .brush_settings import brush_id, color_rgba, validate_settings
from .editing_common import edit_operation, finite_number, invalid
from .types import McpStructuredError, ReasonCode

MAX_POINTS = 2048
MAX_STROKE_REGION_PIXELS = 4_194_304


def _points(value: Any, width: int, height: int, settings: Mapping[str, Any]) -> list[StrokePoint]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_POINTS:
        raise invalid(f"'points' must contain between 1 and {MAX_POINTS} points.", "points")
    points = []
    allowed = {"x", "y", "pressure", "tilt_x", "tilt_y", "time"}
    for index, item in enumerate(value):
        field = f"points[{index}]"
        if not isinstance(item, Mapping) or set(item) - allowed:
            raise invalid("Each point must use the documented coordinate/tablet fields.", field)
        x = finite_number(item.get("x"), f"{field}.x", minimum=0)
        y = finite_number(item.get("y"), f"{field}.y", minimum=0)
        if x >= width or y >= height:
            raise invalid("Stroke coordinates must be inside the canvas.", field)
        pressure = finite_number(
            item.get("pressure", 1.0), f"{field}.pressure", minimum=0, maximum=1
        )
        tx = finite_number(item.get("tilt_x", 0), f"{field}.tilt_x", minimum=-60, maximum=60)
        ty = finite_number(item.get("tilt_y", 0), f"{field}.tilt_y", minimum=-60, maximum=60)
        timestamp = finite_number(item.get("time", 0), f"{field}.time", minimum=0, maximum=1e15)
        points.append(
            StrokePoint(
                x,
                y,
                pressure if settings.get("pressure_sensitive", True) else 1.0,
                tx if settings.get("tilt_angle", True) else 0.0,
                ty if settings.get("tilt_angle", True) else 0.0,
                timestamp=timestamp,
            )
        )
    return points


def _target(canvas: Any, layer_id: Any, tool_id: str) -> Any:
    if not isinstance(layer_id, str) or not layer_id or len(layer_id) > 256:
        raise invalid("'layerId' must be a non-empty layer ID.", "layerId")
    getter = getattr(canvas, "_get_layer_by_id", None)
    layer = getter(layer_id) if callable(getter) else None
    if layer is None:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "The stroke target layer was not found.",
            details={"layerId": layer_id},
        )
    if layer.locked or (tool_id == "basic_eraser" and layer.alpha_lock):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "The target layer prevents this stroke.",
            details={"layerId": layer_id},
        )
    parent_id = getattr(layer, "parent_id", None)
    seen = {layer_id}
    while parent_id is not None:
        parent = getter(parent_id)
        if parent is None or parent_id in seen:
            raise McpStructuredError(ReasonCode.CONFLICT, "The target layer tree is inconsistent.")
        if parent.locked:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Unlock the parent group before drawing in its contents.",
                details={"layerId": layer_id, "parentId": parent_id},
            )
        seen.add(parent_id)
        parent_id = parent.parent_id
    if layer.layer_type != "raster" or getattr(layer, "mask_edit_mode", False):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Drawing requires a raster layer outside layer-mask edit mode.",
            details={"layerId": layer_id},
        )
    return layer


def _submit_stroke(canvas: Any, layer: Any, command: Any, label: str) -> bool:
    # QImage retains the old pixels by implicit sharing; history groups are
    # shallow snapshots. No extra history entry is written by this guard.
    before = QImage(layer.image) if layer.image is not None else None
    offsets = (layer.offset_x, layer.offset_y)
    history = PageHistoryBundle("", canvas._undo_stack.copy(), canvas._redo_stack.copy())
    new_operation = getattr(canvas, "_history_new_operation_callback", None)
    pushed = False

    def defer_new_operation() -> None:
        nonlocal pushed
        pushed = True

    def rollback() -> None:
        layer.image = before
        layer.offset_x, layer.offset_y = offsets
        canvas._txn_preimage_capture.reset()
        canvas._active_history_group = None
        canvas.bump_layer_texture_versions([layer.id])
        pending = canvas._in_transaction
        canvas.end_transaction()
        if not pending:
            canvas._recompose()
        canvas._history_attach_bundle(history)

    # A failing commit must not invalidate other pages' or the book's redo.
    if callable(new_operation):
        canvas._history_set_new_operation_callback(defer_new_operation)
    try:
        canvas.begin_transaction(label)
        try:
            changed = bool(canvas.submit(command))
            if not changed:
                rollback()
                return False
            canvas.end_transaction()
        except Exception:
            rollback()
            raise
    finally:
        if callable(new_operation):
            canvas._history_set_new_operation_callback(new_operation)
    if pushed:
        new_operation()
    return changed


async def draw_stroke(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    tool_id = brush_id(arguments)
    settings = validate_settings(arguments.get("settings"), tool_id, stroke=True)
    async with edit_operation(service, name, arguments) as canvas:
        layer = _target(canvas, arguments.get("layerId"), tool_id)
        width, height = canvas.get_canvas_size()
        points = _points(arguments.get("points"), width, height, settings)
        brush_keys = {
            "size",
            "opacity",
            "tone_range",
            "tone_gamma",
        }
        brush_values = {key: value for key, value in settings.items() if key in brush_keys}
        if "color" in settings:
            color = color_rgba(settings["color"])
            if len(color) == 4:
                color = (*color[:3], round(color[3] * settings.get("opacity", 1)))
            brush_values["color"] = color
        brush = BrushSettings(**brush_values)
        xs, ys = [point.x for point in points], [point.y for point in points]
        region = min(width, max(xs) - min(xs) + 2 * brush.size + 4) * min(
            height, max(ys) - min(ys) + 2 * brush.size + 4
        )
        if region > MAX_STROKE_REGION_PIXELS:
            raise invalid(
                "The stroke region exceeds the pixel limit; use smaller strokes.",
                "points",
            )
        changed = False
        if any(point.pressure > 0 for point in points) and (
            tool_id == "basic_eraser"
            or (brush.opacity > 0 and (len(brush.color) < 4 or brush.color[3] > 0))
        ):
            command_type = PaintCommand if tool_id == "basic_pen" else EraseCommand
            command = command_type(points, brush, layer_id=layer.id)
            previous_id = canvas.current_layer().id
            previous_mask_edit = getattr(canvas, "_mask_edit_flag", False)
            try:
                canvas.set_active_layer(layer.id)
                changed = _submit_stroke(
                    canvas,
                    layer,
                    command,
                    "MCP Pen Stroke" if tool_id == "basic_pen" else "MCP Eraser Stroke",
                )
            except McpStructuredError:
                raise
            except Exception as exc:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED,
                    "The brush renderer could not complete the stroke.",
                    details={"errorType": type(exc).__name__},
                ) from exc
            finally:
                canvas.set_active_layer(previous_id)
                canvas._mask_edit_flag = previous_mask_edit
        return {
            "status": "completed" if changed else "no_change",
            "changed": changed,
            "toolId": tool_id,
            "layerId": layer.id,
            "pointCount": len(points),
            "undoable": changed,
        }
