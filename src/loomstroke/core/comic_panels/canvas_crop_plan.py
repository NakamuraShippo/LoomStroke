"""Prepare a managed page viewport change with original image buffers retained."""

import copy
from dataclasses import replace

from loomstroke.core.canvas_meta_transform_registry import CanvasMetaTransformRegistry
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .canvas_crop_masks import frame_crop_masks
from .canvas_transform_basis import _multiply, conjugate_transform
from .canvas_transform_plan import LAYOUT_NAMESPACE, _restore_fields
from .membership import BALLOON_KEY, PANEL_KEY, PART_KEY
from .metadata import read_frame_layer, write_frame_layer
from .models import PanelFrameError
from .page_geometry_registry import PanelPageGeometryRegistry
from .vector_page_basis import transformed_basis


def has_managed_frames(canvas):
    return any(
        (layer.metadata or {}).get(PART_KEY, {}).get("role") == "panel"
        for layer in canvas._flatten_layers()
    )


def prepare_frame_canvas_crop(canvas, size, shift):
    layers = tuple(canvas._flatten_layers())
    lookup = {layer.id: layer for layer in layers}
    layout = next(
        (
            (layer.metadata or {}).get("plugins", {}).get(LAYOUT_NAMESPACE)
            for layer in layers
            if LAYOUT_NAMESPACE in (layer.metadata or {}).get("plugins", {})
        ),
        None,
    )
    if layout is None:
        raise PanelFrameError("managed frame layout is missing")
    dx, dy = shift
    mode = f"translate:{dx}:{dy}"
    matrix = Affine2D.translation(dx, dy)
    registry = CanvasMetaTransformRegistry.shared()
    polygons = PanelPageGeometryRegistry.resolve(layout, mode, (canvas._width, canvas._height))
    prepared, frames = {}, []
    for layer in layers:
        metadata = registry.transform(layer.metadata, mode, canvas._width, canvas._height)
        metadata = copy.deepcopy((layer.metadata or {}) if metadata is None else metadata)
        marker = metadata.get(PART_KEY, {})
        if layer.layer_type == "vector" and marker.get("role") in {"edge", "restore"}:
            metadata = transformed_basis(metadata, matrix, (canvas._width, canvas._height))
        if marker.get("paint_basis_origin") is not None:
            marker["paint_basis_origin"] = list(matrix.apply(*marker["paint_basis_origin"]))
        values = {
            "metadata": metadata,
            "offset_x": layer.offset_x + dx if layer.layer_type == "raster" else layer.offset_x,
            "offset_y": layer.offset_y + dy if layer.layer_type == "raster" else layer.offset_y,
            "transform": conjugate_transform(layer.transform, matrix),
            "vector_objects": [
                _shift_object(obj, matrix, registry, mode, canvas) for obj in layer.vector_objects
            ],
        }
        if layer.layer_type == "raster" and layer.transform != LayerTransform.identity():
            # Explicit raster transforms map texture-local coordinates directly;
            # their legacy offsets are ignored by the GPU transform processor.
            values["transform"] = LayerTransform.from_affine(
                _multiply(matrix, layer.transform.to_affine())
            )
            values["offset_x"], values["offset_y"] = layer.offset_x, layer.offset_y
            if values["transform"] == LayerTransform.identity():
                values["offset_x"] = values["offset_y"] = 0
        for name in ("layer_mask", "effect_mask"):
            mask = getattr(layer, name, None)
            if mask is not None:
                values[name] = mask.copy(-dx, -dy, *size)
        for obj in values["vector_objects"]:
            payload = obj.meta.get(PANEL_KEY)
            frame = read_frame_layer(payload.get("metadata")) if payload else None
            if frame and marker.get("role") not in {"restore", "retired_carrier", "retired_panel"}:
                frames.append((frame, obj))
        prepared[layer.id] = values
    for frame, carrier in frames:
        for layer_id, mask in frame_crop_masks(
            frame, carrier, polygons, lookup, size, (canvas._width, canvas._height)
        ).items():
            prepared[layer_id]["layer_mask"] = mask
    return prepared


def _shift_object(obj, matrix, registry, mode, canvas):
    metadata = registry.transform(obj.meta, mode, canvas._width, canvas._height)
    metadata = copy.deepcopy(obj.meta if metadata is None else metadata)
    if (
        BALLOON_KEY in obj.meta
        and metadata.get(BALLOON_KEY) == obj.meta[BALLOON_KEY]
        and (matrix.tx or matrix.ty)
    ):
        raise PanelFrameError("balloon canvas translation hook is unavailable")
    text = metadata.get("text_tool")
    if text and isinstance(text.get("frame"), dict):
        frame = text["frame"]
        frame["x"], frame["y"] = matrix.apply(frame["x"], frame["y"])
    shape = metadata.get("shape_manager_parametric")
    if shape and shape.get("reference_bbox") and BALLOON_KEY not in obj.meta:
        x, y, x2, y2 = shape["reference_bbox"]
        shape["reference_bbox"] = [*matrix.apply(x, y), *matrix.apply(x2, y2)]
    payload = metadata.get(PANEL_KEY)
    frame = read_frame_layer(payload.get("metadata")) if payload else None
    if frame:
        payload["metadata"] = write_frame_layer(
            payload["metadata"],
            replace(
                frame,
                region_version=frame.region_version + 1,
                clip_mask_region_version=frame.region_version + 1,
            ),
        )
        record = payload["metadata"]["frame_layer"]
        for edge in (*record["edges"], *record.get("retired_edges", ())):
            _restore_fields(edge, matrix)
    return replace(
        obj,
        meta=metadata,
        paths=[
            replace(
                path,
                anchors=[replace(anchor, pos=matrix.apply(*anchor.pos)) for anchor in path.anchors],
            )
            for path in obj.paths
        ],
    )
