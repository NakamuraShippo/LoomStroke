"""Complete reference remapping for a copied frame, retaining unknown fields."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass

from .metadata import read_frame_layer
from .models import PanelFrameError, SharedContent


@dataclass(frozen=True, slots=True)
class FrameReferenceRemap:
    panel_ids: Mapping[str, str]
    layer_ids: Mapping[str, str]
    edge_ids: Mapping[str, str]
    node_ids: Mapping[str, str]
    object_ids: Mapping[str, str]


def _remap(mapping: Mapping[str, str], old: str) -> str:
    new = mapping.get(old)
    if not isinstance(new, str) or not new or new == old:
        raise PanelFrameError(f"missing or unchanged copied reference: {old}")
    return new


def _validate_unique(mapping: Mapping[str, str]) -> None:
    if any(not isinstance(value, str) or not value for value in mapping.values()):
        raise PanelFrameError("invalid copied reference identity")
    if len(set(mapping.values())) != len(mapping):
        raise PanelFrameError("copy reference remap is not injective")


def remap_frame_metadata(metadata: Mapping, ids: FrameReferenceRemap) -> dict:
    """No fallback to the source page's identities; incomplete maps fail closed.

    The caller supplies remaps for the actual layer/object/layout copy. Unknown
    optional metadata remains detached and unchanged, including edge extensions.
    This does not implement the page copy command or infer unknown references.
    """
    frame = read_frame_layer(metadata)
    if frame is None:
        raise PanelFrameError("copy requires a materialized frame")
    for mapping in (ids.panel_ids, ids.layer_ids, ids.edge_ids, ids.node_ids, ids.object_ids):
        _validate_unique(mapping)
    result = copy.deepcopy(dict(metadata))
    payload = result["frame_layer"]
    payload["layout_leaf_ids"] = [_remap(ids.panel_ids, p) for p in frame.layout_leaf_ids]
    payload["panel_group_id"] = _remap(ids.layer_ids, frame.panel_group_id)
    payload["content_group_id"] = _remap(ids.layer_ids, frame.content_group_id)
    for edge in [*payload["edges"], *payload.get("retired_edges", [])]:
        edge["edge_id"] = _remap(ids.edge_ids, edge["edge_id"])
        edge["part_layer_id"] = _remap(ids.layer_ids, edge["part_layer_id"])
        boundary = edge["boundary_ref"]
        if boundary["kind"] == "cut":
            boundary["node_id"] = _remap(ids.node_ids, boundary["node_id"])
        original = edge.get("vector_restore_ref", "")
        if original:
            edge["vector_restore_ref"] = _remap(ids.object_ids, original)
        if edge.get("restoration_layer_id"):
            edge["restoration_layer_id"] = _remap(ids.layer_ids, edge["restoration_layer_id"])
        for key in ("paint_source_layer_ids", "raster_source_layer_ids"):
            if key in edge:
                edge[key] = [_remap(ids.layer_ids, value) for value in edge[key]]
    for key in ("retired_panel_group_ids", "retired_part_layer_ids", "retired_edge_layer_ids"):
        if key in payload:
            payload[key] = [_remap(ids.layer_ids, value) for value in payload[key]]
    read_frame_layer(result)
    return result


def remap_shared_content(
    shared: SharedContent, *, page_id: str, ids: FrameReferenceRemap
) -> SharedContent:
    _validate_unique(ids.panel_ids)
    return SharedContent(
        page_id,
        _remap(ids.layer_ids, shared.group_id),
        tuple(_remap(ids.panel_ids, p) for p in shared.panel_ids),
    )
