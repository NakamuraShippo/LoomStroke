"""Allocate inclined outer source intervals to child panels without resampling."""

import copy
import math
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.convex_edges import edge_ownership_masks, span_ownership_mask
from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask, rebase_erasure
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_move_command import PanelPartMoveCommand
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.plugins.vector_tools.common import Anchor, VectorPath

from .model import PANEL_META_KEY
from .panel_convex_plan import boundary_depths, convex_layout_command
from .panel_convex_template_plan import convex_template_slots
from .panel_corner_ownership import retained_corner_owners
from .panel_edge_geometry import registered_endpoints
from .panel_edge_restore_geometry import retain_fragment_endpoints
from .panel_fragment_anchor import anchor_at_endpoints
from .panel_geometry_projection import project_panel_geometry
from .panel_source_guard import source_guard
from .panel_template_geometry_state import template_geometry_state


def _interval(old, polygon, boundary):
    if boundary not in polygon.boundaries:
        return None
    position = polygon.boundaries.index(boundary)
    a, b = polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    values = [((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length for p in old]
    span = max(0.0, min(values)), min(1.0, max(values))
    if span[1] - span[0] <= 1e-8:
        return None
    ends = tuple(tuple(a[i] + t * (b[i] - a[i]) for i in (0, 1)) for t in span)
    return position, span, ends


def _part_commands(part, edge, changed, metadata, mask, parent_id, *, fresh, objects):
    if not fresh:
        result = [
            PanelLayerPatchCommand(
                part.layer_id,
                before={
                    "metadata": part.values["metadata"],
                    "layer_mask": part.values["layer_mask"],
                },
                after={"metadata": metadata, "layer_mask": mask},
            )
        ]
        if edge.render_mode == "vector":
            result[0].before["vector_objects"] = part.values["vector_objects"]
            result[0].after["vector_objects"] = objects
        if part.parent_id != parent_id:
            result.append(
                PanelPartMoveCommand(part.layer_id, parent_id, expected_parent_id=part.parent_id)
            )
        return result
    values = {
        key: value
        for key, value in part.values.items()
        if key not in {"children", "vector_objects", "metadata", "layer_mask"}
    }
    values.update(
        name=changed.edge_id,
        metadata=metadata,
        layer_mask=mask,
        vector_objects=[
            replace(
                obj,
                id=changed.edge_id if obj.id == edge.edge_id else f"{changed.edge_id}-{i}",
                meta={**copy.deepcopy(obj.meta), "layer_id": changed.part_layer_id},
            )
            for i, obj in enumerate(objects)
        ],
    )
    return [PanelLayerInsertCommand(changed.part_layer_id, parent_id=parent_id, attributes=values)]


def _allocate_sources(  # noqa: PLR0912, PLR0915 - one source registry/part allocation transaction
    state, panel_id, polygons, child_ids, command, layout, page_size, *, source_outlines=None
):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    old_parts = {e.part_layer_id for e in frame.edges}
    # Replace preliminary reshaping of source parts with lossless windows.
    command.commands = tuple(
        c for c in command.commands if getattr(c, "layer_id", None) not in old_parts
    )
    carriers = {
        pid: next(
            c
            for c in command.commands
            if getattr(c, "layer_id", None) == source[0]
            and "vector_objects" in getattr(c, "after", {})
        )
        for pid, source in state.panel_sources.items()
        if pid in child_ids
    }
    for item in command.commands:
        if getattr(item, "_frame", None) and item.panel_id in child_ids:
            carriers[item.panel_id] = next(
                c
                for c in command.commands
                if getattr(c, "layer_id", None) == item._carrier_id
                and "vector_objects" in getattr(c, "after", {})
            )
    used = set()
    patches = []
    for pid in child_ids:
        polygon = polygons[pid]
        carrier_patch = carriers[pid]
        carrier = carrier_patch.after["vector_objects"][0]
        target = read_frame_layer(carrier.meta[PANEL_META_KEY]["metadata"])
        owners = list(retained_corner_owners(state, panel_id, polygon, boundary_depths(layout)))
        original_refs = set(state.panel_polygons[panel_id].boundaries)
        for vertex in range(len(polygon.points)):
            adjacent = ((vertex - 1) % len(polygon.points), vertex)
            fresh_corner = next(
                (i for i in adjacent if polygon.boundaries[i] not in original_refs), None
            )
            if fresh_corner is not None:
                owners[vertex] = fresh_corner
        width = state.panel_sources[panel_id][1].style.stroke_width
        masks = edge_ownership_masks(
            tuple(map(int, page_size)),
            polygon.points,
            owners,
            corner_radius=width * carrier.style.miter_limit / 2 + 2,
        )
        allocated, raw_edges = [], []
        for index, edge in enumerate(frame.edges):
            old = registered_endpoints(state, panel_id, edge, index)
            fragment = _interval(old, polygon, edge.boundary_ref)
            if fragment is None:
                continue
            position, span, ends = fragment
            part = state.panel_parts[edge.part_layer_id]
            metadata = copy.deepcopy(part.values["metadata"])
            marker = metadata[PART_KEY]
            if part.values["layer_mask"] is not None and not (
                marker.get("nearest_mask")
                or marker.get("span_window")
                or marker.get("erase_strokes")
            ):
                raise PanelFrameError("splitting independently masked edges requires a mask plan")
            fresh = edge.part_layer_id in used
            edge_id = f"panel-edge-fragment-{uuid.uuid4().hex}" if fresh else edge.edge_id
            cuts = tuple(math.dist(p, q) > 1e-6 for p, q in zip(ends, old, strict=True))
            marker.update(
                edge_id=edge_id,
                nearest_mask=True,
                span_window=True,
                window_ends=list(cuts),
                window_width=width,
            )
            metadata = rebase_erasure(metadata, old, ends)
            corners = tuple(
                f"vertex:{i}"
                for i, owner in enumerate(owners)
                if owner == position
                and (
                    (i == position and span[0] == 0)
                    or (i == (position + 1) % len(owners) and span[1] == 1)
                )
            )
            changed = replace(
                edge,
                edge_id=edge_id,
                part_layer_id=f"{edge_id}-layer" if fresh else edge.part_layer_id,
                span=span,
                side="",
                owned_corners=corners,
            )
            if edge.render_mode == "raster":
                changed = replace(
                    changed, raster_anchor=anchor_at_endpoints(edge, part, ends, metadata)
                )
            a, b = polygon.points[position], polygon.points[(position + 1) % len(polygon.points)]
            mask = span_ownership_mask(
                masks[position],
                a,
                b,
                span,
                window_ends=cuts,
                corner_windows=[polygon.points[int(c[7:])] for c in corners],
                corner_radius=width * carrier.style.miter_limit / 2 + 2,
            )
            mask = erased_edge_mask(mask, metadata, ends, page_size)
            objects = [
                (
                    replace(
                        obj, paths=[VectorPath([Anchor(p) for p in polygon.points], closed=True)]
                    )
                    if len(obj.paths) == 1
                    and obj.paths[0].closed
                    and tuple(a.pos for a in obj.paths[0].anchors)
                    == (source_outlines or {}).get(
                        part.layer_id, state.panel_polygons[panel_id].points
                    )
                    else obj
                )
                for obj in part.values["vector_objects"]
            ]
            patches.extend(
                _part_commands(
                    part,
                    edge,
                    changed,
                    metadata,
                    mask,
                    target.panel_group_id,
                    fresh=fresh,
                    objects=objects,
                )
            )
            raw = next(
                e
                for e in state.panel_records[panel_id].metadata["frame_layer"]["edges"]
                if e["edge_id"] == edge.edge_id
            )
            raw = retain_fragment_endpoints(raw, old, ends)
            raw.update(edge_id=edge_id, part_layer_id=changed.part_layer_id)
            raw_edges.append(raw)
            allocated.append(changed)
            used.add(edge.part_layer_id)
        inherited = {e.boundary_ref for e in allocated}
        for edge in target.edges:
            if edge.boundary_ref not in inherited:
                position = polygon.boundaries.index(edge.boundary_ref)
                allocated.append(
                    replace(
                        edge,
                        owned_corners=tuple(
                            f"vertex:{i}" for i, owner in enumerate(owners) if owner == position
                        ),
                    )
                )
                part = (
                    next(
                        c
                        for c in command.commands
                        if getattr(c, "layer_id", None) == edge.part_layer_id
                        and "layer_mask" in getattr(c, "after", {})
                    )
                    if edge.part_layer_id in state.panel_parts
                    else None
                )
                if part:
                    part.after["layer_mask"] = masks[position]
                else:
                    inserted = next(
                        (
                            c
                            for c in command.commands
                            if isinstance(c, PanelLayerInsertCommand)
                            and c.layer_id == edge.part_layer_id
                        ),
                        None,
                    )
                    if inserted:
                        inserted._layer.layer_mask = masks[position]
                    else:
                        # The part was created by a slot conversion and warped by a patch.
                        patch = next(
                            c
                            for c in command.commands
                            if getattr(c, "layer_id", None) == edge.part_layer_id
                            and "layer_mask" in getattr(c, "after", {})
                        )
                        patch.after["layer_mask"] = masks[position]
            elif edge.part_layer_id not in old_parts:
                patches.append(
                    PanelLayerPatchCommand(
                        edge.part_layer_id, before={"visible": True}, after={"visible": False}
                    )
                )
        metadata = copy.deepcopy(carrier.meta[PANEL_META_KEY]["metadata"])
        registry = {raw["edge_id"]: raw for raw in metadata["frame_layer"]["edges"]}
        registry.update({raw["edge_id"]: raw for raw in raw_edges})
        metadata["frame_layer"]["edges"] = list(registry.values())
        metadata = write_frame_layer(metadata, replace(target, edges=tuple(allocated)))
        meta = copy.deepcopy(carrier.meta)
        meta[PANEL_META_KEY]["metadata"] = metadata
        patches.append(
            PanelLayerPatchCommand(
                carrier_patch.layer_id,
                before={"vector_objects": carrier_patch.after["vector_objects"]},
                after={"vector_objects": [replace(carrier, meta=meta)]},
            )
        )
    for edge in frame.edges:
        if edge.part_layer_id not in used:
            part = state.panel_parts[edge.part_layer_id]
            patches.append(
                PanelLayerPatchCommand(
                    part.layer_id,
                    before={"visible": part.values["visible"]},
                    after={"visible": False},
                )
            )
    command.commands += tuple(patches)


def convex_split_command(state, panel_id, layout, children, polygons, *, page_size):
    slots = convex_template_slots(state, layout, polygons, page_size=page_size)
    projected = project_panel_geometry(state, slots, page_size)
    projected = template_geometry_state(projected, state, layout, page_size)
    geometry = convex_layout_command(
        projected, layout, page_size=page_size, label="Split inclined panel", guarded=False
    )
    command = PanelEditCommand(
        [source_guard(state, page_size=page_size), *slots.commands, *geometry.commands],
        label="Split inclined panel",
    )
    _allocate_sources(
        state, panel_id, polygons, tuple(c.panel_id for c in children), command, layout, page_size
    )
    return command
