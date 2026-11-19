"""Command-backed pen/eraser strokes for an explicit raster layer."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.canvas_history_port import PageHistoryBundle
from loomstroke.core.commands import (
    BrushSettings,
    EraseCommand,
    LayerTransform,
    PaintCommand,
    StrokePoint,
)
from loomstroke.core.native_cmyk.tool_space import document_bounds, is_placed, stroke_region
from loomstroke.core.raster_edit_geometry import RasterEditGeometry

from .brush_settings import brush_id, color_rgba, validate_settings
from .editing_common import edit_operation, finite_number, invalid
from .types import McpStructuredError, ReasonCode

MAX_POINTS = 2048
MAX_STROKE_REGION_PIXELS = 4_194_304
MAX_STROKES = 64
MAX_BATCH_POINTS = 8192
MAX_BATCH_REGION_PIXELS = 16_777_216


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
    return bool(
        _submit_stroke_transaction(canvas, layer, label, lambda: int(bool(canvas.submit(command))))
    )


def _submit_stroke_transaction(
    canvas: Any, layer: Any, label: str, submit: Callable[[], int], *, batch: bool = False
) -> int:
    # QImage retains the old pixels by implicit sharing; history groups are
    # shallow snapshots. No extra history entry is written by this guard.
    before = QImage(layer.image) if layer.image is not None else None
    offsets = (layer.offset_x, layer.offset_y)
    native = layer.native_cmyk if batch else None
    native_version = native.version if native is not None else None
    inks_before = native.snapshot() if native is not None else None
    history = PageHistoryBundle(
        "", canvas._undo_stack.copy(), canvas._redo_stack.copy(), canvas._history()._restore_owner
    )
    new_operation = getattr(canvas, "_history_new_operation_callback", None)
    pushed = False

    def defer_new_operation() -> None:
        nonlocal pushed
        pushed = True

    def rollback() -> None:
        if native is not None and inks_before is not None and native.version != native_version:
            height, width = inks_before.alpha.shape
            native.patch((0, 0, width, height), inks_before)
        layer.image = before
        layer.offset_x, layer.offset_y = offsets
        canvas._txn_preimage_capture.reset()
        canvas._active_history_group = None
        canvas._in_transaction = False
        canvas._stroke_painting_active = False
        canvas._transaction_label = ""
        canvas.bump_layer_texture_versions([layer.id])
        canvas._history_attach_bundle(history)
        canvas._recompose()

    # A failing commit must not invalidate other pages' or the book's redo.
    if callable(new_operation):
        canvas._history_set_new_operation_callback(defer_new_operation)
    try:
        try:
            canvas.begin_transaction(label)
            changed = submit()
            if changed or batch:
                canvas.end_transaction()
        except Exception:
            rollback()
            raise
        if not changed and not batch:
            rollback()
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
        command, point_count, _ = _prepare_stroke(
            canvas, layer, tool_id, settings, arguments.get("points")
        )
        changed = command is not None and bool(
            _draw_on_target(
                canvas,
                layer,
                lambda: _submit_stroke(
                    canvas,
                    layer,
                    command,
                    "MCP Pen Stroke" if tool_id == "basic_pen" else "MCP Eraser Stroke",
                ),
                "The brush renderer could not complete the stroke.",
            )
        )
        return {
            "status": "completed" if changed else "no_change",
            "changed": changed,
            "toolId": tool_id,
            "layerId": layer.id,
            "pointCount": point_count,
            "undoable": changed,
        }


def _prepare_stroke(
    canvas: Any, layer: Any, tool_id: str, settings: Mapping[str, Any], point_values: Any
) -> tuple[PaintCommand | EraseCommand | None, int, float]:
    width, height = canvas.get_canvas_size()
    points = _points(point_values, width, height, settings)
    brush_keys = {"size", "opacity", "tone_range", "tone_gamma"}
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
        raise invalid("The stroke region exceeds the pixel limit; use smaller strokes.", "points")
    if not any(point.pressure > 0 for point in points) or (
        tool_id == "basic_pen"
        and (brush.opacity <= 0 or (len(brush.color) == 4 and brush.color[3] == 0))
    ):
        return None, len(points), region
    command_type = PaintCommand if tool_id == "basic_pen" else EraseCommand
    return command_type(points, brush, layer_id=layer.id), len(points), region


def _draw_on_target(canvas: Any, layer: Any, operation: Callable[[], int], error: str) -> int:
    if getattr(canvas, "_paint_tool_session", None) is not None:
        raise McpStructuredError(ReasonCode.CONFLICT, "Finish the current paint tool stroke first.")
    previous_id = canvas.current_layer().id
    previous_mask_edit = getattr(canvas, "_mask_edit_flag", False)
    try:
        canvas.set_active_layer(layer.id)
        return operation()
    except McpStructuredError:
        raise
    except Exception as exc:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, error, details={"errorType": type(exc).__name__}
        ) from exc
    finally:
        canvas.set_active_layer(previous_id)
        canvas._mask_edit_flag = previous_mask_edit


def _layer_stroke_revision(layer: Any) -> tuple[int, int, int, int, int | None]:
    return (
        layer.texture_version,
        layer.image.cacheKey() if layer.image is not None else 0,
        layer.offset_x,
        layer.offset_y,
        layer.native_cmyk.version if layer.native_cmyk is not None else None,
    )


def _placed_region_pixels(canvas: Any, layer: Any, command: PaintCommand | EraseCommand) -> int:
    native = layer.native_cmyk is not None
    placed = is_placed(layer) if native else layer.transform != LayerTransform.identity()
    if layer.image is None or not placed:
        return 0
    bounds = canvas._compute_stroke_bounds(command.points, command.brush)
    bounds = canvas._apply_selection_clip(bounds) if bounds is not None else None
    if bounds is None:
        return 0
    try:
        if native:
            bounds = bounds.intersected(document_bounds(layer))
            if bounds.isEmpty():
                return 0
            source = stroke_region(layer, bounds)
        else:
            source = RasterEditGeometry.capture(layer).source_rect(bounds)
    except (ValueError, OverflowError) as exc:
        raise invalid("The raster layer needs a finite, invertible placement.", "layerId") from exc
    return source.width() * source.height()


def _submit_batch(
    canvas: Any, layer: Any, commands: list[tuple[int, PaintCommand | EraseCommand]]
) -> int:
    dirty = QRect()

    def submit() -> int:
        nonlocal dirty
        changed_count = 0
        for index, command in commands:
            before = _layer_stroke_revision(layer)
            if not canvas.submit(command):
                if _layer_stroke_revision(layer) != before:
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED,
                        "The brush renderer rejected a stroke after changing the layer.",
                        details={"strokeIndex": index},
                    )
                continue
            changed_count += 1
            pending = canvas._txn_preimage_capture.pending_dirty_rect()
            if pending is not None:
                dirty = dirty.united(pending)
                # Preserve separate dirty patches inside the shared history group.
                # Distant strokes need no full-page rectangular before/after pair.
                canvas._txn_flush_deferred_paint()
        return changed_count

    changed_count = _submit_stroke_transaction(
        canvas, layer, "MCP Brush Strokes", submit, batch=True
    )
    if not dirty.isEmpty():
        # The RGB preimages have already been flushed, so publish their combined
        # content notification once. Native and placed strokes publish on commit.
        canvas._emit_layer_content_changed(layer, dirty, {"command": "paint"})
    return changed_count


async def draw_strokes(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    strokes = arguments.get("strokes")
    if not isinstance(strokes, list) or not 1 <= len(strokes) <= MAX_STROKES:
        raise invalid(f"'strokes' must contain between 1 and {MAX_STROKES} strokes.", "strokes")
    layer_id = arguments.get("layerId")
    async with edit_operation(service, name, arguments) as canvas:
        commands = []
        point_count = 0
        total_region = 0.0
        for index, stroke in enumerate(strokes):
            try:
                if not isinstance(stroke, Mapping) or set(stroke) - {
                    "toolId",
                    "points",
                    "settings",
                }:
                    raise invalid("Each stroke must contain toolId, points and settings.")
                tool_id = brush_id(stroke)
                settings = validate_settings(stroke.get("settings"), tool_id, stroke=True)
                layer = _target(canvas, layer_id, tool_id)
                command, points, region = _prepare_stroke(
                    canvas, layer, tool_id, settings, stroke.get("points")
                )
                if command is not None:
                    region = max(region, _placed_region_pixels(canvas, layer, command))
                    if region > MAX_STROKE_REGION_PIXELS:
                        raise invalid("The stroke exceeds the source region pixel limit.", "points")
            except McpStructuredError as exc:
                exc.details["strokeIndex"] = index
                raise
            point_count += points
            total_region += region
            if point_count > MAX_BATCH_POINTS or total_region > MAX_BATCH_REGION_PIXELS:
                raise invalid("The batch exceeds the total point or region pixel limit.", "strokes")
            if command is not None:
                commands.append((index, command))
        changed_count = (
            _draw_on_target(
                canvas,
                layer,
                lambda: _submit_batch(canvas, layer, commands),
                "The brush renderer could not complete the stroke batch.",
            )
            if commands
            else 0
        )
        return {
            "status": "completed" if changed_count else "no_change",
            "changed": bool(changed_count),
            "layerId": layer.id,
            "strokeCount": len(strokes),
            "changedStrokeCount": changed_count,
            "pointCount": point_count,
            "undoable": bool(changed_count),
        }
