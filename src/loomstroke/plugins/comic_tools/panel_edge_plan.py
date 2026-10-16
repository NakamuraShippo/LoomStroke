"""Per-edge rasterization and restoration, retaining the panel's carrier and mask."""

from __future__ import annotations

import copy
import math
import uuid
from dataclasses import replace
from types import SimpleNamespace

from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError, RasterAnchor
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.comic_panels.raster_patch_command import PanelRasterPatchCommand
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer

from .model import PANEL_META_KEY
from .panel_edge_geometry import edge_side, registered_endpoints, vector_segment_points
from .panel_edit_gate import require_panel_editable
from .panel_source_guard import source_guard


def _carrier_patch(state, panel_id, frame, *, restoration_layer_id=None, edge_id=None):
    layer_id, carrier = state.panel_sources[panel_id]
    metadata = write_frame_layer(state.panel_records[panel_id].metadata, frame)
    if restoration_layer_id:
        for payload in metadata["frame_layer"]["edges"]:
            if payload["edge_id"] == edge_id:
                payload["restoration_layer_id"] = restoration_layer_id
                payload["restoration_rect"] = list(state.panel_rects[panel_id])
                edge = next(e for e in frame.edges if e.edge_id == edge_id)
                index = frame.edges.index(edge)
                payload["restoration_endpoints"] = registered_endpoints(
                    state, panel_id, edge, index
                )
                objects = state.panel_parts[edge.part_layer_id].values["vector_objects"]
                polygon = state.panel_polygons.get(panel_id)
                payload["restoration_nominal"] = all(
                    len(obj.paths) == 1
                    and tuple(a.pos for a in obj.paths[0].anchors)
                    == (
                        polygon.points
                        if polygon
                        else vector_segment_points(
                            state.panel_rects[panel_id],
                            edge_side(edge, index),
                            obj.style.stroke_width,
                            edge.span,
                            edge.owned_corners,
                        )
                    )
                    for obj in objects
                )
    meta = copy.deepcopy(carrier.meta)
    meta[PANEL_META_KEY]["metadata"] = metadata
    return PanelLayerPatchCommand(
        layer_id,
        before={"vector_objects": state.panel_parts[layer_id].values["vector_objects"]},
        after={"vector_objects": [replace(carrier, meta=meta)]},
    )


def rasterize_edge_command(state, panel_id, edge_id):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is None:
        raise PanelFrameError("edge conversion requires a materialized panel")
    index = next((i for i, e in enumerate(frame.edges) if e.edge_id == edge_id), None)
    if index is None or frame.edges[index].render_mode != "vector":
        raise PanelFrameError("edge is not a vector part")
    edge = frame.edges[index]
    require_panel_editable(state, panel_id, edges=(edge,))
    part = state.panel_parts[edge.part_layer_id]
    objects = part.values["vector_objects"]
    if part.values["locked"] or state.panel_parts[frame.panel_group_id].values["locked"]:
        raise PanelFrameError("frame edge is locked")
    points = []
    for obj in objects:
        for path in obj.paths:
            for anchor in path.anchors:
                points.append(anchor.pos)
                for handle in (anchor.h_in, anchor.h_out):
                    if handle is not None:
                        points.append(tuple(anchor.pos[i] + handle[i] for i in (0, 1)))
    start, end = registered_endpoints(state, panel_id, edge, index)
    polygon = state.panel_polygons.get(panel_id)
    if polygon and all(
        len(obj.paths) == 1
        and obj.paths[0].closed
        and tuple(a.pos for a in obj.paths[0].anchors) == polygon.points
        for obj in objects
    ):
        # A nominal polygon source contains the complete outline for identical
        # Qt join quantization. Store only this part's ink neighbourhood;
        # its ownership mask makes the other outline segments irrelevant.
        points = [start, end]
    # Keep the nominal entire edge extent even after vector erasures.
    points += [start, end]
    pad = max((o.style.stroke_width * o.style.miter_limit / 2 + 2 for o in objects), default=2)
    left, top = math.floor(min(p[0] for p in points) - pad), math.floor(
        min(p[1] for p in points) - pad
    )
    right, bottom = math.ceil(max(p[0] for p in points) + pad), math.ceil(
        max(p[1] for p in points) + pad
    )

    def shifted(p):
        return (p[0] - left, p[1] - top) if p else None

    shifted_objects = [
        replace(
            obj,
            paths=[
                replace(
                    path,
                    anchors=[replace(a, pos=shifted(a.pos)) for a in path.anchors],
                )
                for path in obj.paths
            ],
        )
        for obj in objects
    ]
    rasterizer = SnapshotVectorRasterizer(width=right - left, height=bottom - top)
    image = rasterizer.rasterize(
        SimpleNamespace(id=edge.part_layer_id, vector_objects=shifted_objects, vector_kind="draw")
    )
    dx, dy = end[0] - start[0], end[1] - start[1]
    anchor = RasterAnchor(
        ((start[0] + end[0]) / 2 - left, (start[1] + end[1]) / 2 - top),
        0.5,
        math.hypot(dx, dy),
        math.atan2(dy, dx),
    )
    restore_id = f"edge-restore-{uuid.uuid4().hex}"
    restore = PanelLayerInsertCommand(
        restore_id,
        parent_id=frame.panel_group_id,
        attributes={
            "name": edge.edge_id,
            "layer_type": "vector",
            "vector_kind": "draw",
            "vector_objects": objects,
            "visible": False,
            "locked": True,
            "metadata": {PART_KEY: {"version": 1, "role": "restore", "edge_id": edge_id}},
        },
    )
    fields = {
        "layer_type": "raster",
        "vector_objects": [],
        "image": image,
        "offset_x": left,
        "offset_y": top,
        "transform": LayerTransform.identity(),
    }
    part_metadata = copy.deepcopy(part.values["metadata"])
    from .panel_edge_erasure_bake import bake_vector_erasure  # noqa: PLC0415

    image, mask = bake_vector_erasure(
        state, panel_id, edge, index, image, (left, top), (start, end), part_metadata
    )
    fields.update(image=image, layer_mask=mask)
    part_metadata[PART_KEY].update(source_image_origin=[0.0, 0.0], paint_basis_origin=[left, top])
    fields["metadata"] = part_metadata
    conversion = PanelRasterPatchCommand(
        part.layer_id, before={key: part.values[key] for key in fields}, after=fields
    )
    changed = replace(edge, render_mode="raster", raster_anchor=anchor)
    edges = tuple(changed if e.edge_id == edge_id else e for e in frame.edges)
    carrier = _carrier_patch(
        state,
        panel_id,
        replace(frame, edges=edges),
        restoration_layer_id=restore_id,
        edge_id=edge_id,
    )
    return PanelEditCommand(
        [source_guard(state), restore, conversion, carrier], label="Rasterize frame edge"
    )
