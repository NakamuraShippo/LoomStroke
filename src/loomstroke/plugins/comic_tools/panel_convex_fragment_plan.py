"""Preserve each processed outer fragment while inclining merged ownership."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.convex_clip import inset_polygon, polygon_clip_mask
from loomstroke.core.comic_panels.convex_edges import (
    edge_ownership_masks,
    edge_paths,
    span_ownership_mask,
)
from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask
from loomstroke.core.comic_panels.edge_resize import edge_placement
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import FrameEdge, PanelFrameError
from loomstroke.core.comic_panels.nominal_edge_region import nominal_edge_metadata
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.comic_panels.raster_source_origin import raster_image_anchor
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath

from .model import PANEL_META_KEY
from .panel_clip_policy import content_clip_command
from .panel_corner_ownership import retained_corner_owners
from .panel_edit_gate import require_panel_editable


def fragment_panel_commands(
    state, panel_id, polygon, *, layout, page_size, depths, policies, regenerate_clip=False
):
    from .panel_convex_plan import _mapped_objects, _patch  # noqa: PLC0415 - shared mapping helpers

    record = state.panel_records[panel_id]
    frame = read_frame_layer(record.metadata)
    require_panel_editable(state, panel_id, geometry=True)
    source_id, carrier = state.panel_sources[panel_id]
    style = carrier.style
    owners = retained_corner_owners(state, panel_id, polygon, depths)
    masks = edge_ownership_masks(
        tuple(map(int, page_size)),
        polygon.points,
        owners,
        corner_radius=style.stroke_width * style.miter_limit / 2 + 2,
    )
    commands = [
        content_clip_command(
            state,
            panel_id,
            polygon_clip_mask(
                tuple(map(int, page_size)), [inset_polygon(polygon.points, frame.clip_inset_px)]
            ),
            page_size,
            regenerate=regenerate_clip,
        )
    ]
    edges = []
    for position, boundary in enumerate(polygon.boundaries):
        matched = [(i, edge) for i, edge in enumerate(frame.edges) if edge.boundary_ref == boundary]
        if not matched:
            corners = tuple(f"vertex:{i}" for i, owner in enumerate(owners) if owner == position)
            edge, command = fresh_edge(
                state, frame, polygon, position, masks[position], style, corners
            )
            edges.append(edge)
            commands.append(command)
        for index, old_edge in matched:
            part = state.panel_parts[old_edge.part_layer_id]
            metadata = copy.deepcopy(part.values["metadata"])
            marker = metadata[PART_KEY]
            if part.values["layer_mask"] is not None and not (
                marker.get("nearest_mask")
                or marker.get("span_window")
                or marker.get("erase_strokes")
            ):
                raise PanelFrameError("inclining an independently masked edge requires a mask plan")
            a, b = polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]
            ends = tuple(tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)) for t in old_edge.span)
            corners = tuple(
                f"vertex:{i}"
                for i, owner in enumerate(owners)
                if owner == position
                and (
                    (i == position and old_edge.span[0] == 0)
                    or (i == (position + 1) % len(owners) and old_edge.span[1] == 1)
                )
            )
            marker["nearest_mask"] = True
            edge = replace(
                old_edge,
                owned_corners=corners,
                side="",
                raster_resize_policy=policies.get(old_edge.edge_id, old_edge.raster_resize_policy),
            )
            updates = {
                "layer_mask": erased_edge_mask(
                    span_ownership_mask(
                        masks[position],
                        a,
                        b,
                        edge.span,
                        window_ends=marker.get("window_ends", (False, False)),
                        corner_windows=[polygon.points[int(c[7:])] for c in corners],
                        corner_radius=style.stroke_width * style.miter_limit / 2 + 2,
                    ),
                    metadata,
                    ends,
                    page_size,
                ),
                "metadata": metadata,
            }
            if edge.render_mode == "raster":
                anchor, metadata = raster_image_anchor(
                    edge.raster_anchor,
                    metadata,
                    part.values["transform"],
                    (part.values["offset_x"], part.values["offset_y"]),
                )
                placement = edge_placement(anchor, *ends, policy=edge.raster_resize_policy)
                updates.update(
                    transform=LayerTransform.from_affine(Affine2D(*placement.matrix)),
                    offset_x=0,
                    offset_y=0,
                    metadata=metadata,
                )
            else:
                updates["vector_objects"] = _mapped_objects(
                    state,
                    panel_id,
                    old_edge,
                    index,
                    part.values["vector_objects"],
                    polygon,
                    position,
                )
                updates["metadata"] = nominal_edge_metadata(
                    metadata, polygon.points, ends, updates["layer_mask"]
                )
            commands.append(_patch(part, updates))
            edges.append(edge)
    retired = [edge for edge in frame.edges if edge.boundary_ref not in polygon.boundaries]
    commands.extend(
        _patch(state.panel_parts[edge.part_layer_id], {"visible": False}) for edge in retired
    )
    changed = replace(
        frame,
        edges=tuple(edges),
        geometry_mode="layout_tree_convex",
        region_version=frame.region_version + 1,
        clip_mask_region_version=frame.region_version + 1,
    )
    metadata = write_frame_layer(record.metadata, changed)
    if retired:
        metadata["frame_layer"].setdefault("retired_edges", []).extend(
            copy.deepcopy(e)
            for e in record.metadata["frame_layer"]["edges"]
            if any(r.edge_id == e["edge_id"] for r in retired)
        )
    meta = copy.deepcopy(carrier.meta)
    meta[PANEL_META_KEY].update(
        metadata=metadata,
        gutter_px=layout.gutter_px,
        reading_order=layout.reading_order.index(panel_id),
    )
    commands.append(
        _patch(state.panel_parts[source_id], {"vector_objects": [replace(carrier, meta=meta)]})
    )
    return commands


def fresh_edge(state, frame, polygon, position, mask, style, corners):
    edge_id = f"panel-edge-{uuid.uuid4().hex}"
    edge = FrameEdge(edge_id, polygon.boundaries[position], "vector", f"{edge_id}-layer", corners)
    points = edge_paths(polygon.points, style.stroke_width, style.miter_limit)[position]
    return edge, PanelLayerInsertCommand(
        edge.part_layer_id,
        parent_id=frame.panel_group_id,
        attributes={
            "layer_type": "vector",
            "name": edge_id,
            "layer_mask": mask,
            "opacity": state.panel_parts[frame.edges[0].part_layer_id].values["opacity"],
            "metadata": {
                PART_KEY: {"version": 1, "role": "edge", "edge_id": edge_id, "nearest_mask": True}
            },
            "vector_objects": [
                VectorObject(
                    edge_id,
                    [VectorPath([Anchor(p) for p in points], closed=True)],
                    replace(style, cap="butt"),
                )
            ],
        },
    )
