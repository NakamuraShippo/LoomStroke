"""Prepare frame references, source anchors and masks before page mutation."""

import copy
import math
from dataclasses import replace

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D

from .canvas_transform_basis import (
    _multiply,
    conjugate_transform,
    mapped_angle,
    mapped_corner,
    mapped_rect,
    mapped_side,
    page_matrix,
    reversed_winding,
    transform_mask,
)
from .membership import PANEL_KEY, PART_KEY
from .metadata import read_frame_layer, write_frame_layer
from .models import PanelFrameError
from .owner_page_geometry import owner_page_geometry
from .page_geometry_registry import PanelPageGeometryRegistry
from .raster_source_origin import raster_image_anchor
from .vector_page_basis import transformed_basis

LAYOUT_NAMESPACE = "loomstroke.comictools.panel_layout"


def _cut_reversals(tree, mode):
    result = {}

    def walk(node):
        if node.get("panel_id"):
            return
        orientation = node["orientation"]
        reverse = (
            mode in {"flip_hv", "rot180"}
            or (mode in {"flip_h", "rot-90"} and orientation == "vertical")
            or (mode in {"flip_v", "rot90"} and orientation == "horizontal")
        )
        result[node["node_id"]] = (reverse, len(node["children"]) - 1)
        for child in node["children"]:
            walk(child)

    walk(tree)
    return result


def prepare_frame_page_transform(canvas, mode):
    layers = tuple(canvas._flatten_layers())
    if not any((layer.metadata or {}).get(PART_KEY, {}).get("role") == "panel" for layer in layers):
        return {}, {}
    layout = next(
        (
            (layer.metadata or {}).get("plugins", {}).get(LAYOUT_NAMESPACE)
            for layer in layers
            if (layer.metadata or {}).get("plugins", {}).get(LAYOUT_NAMESPACE)
        ),
        None,
    )
    if layout is None:
        raise PanelFrameError("managed frame layout is missing")
    cuts = _cut_reversals(layout["tree"], mode)
    lookup = {layer.id: layer for layer in layers}
    matrix = page_matrix(mode, canvas._width, canvas._height)
    metadata, attributes = {}, {}
    polygons = None
    for layer in layers:
        for obj in layer.vector_objects:
            meta = copy.deepcopy(obj.meta)
            record = meta.get(PANEL_KEY)
            frame = read_frame_layer(record.get("metadata")) if record else None
            if frame and (layer.metadata or {}).get(PART_KEY, {}).get("role") not in {
                "restore",
                "retired_carrier",
                "retired_panel",
            }:
                polygon = None
                if frame.geometry_mode == "layout_tree_convex":
                    if polygons is None:
                        polygons = PanelPageGeometryRegistry.resolve(
                            layout, mode, (canvas._width, canvas._height)
                        )
                    polygon = owner_page_geometry(polygons, frame.layout_leaf_ids)
                edges = tuple(
                    _edge(edge, index, matrix, mode, cuts, lookup, metadata, polygon)
                    for index, edge in enumerate(frame.edges)
                )
                changed = replace(
                    frame,
                    edges=edges,
                    region_version=frame.region_version + 1,
                    clip_mask_region_version=frame.region_version + 1,
                )
                record["metadata"] = write_frame_layer(record["metadata"], changed)
                payload = record["metadata"]["frame_layer"]
                for edge in (*payload["edges"], *payload.get("retired_edges", ())):
                    _restore_fields(edge, matrix)
                metadata[(layer.id, obj.id)] = meta
        attributes[layer.id] = _layer_attributes(layer, matrix, mode)
    _vector_bases(layers, matrix, (canvas._width, canvas._height), metadata)
    return metadata, attributes


def _layer_attributes(layer, matrix, mode):
    values = {"transform": conjugate_transform(layer.transform, matrix)}
    if layer.layer_type == "raster" and layer.image is not None:
        source = layer.transform.to_affine()
        if layer.transform == LayerTransform.identity():
            source = Affine2D.translation(layer.offset_x, layer.offset_y)
        local = page_matrix(mode, layer.image.width(), layer.image.height())
        values.update(
            transform=LayerTransform.from_affine(
                _multiply(_multiply(matrix, source), local.inverse())
            ),
            offset_x=0,
            offset_y=0,
        )
    for name in ("layer_mask", "effect_mask"):
        mask = getattr(layer, name, None)
        if mask is not None:
            values[name] = transform_mask(mask, mode)
    return values


def _vector_bases(layers, matrix, page_size, metadata):
    for layer in layers:
        marker = (layer.metadata or {}).get(PART_KEY, {})
        if layer.layer_type == "vector" and marker.get("role") in {"edge", "restore"}:
            metadata[(layer.id, None)] = transformed_basis(layer.metadata, matrix, page_size)
            if reversed_winding(matrix):
                changed = metadata[(layer.id, None)][PART_KEY]
                for stroke in changed.get("erase_strokes", []):
                    stroke["points"] = [[1 - p[0], p[1]] for p in stroke["points"]]
                if "window_ends" in changed:
                    changed["window_ends"].reverse()


def _edge(edge, index, matrix, mode, cuts, layers, metadata, polygon):
    boundary = edge.boundary_ref
    if boundary.kind == "page":
        boundary = replace(boundary, side=mapped_side(boundary.side, matrix))
    else:
        reverse, count = cuts[boundary.node_id]
        if reverse:
            boundary = replace(
                boundary,
                side="after" if boundary.side == "before" else "before",
                index=count - 1 - boundary.index,
            )
    part = layers.get(edge.part_layer_id)
    if part is None:
        raise PanelFrameError("frame source part is missing")
    anchor = edge.raster_anchor
    if anchor:
        if part.image is None:
            raise PanelFrameError("raster frame source image is missing")
        effective, source_meta = raster_image_anchor(
            anchor, part.metadata, part.transform, (part.offset_x, part.offset_y)
        )
        local = page_matrix(mode, part.image.width(), part.image.height())
        anchor = replace(
            anchor,
            source_xy_px=local.apply(*effective.source_xy_px),
            edge_t=1 - anchor.edge_t if reversed_winding(matrix) else anchor.edge_t,
            source_angle_rad=mapped_angle(anchor.source_angle_rad, local),
        )
        source_meta[PART_KEY]["source_image_origin"] = [0.0, 0.0]
        if reversed_winding(matrix) and "window_ends" in source_meta[PART_KEY]:
            source_meta[PART_KEY]["window_ends"].reverse()
        metadata[(part.id, None)] = source_meta
    corners = (
        tuple(mapped_corner(c, matrix) for c in edge.owned_corners)
        if polygon is None
        else tuple(_mapped_vertex(c, polygon, matrix) for c in edge.owned_corners)
    )
    if polygon is not None and boundary not in polygon.after_boundaries:
        raise PanelFrameError("transformed convex boundary is missing")
    return replace(
        edge,
        boundary_ref=boundary,
        raster_anchor=anchor,
        side=(
            mapped_side(edge.side or ("top", "right", "bottom", "left")[index], matrix)
            if polygon is None
            else ""
        ),
        span=(1 - edge.span[1], 1 - edge.span[0]) if reversed_winding(matrix) else edge.span,
        owned_corners=corners,
    )


def _mapped_vertex(corner, polygon, matrix):
    if not corner.startswith("vertex:"):
        raise PanelFrameError("convex frame corner is invalid")
    source = polygon.before_points[int(corner[7:])]
    point = matrix.apply(*source)
    index = next(
        (i for i, p in enumerate(polygon.after_points) if math.dist(point, p) < 1e-7), None
    )
    if index is None:
        raise PanelFrameError("transformed convex vertex is missing")
    return f"vertex:{index}"


def _restore_fields(edge, matrix):
    if "restoration_rect" in edge:
        edge["restoration_rect"] = mapped_rect(edge["restoration_rect"], matrix)
    for name in ("restoration_endpoints", "restoration_fragment_endpoints"):
        if name in edge:
            points = [list(matrix.apply(*p)) for p in edge[name]]
            edge[name] = list(reversed(points)) if reversed_winding(matrix) else points
    # Retain the actual transformed corner partition instead of regenerating
    # the original horizontal-corner convention after a quarter turn.
    if "restoration_nominal" in edge:
        edge["restoration_nominal"] = False


def install_frame_page_attributes(canvas, attributes):
    for layer_id, values in attributes.items():
        layer = canvas._get_layer_by_id(layer_id)
        for name, value in values.items():
            setattr(layer, name, value)
        layer.transform_version += 1
        layer.texture_version += 1
        if "layer_mask" in values:
            dirty = getattr(canvas, "_gpu_mask_dirty_layers", None)
            if isinstance(dirty, set):
                dirty.add(layer_id)
