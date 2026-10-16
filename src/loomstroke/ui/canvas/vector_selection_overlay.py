"""Screen-space vector selection / editing overlay.

Retained vector spec §15: anchor, handle, transform-bbox and marquee
visuals are separated from the base vector render.  They are drawn here in
device (widget) space so that

- selected vector layers keep the direct GPU render path (sharp at any
  zoom) instead of falling back to canvas-resolution rasterization, and
- markers keep a fixed on-screen size regardless of the zoom factor.

The caller (``CanvasWidget._draw_overlays``) invokes
:func:`draw_vector_selection_overlay` with a painter whose current
transform maps canvas coordinates to overlay-widget coordinates (native
offset included).  All positions are mapped through that transform and
then drawn with an identity painter transform.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF, QTransform

from loomstroke.core.vector_text_render_service import VectorTextRenderService

# Marker geometry in device pixels.  Values match the legacy raster-baked
# drawing at 100% zoom (vector_text_render_primitives.py).
ANCHOR_RADIUS = 4.0
HANDLE_DOT_RADIUS = 2.0
CORNER_HANDLE_SIZE = 8.0
EDGE_HANDLE_SIZE = 7.0
ROTATION_HANDLE_OFFSET = 12.0
SHAPE_HANDLE_SIZE = 8.0
SHAPE_HANDLE_SIZE_ACTIVE = 10.0

_COORD_LIMIT = 1_000_000.0

_OVERLAY_BLUE = QColor(20, 120, 255)
_ANCHOR_BOX_BORDER = QColor(20, 120, 255, 180)
_ANCHOR_BOX_FILL = QColor(20, 120, 255, 24)


def draw_vector_selection_overlay(painter: QPainter, canvas: Any) -> None:
    """Draw vector selection/editing UI in screen space.

    ``painter`` must carry the canvas→widget transform; the overlay maps
    canvas coordinates through it and renders markers at fixed pixel size.
    """

    selection = getattr(canvas, "_vector_selection", None)
    if not isinstance(selection, dict):
        selection = None
    preview_obj = getattr(canvas, "_vector_preview", None)
    if selection is None and preview_obj is None:
        return

    transform = painter.transform()
    painter.save()
    painter.setTransform(QTransform())
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        _draw_selection_visuals(painter, canvas, transform, selection, preview_obj)
    finally:
        painter.restore()


def _draw_selection_visuals(
    painter: QPainter,
    canvas: Any,
    transform: QTransform,
    selection: dict[str, Any] | None,
    preview_obj: Any,
) -> None:
    drawn_boxes: set[tuple[int, int, int, int]] = set()

    for layer_id, vec in _control_candidates(canvas, selection, preview_obj):
        if VectorTextRenderService.extract_text_payload(vec) is None:
            _draw_object_anchors(painter, transform, vec, layer_id, selection, preview_obj)
        if selection and selection.get("id") == getattr(vec, "id", None):
            bounds = _selected_object_bounds(selection, vec)
            if bounds:
                _draw_transform_box(painter, transform, bounds, drawn_boxes)
            handles = selection.get("shape_handles")
            if handles:
                _draw_shape_handles(painter, transform, handles)

    if selection:
        anchor_box = VectorTextRenderService.coerce_overlay_bounds(
            selection.get("anchor_box_bounds")
        )
        if anchor_box:
            _draw_anchor_box(painter, transform, anchor_box)
        if selection.get("items"):
            bounds = VectorTextRenderService.coerce_overlay_bounds(
                selection.get("transform_bounds")
            )
            if bounds:
                _draw_transform_box(painter, transform, bounds, drawn_boxes)


def _control_candidates(
    canvas: Any,
    selection: dict[str, Any] | None,
    preview_obj: Any,
) -> list[tuple[str, Any]]:
    """Return ``(layer_id, vector_object)`` pairs that may show controls."""

    candidates: list[tuple[str, Any]] = []
    seen_ids: set[Any] = set()

    if preview_obj is not None:
        preview_layer_id = getattr(canvas, "_vector_preview_layer_id", None)
        candidates.append((str(preview_layer_id or ""), preview_obj))
        preview_id = getattr(preview_obj, "id", None)
        if preview_id is not None:
            seen_ids.add(preview_id)

    if selection:
        selected_id = selection.get("id")
        if selected_id is not None and selected_id not in seen_ids:
            found = _find_selected_object(canvas, selection, selected_id)
            if found is not None:
                candidates.append(found)

    return candidates


def _find_selected_object(
    canvas: Any,
    selection: dict[str, Any],
    selected_id: Any,
) -> tuple[str, Any] | None:
    layer_id = selection.get("layer_id")
    layers: list[Any]
    if layer_id:
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        layer = finder(str(layer_id)) if callable(finder) else None
        if layer is None:
            return None
        layers = [layer]
    else:
        layers = _flatten_layers(getattr(canvas, "_layers", []) or [])
    for layer in layers:
        if not getattr(layer, "visible", True):
            continue
        for vec in getattr(layer, "vector_objects", []) or []:
            if getattr(vec, "id", None) == selected_id:
                return (str(getattr(layer, "id", "")), vec)
    return None


def _flatten_layers(layers: list[Any]) -> list[Any]:
    result: list[Any] = []
    stack = list(layers)
    while stack:
        layer = stack.pop()
        result.append(layer)
        stack.extend(getattr(layer, "children", []) or [])
    return result


def _map_point(transform: QTransform, x: float, y: float) -> QPointF | None:
    point = transform.map(QPointF(float(x), float(y)))
    if abs(point.x()) > _COORD_LIMIT or abs(point.y()) > _COORD_LIMIT:
        return None
    return point


def _draw_object_anchors(
    painter: QPainter,
    transform: QTransform,
    vec: Any,
    layer_id: str,
    selection: dict[str, Any] | None,
    preview_obj: Any,
) -> None:
    paths = getattr(vec, "paths", None) or []
    for path_idx, path in enumerate(paths):
        if not VectorTextRenderService.should_draw_vector_path_controls(
            vec=vec,
            layer_id=layer_id,
            path_idx=path_idx,
            selection=selection,
            preview_obj=preview_obj,
        ):
            continue
        anchors = getattr(path, "anchors", None) or []
        for anchor_idx, anchor in enumerate(anchors):
            selected = bool(
                selection
                and selection.get("id") == getattr(vec, "id", None)
                and selection.get("path") == path_idx
                and selection.get("anchor") == anchor_idx
                and selection.get("layer_id") in (None, layer_id)
            )
            _draw_anchor_marker(painter, transform, anchor, selected)


def _draw_anchor_marker(
    painter: QPainter,
    transform: QTransform,
    anchor: Any,
    selected: bool,
) -> None:
    center = _map_point(transform, anchor.pos[0], anchor.pos[1])
    if center is None:
        return
    for handle in (anchor.h_in, anchor.h_out):
        if not handle:
            continue
        tip = _map_point(
            transform,
            anchor.pos[0] + handle[0],
            anchor.pos[1] + handle[1],
        )
        if tip is None:
            continue
        painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.PenStyle.DotLine))
        painter.drawLine(center, tip)
        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(tip, HANDLE_DOT_RADIUS, HANDLE_DOT_RADIUS)
    marker_color = _OVERLAY_BLUE if selected else QColor(255, 255, 255)
    border = _OVERLAY_BLUE if selected else QColor(50, 50, 50)
    painter.setBrush(QBrush(marker_color))
    painter.setPen(QPen(border, 1))
    painter.drawEllipse(center, ANCHOR_RADIUS, ANCHOR_RADIUS)


def _selected_object_bounds(
    selection: dict[str, Any],
    vec: Any,
) -> tuple[float, float, float, float] | None:
    text_payload = VectorTextRenderService.extract_text_payload(vec)
    if text_payload is not None:
        if not selection.get("show_transform_overlay", True):
            return None
        fallback = VectorTextRenderService.vector_bbox(
            vec
        ) or VectorTextRenderService.text_bbox_from_payload(text_payload)
        if fallback is None:
            return None
        return VectorTextRenderService.coerce_overlay_bounds(
            selection.get("transform_bounds"),
            fallback=fallback,
        )
    bounds = VectorTextRenderService.coerce_overlay_bounds(selection.get("transform_bounds"))
    if bounds:
        return bounds
    return VectorTextRenderService.vector_bbox(vec)


def _draw_transform_box(
    painter: QPainter,
    transform: QTransform,
    bounds: tuple[float, float, float, float],
    drawn_boxes: set[tuple[int, int, int, int]],
) -> None:
    key = tuple(round(value) for value in bounds)
    if key in drawn_boxes:
        return
    drawn_boxes.add(key)  # type: ignore[arg-type]

    min_x, min_y, max_x, max_y = bounds
    corners = [
        _map_point(transform, min_x, min_y),
        _map_point(transform, max_x, min_y),
        _map_point(transform, max_x, max_y),
        _map_point(transform, min_x, max_y),
    ]
    if any(point is None for point in corners):
        return
    points = [point for point in corners if point is not None]

    painter.setPen(QPen(_OVERLAY_BLUE, 1, Qt.PenStyle.DotLine))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPolygon(QPolygonF(points))

    painter.setBrush(QBrush(QColor(255, 255, 255)))
    half_corner = CORNER_HANDLE_SIZE * 0.5
    for point in points:
        painter.drawRect(
            QRectF(
                point.x() - half_corner,
                point.y() - half_corner,
                CORNER_HANDLE_SIZE,
                CORNER_HANDLE_SIZE,
            )
        )
    edge_mids = [
        (points[0] + points[1]) * 0.5,
        (points[1] + points[2]) * 0.5,
        (points[2] + points[3]) * 0.5,
        (points[3] + points[0]) * 0.5,
    ]
    half_edge = EDGE_HANDLE_SIZE * 0.5
    for point in edge_mids:
        painter.drawEllipse(point, half_edge, half_edge)

    top_mid = edge_mids[0]
    center = (points[0] + points[2]) * 0.5
    direction = top_mid - center
    length = (direction.x() ** 2 + direction.y() ** 2) ** 0.5
    if length < 1e-6:
        direction = QPointF(0.0, -1.0)
    else:
        direction = QPointF(direction.x() / length, direction.y() / length)
    rotation_point = top_mid + direction * ROTATION_HANDLE_OFFSET
    painter.drawLine(top_mid, rotation_point)
    painter.drawEllipse(rotation_point, half_edge, half_edge)


def _draw_anchor_box(
    painter: QPainter,
    transform: QTransform,
    bounds: tuple[float, float, float, float],
) -> None:
    min_x, min_y, max_x, max_y = bounds
    if max_x <= min_x or max_y <= min_y:
        return
    if max(abs(min_x), abs(min_y), abs(max_x), abs(max_y)) > _COORD_LIMIT:
        return
    corners = [
        _map_point(transform, min_x, min_y),
        _map_point(transform, max_x, min_y),
        _map_point(transform, max_x, max_y),
        _map_point(transform, min_x, max_y),
    ]
    if any(point is None for point in corners):
        return
    painter.setPen(QPen(_ANCHOR_BOX_BORDER, 1, Qt.PenStyle.DashLine))
    painter.setBrush(QBrush(_ANCHOR_BOX_FILL))
    painter.drawPolygon(QPolygonF([point for point in corners if point is not None]))


def _draw_shape_handles(
    painter: QPainter,
    transform: QTransform,
    handles: Any,
) -> None:
    if not isinstance(handles, list):
        return
    for entry in handles:
        if not isinstance(entry, dict):
            continue
        try:
            x = float(entry.get("x"))  # type: ignore[arg-type]
            y = float(entry.get("y"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        point = _map_point(transform, x, y)
        if point is None:
            continue
        active = bool(entry.get("active", False))
        kind = str(entry.get("kind", "circle"))
        raw_color = entry.get("color")
        if isinstance(raw_color, (list, tuple)) and len(raw_color) >= 3:
            r, g, b = int(raw_color[0]), int(raw_color[1]), int(raw_color[2])
            color = (
                QColor(min(255, r + 40), min(255, g + 40), min(255, b + 40))
                if active
                else QColor(r, g, b)
            )
            border = QColor(max(0, r - 80), max(0, g - 80), max(0, b - 80))
        else:
            color = QColor(255, 140, 0) if active else QColor(255, 196, 72)
            border = QColor(130, 76, 0)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(color))
        size = SHAPE_HANDLE_SIZE_ACTIVE if active else SHAPE_HANDLE_SIZE
        half = size * 0.5
        if kind == "diamond":
            diamond = QPolygonF(
                [
                    QPointF(point.x(), point.y() - half),
                    QPointF(point.x() + half, point.y()),
                    QPointF(point.x(), point.y() + half),
                    QPointF(point.x() - half, point.y()),
                ]
            )
            painter.drawPolygon(diamond)
        else:
            painter.drawEllipse(point, half, half)


__all__ = ("draw_vector_selection_overlay",)
