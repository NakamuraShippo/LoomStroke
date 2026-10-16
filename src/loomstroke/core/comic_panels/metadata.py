"""Lossless, detached editing of CT-01's existing optional metadata slot.

Unknown fields are retained at every edited level. Unsupported versions/modes
are rejected for editing; reading this module never repairs or mutates a file.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .models import BoundaryRef, FrameEdge, FrameLayer, PanelFrameError, RasterAnchor


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PanelFrameError("frame metadata must be a mapping")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise PanelFrameError("frame reference must be a nonempty string")
    return value


def _integer(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise PanelFrameError("frame version/index must be a nonnegative integer")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PanelFrameError("frame references must be a sequence")
    return tuple(_string(v) for v in value)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PanelFrameError("frame geometry must be numeric")
    try:
        return float(value)
    except OverflowError as exc:
        raise PanelFrameError("frame geometry exceeds numeric range") from exc


def _supported(payload: Mapping[str, Any]) -> None:
    if _integer(payload.get("version")) != 1:
        raise PanelFrameError("unsupported frame metadata version")
    if payload.get("geometry_mode") not in {"layout_tree_rect", "layout_tree_convex"}:
        raise PanelFrameError("unsupported frame geometry mode")


def _edge(payload: Any) -> FrameEdge:
    p = _mapping(payload)
    b = _mapping(p.get("boundary_ref"))
    boundary = BoundaryRef(
        _string(b.get("kind")),
        _string(b.get("side")),
        b.get("node_id", ""),
        _integer(b.get("index", 0)),
    )
    anchor = None
    if p.get("raster_anchor") is not None:
        a = _mapping(p["raster_anchor"])
        xy = a.get("source_xy_px")
        if not isinstance(xy, (list, tuple)) or len(xy) != 2:
            raise PanelFrameError("invalid source anchor coordinates")
        anchor = RasterAnchor(
            (_number(xy[0]), _number(xy[1])),
            _number(a.get("edge_t")),
            _number(a.get("base_edge_length_px")),
            _number(a.get("source_angle_rad", 0.0)),
        )
    return FrameEdge(
        _string(p.get("edge_id")),
        boundary,
        _string(p.get("render_mode")),
        _string(p.get("part_layer_id")),
        _strings(p.get("owned_corners", [])),
        anchor,
        _string(p.get("raster_resize_policy", "keep_pixels")),
        p.get("vector_restore_ref", ""),
        p.get("side", ""),
        tuple(_number(v) for v in p.get("span", [0.0, 1.0])),
    )


def read_frame_layer(metadata: Mapping[str, Any] | None) -> FrameLayer | None:
    """Decode a PanelRecord.metadata slot; absent data is a legacy panel."""
    if metadata is None:
        return None
    metadata = _mapping(metadata)
    if "frame_layer" not in metadata:
        return None
    p = _mapping(metadata["frame_layer"])
    _supported(p)
    raw_edges = p.get("edges")
    if not isinstance(raw_edges, (list, tuple)):
        raise PanelFrameError("frame edges must be a sequence")
    return FrameLayer(
        _strings(p.get("layout_leaf_ids")),
        _string(p.get("panel_group_id")),
        _string(p.get("content_group_id")),
        tuple(_edge(e) for e in raw_edges),
        _number(p.get("clip_inset_px", 0.0)),
        _integer(p.get("region_version", 0)),
        _integer(p.get("clip_mask_region_version", 0)),
        p["geometry_mode"],
    )


def write_frame_layer(metadata: Mapping[str, Any] | None, frame: FrameLayer) -> dict[str, Any]:
    """Merge known fields without dropping sibling/nested unknown metadata."""
    result = copy.deepcopy(dict(metadata or {}))
    old = result.get("frame_layer")
    p = dict(_mapping(old)) if old is not None else {}
    if old is not None:
        _supported(p)
    old_edges = p.get("edges", [])
    if not isinstance(old_edges, (list, tuple)):
        raise PanelFrameError("invalid existing edge registry")
    by_id: dict[str, Mapping[str, Any]] = {}
    for raw_edge in old_edges:
        raw = _mapping(raw_edge)
        edge_id = _string(raw.get("edge_id"))
        if edge_id in by_id:
            raise PanelFrameError("duplicate existing edge identity")
        by_id[edge_id] = raw
    p.update(
        version=1,
        geometry_mode=frame.geometry_mode,
        layout_leaf_ids=list(frame.layout_leaf_ids),
        panel_group_id=frame.panel_group_id,
        content_group_id=frame.content_group_id,
        clip_inset_px=frame.clip_inset_px,
        region_version=frame.region_version,
        clip_mask_region_version=frame.clip_mask_region_version,
    )
    p["edges"] = [_write_edge(e, by_id.get(e.edge_id, {})) for e in frame.edges]
    result["frame_layer"] = p
    return result


def _write_edge(edge: FrameEdge, old: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(old))
    boundary = dict(_mapping(result.get("boundary_ref", {})))
    boundary.update(kind=edge.boundary_ref.kind, side=edge.boundary_ref.side)
    if edge.boundary_ref.kind == "cut":
        boundary.update(node_id=edge.boundary_ref.node_id, index=edge.boundary_ref.index)
    else:
        boundary.pop("node_id", None)
        boundary.pop("index", None)
    result.update(
        edge_id=edge.edge_id,
        boundary_ref=boundary,
        render_mode=edge.render_mode,
        part_layer_id=edge.part_layer_id,
        owned_corners=list(edge.owned_corners),
        raster_resize_policy=edge.raster_resize_policy,
        vector_restore_ref=edge.vector_restore_ref,
    )
    if edge.raster_anchor is not None:
        a = dict(_mapping(result.get("raster_anchor") or {}))
        a.update(
            source_xy_px=list(edge.raster_anchor.source_xy_px),
            edge_t=edge.raster_anchor.edge_t,
            base_edge_length_px=edge.raster_anchor.base_edge_length_px,
            source_angle_rad=edge.raster_anchor.source_angle_rad,
        )
        result["raster_anchor"] = a
    else:
        result.pop("raster_anchor", None)
    if edge.side or "side" in result:
        result["side"] = edge.side
    if edge.span != (0.0, 1.0) or "span" in result:
        result["span"] = list(edge.span)
    return result
