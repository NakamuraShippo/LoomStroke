"""Explicit vector restoration preserves the edited raster as a hidden source."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.command_types import LayerTransform
from loomstroke.plugins.vector_tools.common import Anchor, VectorPath

from .panel_edge_geometry import edge_side, registered_endpoints
from .panel_edge_plan import _carrier_patch
from .panel_edge_restore_geometry import restored_rect_paths
from .panel_edit_gate import require_panel_editable
from .panel_source_guard import source_guard


def restore_vector_edge_command(state, panel_id, edge_id):
    record = state.panel_records[panel_id]
    frame = read_frame_layer(record.metadata)
    index = next(i for i, e in enumerate(frame.edges) if e.edge_id == edge_id)
    edge = frame.edges[index]
    require_panel_editable(state, panel_id, edges=(edge,))
    if edge.render_mode != "raster":
        raise PanelFrameError("choose a raster edge to restore")
    payload = next(e for e in record.metadata["frame_layer"]["edges"] if e["edge_id"] == edge_id)
    restore_id = payload.get("restoration_layer_id")
    restoration = state.panel_parts.get(restore_id)
    part = state.panel_parts[edge.part_layer_id]
    if restoration is None or part.values["locked"]:
        raise PanelFrameError("vector restoration source is missing or edge is locked")
    old_rect = payload.get("restoration_rect", state.panel_rects[panel_id])
    rect = state.panel_rects[panel_id]
    side = edge_side(edge, index) if not state.panel_polygons else None
    objects = []
    for obj in restoration.values["vector_objects"]:
        if state.panel_polygons:
            from .panel_convex_plan import map_boundary_point, map_boundary_vector  # noqa: PLC0415

            old = payload.get(
                "restoration_fragment_endpoints", payload.get("restoration_endpoints")
            )
            if not old:
                raise PanelFrameError("polygon vector restoration has no source endpoints")
            new = registered_endpoints(state, panel_id, edge, index)
            if payload.get("restoration_nominal"):
                paths = [
                    VectorPath(
                        [Anchor(p) for p in state.panel_polygons[panel_id].points], closed=True
                    )
                ]
            elif tuple(map(tuple, old)) == tuple(map(tuple, new)):
                paths = obj.paths
            else:
                paths = [
                    replace(
                        path,
                        anchors=[
                            replace(
                                a,
                                pos=map_boundary_point(a.pos, old, new),
                                h_in=map_boundary_vector(a.h_in, old, new),
                                h_out=map_boundary_vector(a.h_out, old, new),
                            )
                            for a in path.anchors
                        ],
                    )
                    for path in obj.paths
                ]
            objects.append(replace(obj, paths=paths))
            continue
        paths = restored_rect_paths(obj, payload, old_rect, rect, edge, side)
        objects.append(replace(obj, paths=paths))
    backup_id = f"edge-edited-raster-{uuid.uuid4().hex}"
    backup = PanelLayerInsertCommand(
        backup_id,
        parent_id=frame.panel_group_id,
        attributes={
            "name": edge.edge_id,
            "layer_type": "raster",
            "image": part.values["image"],
            "transform": part.values["transform"],
            "offset_x": part.values["offset_x"],
            "offset_y": part.values["offset_y"],
            "layer_mask": part.values["layer_mask"],
            "visible": False,
            "locked": True,
            "metadata": {PART_KEY: {"version": 1, "role": "restore", "edge_id": edge_id}},
        },
    )
    metadata = copy.deepcopy(part.values["metadata"])
    from loomstroke.core.comic_panels.vector_page_basis import BASIS_KEY  # noqa: PLC0415

    basis = (restoration.values["metadata"] or {}).get(PART_KEY, {}).get(BASIS_KEY)
    if basis:
        metadata[PART_KEY][BASIS_KEY] = copy.deepcopy(basis)
    metadata[PART_KEY].pop("paint_basis_origin", None)
    metadata[PART_KEY].pop("source_image_origin", None)
    metadata[PART_KEY].setdefault("raster_source_layer_ids", []).append(backup_id)
    updates = {
        "layer_type": "vector",
        "vector_objects": objects,
        "image": None,
        "transform": LayerTransform.identity(),
        "offset_x": 0,
        "offset_y": 0,
        "metadata": metadata,
    }
    patch = PanelLayerPatchCommand(
        part.layer_id, before={k: part.values[k] for k in updates}, after=updates
    )
    changed = replace(
        frame,
        edges=tuple(
            replace(e, render_mode="vector", raster_anchor=None) if e.edge_id == edge_id else e
            for e in frame.edges
        ),
    )
    return PanelEditCommand(
        [source_guard(state), backup, patch, _carrier_patch(state, panel_id, changed)],
        label="Restore vector frame edge",
    )
