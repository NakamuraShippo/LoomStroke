"""Prepared inclined-layout edits retain source ink and boundary identities."""

import copy
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.convex_clip import inset_polygon, polygon_clip_mask
from loomstroke.core.comic_panels.convex_edges import (
    edge_ownership_masks,
    edge_paths,
)
from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask
from loomstroke.core.comic_panels.edge_resize import edge_placement
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY, SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import FrameEdge, PanelFrameError
from loomstroke.core.comic_panels.nominal_edge_region import nominal_edge_metadata
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.comic_panels.raster_source_origin import raster_image_anchor
from loomstroke.core.comic_panels.shared_metadata import read_shared_content
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath

from . import layout_tree as lt
from .model import PANEL_META_KEY, merged_layer_metadata_with_layout
from .panel_clip_policy import content_clip_command
from .panel_corner_ownership import retained_corner_owners
from .panel_edge_geometry import edge_side, registered_endpoints, vector_segment_points
from .panel_edit_gate import require_panel_editable
from .panel_effective_convex import effective_convex_panels
from .panel_source_guard import source_guard


def map_boundary_point(point, old, new):
    if point is None:
        return None
    a, b = old
    c, d = new
    dx, dy = b[0] - a[0], b[1] - a[1]
    nx, ny = d[0] - c[0], d[1] - c[1]
    old_length = (dx * dx + dy * dy) ** 0.5
    new_length = (nx * nx + ny * ny) ** 0.5
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / old_length**2
    normal = ((point[1] - a[1]) * dx - (point[0] - a[0]) * dy) / old_length
    return c[0] + t * nx - normal * ny / new_length, c[1] + t * ny + normal * nx / new_length


def boundary_depths(layout):
    result = {}

    def walk(node, depth):
        result[node.node_id] = depth
        for child in node.children:
            walk(child, depth + 1)

    walk(layout.root, 1)
    return result


def map_boundary_vector(vector, old, new):
    if vector is None:
        return None
    origin = map_boundary_point((0, 0), old, new)
    mapped = map_boundary_point(vector, old, new)
    return mapped[0] - origin[0], mapped[1] - origin[1]


def _patch(part, updates):
    return PanelLayerPatchCommand(
        part.layer_id, before={k: part.values[k] for k in updates}, after=updates
    )


def _mapped_objects(state, panel_id, old_edge, index, objects, polygon, position):
    old_polygon = state.panel_polygons.get(panel_id)
    result = []
    for obj in objects:
        if old_polygon:
            old_position = old_polygon.boundaries.index(old_edge.boundary_ref)
            nominal = edge_paths(old_polygon.points, obj.style.stroke_width, obj.style.miter_limit)[
                old_position
            ]
        else:
            nominal = vector_segment_points(
                state.panel_rects[panel_id],
                edge_side(old_edge, index),
                obj.style.stroke_width,
                old_edge.span,
                old_edge.owned_corners,
            )
        points = edge_paths(polygon.points, obj.style.stroke_width, obj.style.miter_limit)[position]
        if len(obj.paths) == 1 and tuple(a.pos for a in obj.paths[0].anchors) == tuple(nominal):
            paths = [VectorPath([Anchor(p) for p in points], closed=True)]
        else:
            old = registered_endpoints(state, panel_id, old_edge, index)
            a, b = polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]
            new = tuple(tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)) for t in old_edge.span)
            paths = (
                obj.paths
                if tuple(map(tuple, old)) == tuple(map(tuple, new))
                else [
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
            )
        result.append(replace(obj, paths=paths))
    return result


def _panel_commands(  # noqa: PLR0912, PLR0915 - ordered source/clip/edge/carrier plan
    state, panel_id, polygon, *, layout, page_size, depths, policies, regenerate_clip=False
):
    record = state.panel_records[panel_id]
    frame = read_frame_layer(record.metadata)
    if frame is None:
        raise PanelFrameError("inclining requires a materialized panel")
    if frame.layout_leaf_ids != (panel_id,) or any(
        edge.span != (0.0, 1.0)
        or state.panel_parts[edge.part_layer_id]
        .values["metadata"]
        .get(PART_KEY, {})
        .get("span_window")
        for edge in frame.edges
    ):
        from .panel_convex_fragment_plan import fragment_panel_commands  # noqa: PLC0415

        return fragment_panel_commands(
            state,
            panel_id,
            polygon,
            layout=layout,
            page_size=page_size,
            depths=depths,
            policies=policies,
            regenerate_clip=regenerate_clip,
        )
    require_panel_editable(state, panel_id, geometry=True)
    if state.panel_parts[frame.panel_group_id].values["locked"]:
        raise PanelFrameError("panel is locked")
    source_id, carrier = state.panel_sources[panel_id]
    style = carrier.style
    owners = retained_corner_owners(state, panel_id, polygon, depths)
    masks = edge_ownership_masks(
        tuple(map(int, page_size)),
        polygon.points,
        owners,
        corner_radius=style.stroke_width * style.miter_limit / 2 + 2,
    )
    paths = edge_paths(polygon.points, style.stroke_width, style.miter_limit)
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
        match = next(
            ((i, e) for i, e in enumerate(frame.edges) if e.boundary_ref == boundary), None
        )
        corners = tuple(f"vertex:{i}" for i, owner in enumerate(owners) if owner == position)
        if match is None:
            edge_id = f"panel-edge-{uuid.uuid4().hex}"
            edge = FrameEdge(edge_id, boundary, "vector", f"{edge_id}-layer", corners)
            commands.append(
                PanelLayerInsertCommand(
                    edge.part_layer_id,
                    parent_id=frame.panel_group_id,
                    attributes={
                        "layer_type": "vector",
                        "name": edge_id,
                        "layer_mask": masks[position],
                        "opacity": state.panel_parts[frame.edges[0].part_layer_id].values[
                            "opacity"
                        ],
                        "metadata": {
                            PART_KEY: {
                                "version": 1,
                                "role": "edge",
                                "edge_id": edge_id,
                                "nearest_mask": True,
                            }
                        },
                        "vector_objects": [
                            VectorObject(
                                edge_id,
                                [VectorPath([Anchor(p) for p in paths[position]], closed=True)],
                                replace(style, cap="butt"),
                            )
                        ],
                    },
                )
            )
            commands[-1]._layer.metadata = nominal_edge_metadata(
                commands[-1]._layer.metadata,
                polygon.points,
                (polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]),
                masks[position],
            )
        else:
            index, old_edge = match
            if old_edge.span != (0.0, 1.0):
                raise PanelFrameError(
                    "inclining split edge spans requires polygon fragment allocation"
                )
            part = state.panel_parts[old_edge.part_layer_id]
            if part.values["locked"] or part.values["mask_edit_mode"]:
                raise PanelFrameError("frame edge is locked or has an unfinished mask edit")
            marker = (part.values["metadata"] or {}).get(PART_KEY, {})
            if part.values["layer_mask"] is not None and not (
                marker.get("nearest_mask") or marker.get("erase_strokes")
            ):
                raise PanelFrameError("inclining an independently masked edge requires a mask plan")
            metadata = copy.deepcopy(part.values["metadata"])
            metadata[PART_KEY]["nearest_mask"] = True
            updates = {
                "layer_mask": erased_edge_mask(
                    masks[position],
                    metadata,
                    (
                        polygon.points[position],
                        polygon.points[(position + 1) % len(polygon.points)],
                    ),
                    page_size,
                ),
                "metadata": metadata,
            }
            edge = replace(
                old_edge,
                owned_corners=corners,
                side="",
                raster_resize_policy=policies.get(old_edge.edge_id, old_edge.raster_resize_policy),
            )
            if edge.render_mode == "raster":
                anchor, metadata = raster_image_anchor(
                    edge.raster_anchor,
                    metadata,
                    part.values["transform"],
                    (part.values["offset_x"], part.values["offset_y"]),
                )
                placement = edge_placement(
                    anchor,
                    polygon.points[position],
                    polygon.points[(position + 1) % len(polygon.points)],
                    policy=edge.raster_resize_policy,
                )
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
                    metadata,
                    polygon.points,
                    (
                        polygon.points[position],
                        polygon.points[(position + 1) % len(polygon.points)],
                    ),
                    updates["layer_mask"],
                )
            commands.append(_patch(part, updates))
        edges.append(edge)
    retired = [e for e in frame.edges if e.boundary_ref not in polygon.boundaries]
    for edge in retired:
        commands.append(_patch(state.panel_parts[edge.part_layer_id], {"visible": False}))
    changed = replace(
        frame,
        edges=tuple(edges),
        geometry_mode="layout_tree_convex",
        region_version=frame.region_version + 1,
        clip_mask_region_version=frame.region_version + 1,
    )
    metadata = write_frame_layer(record.metadata, changed)
    payload = metadata["frame_layer"]
    if retired:
        old_payload = record.metadata["frame_layer"]
        payload.setdefault("retired_edges", []).extend(
            copy.deepcopy(e)
            for e in old_payload["edges"]
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


def convex_layout_command(
    state, layout, *, page_size, label, edge_policies=None, regenerate_clips=(), guarded=True
):
    polygons = effective_convex_panels(layout, state.panel_records, page_size)
    if polygons.keys() != state.panel_records.keys():
        raise PanelFrameError(
            "inclined topology changes need an explicit content allocation preview"
        )
    order = tuple(p for p in layout.reading_order if p in polygons)
    order += tuple(p for p in polygons if p not in order)
    layout = replace(layout, reading_order=order)
    commands = [source_guard(state, page_size=page_size)] if guarded else []
    for panel_id, polygon in polygons.items():
        commands.extend(
            _panel_commands(
                state,
                panel_id,
                polygon,
                layout=layout,
                page_size=page_size,
                depths=boundary_depths(layout),
                policies=edge_policies or {},
                regenerate_clip=panel_id in regenerate_clips,
            )
        )
    from .panel_shared_plan import _shared_mask  # noqa: PLC0415 - planner dependency

    planned = replace(
        state,
        panel_rects={pid: polygon.rect for pid, polygon in polygons.items()},
        panel_polygons=polygons,
    )
    for group_id, part in state.panel_parts.items():
        if SHARED_KEY not in (part.values["metadata"] or {}):
            continue
        shared = read_shared_content(part.values["metadata"], group_id=group_id)
        commands.append(
            _patch(
                part,
                {"layer_mask": _shared_mask(planned, shared.panel_ids, tuple(map(int, page_size)))},
            )
        )
    commands.append(
        PanelLayerPatchCommand(
            state.frames_layer_id,
            before={"metadata": state.frames_layer_metadata},
            after={
                "metadata": merged_layer_metadata_with_layout(
                    state.frames_layer_metadata, lt.layout_to_payload(layout)
                )
            },
        )
    )
    return PanelEditCommand(commands, label=label)
