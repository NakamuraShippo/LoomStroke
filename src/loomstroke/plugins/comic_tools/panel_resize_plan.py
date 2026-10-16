"""Prepare rectangle changes without replacing content or raster edge sources."""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from loomstroke.core.comic_panels.clip_mask import union_clip_mask
from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask
from loomstroke.core.comic_panels.edge_resize import edge_placement
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.raster_source_origin import raster_image_anchor
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.plugins.vector_tools.common import Anchor, VectorPath

from .model import PANEL_META_KEY
from .panel_clip_policy import content_clip_command
from .panel_edit_gate import require_panel_editable


def edge_endpoints(rect, side):
    x, y, w, h = rect
    return {
        "top": ((x, y), (x + w, y)),
        "right": ((x + w, y), (x + w, y + h)),
        "bottom": ((x + w, y + h), (x, y + h)),
        "left": ((x, y + h), (x, y)),
    }[side]


def _mapped_point(p, old_rect, new_rect):
    if p is None:
        return None
    ox, oy, ow, oh = old_rect
    x, y, w, h = new_rect
    return x + (p[0] - ox) * w / ow, y + (p[1] - oy) * h / oh


def map_edge_point(point, old_rect, new_rect, side):
    if point is None:
        return None
    a, b = edge_endpoints(old_rect, side)
    c, d = edge_endpoints(new_rect, side)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (length * length)
    normal = ((point[1] - a[1]) * dx - (point[0] - a[0]) * dy) / length
    nx, ny = d[0] - c[0], d[1] - c[1]
    new_length = math.hypot(nx, ny)
    return c[0] + t * nx - normal * ny / new_length, c[1] + t * ny + normal * nx / new_length


def _patch(part, updates):
    return PanelLayerPatchCommand(
        part.layer_id, before={key: part.values[key] for key in updates}, after=updates
    )


def map_edge_vector(vector, old_rect, new_rect, side):
    if vector is None:
        return None
    origin = map_edge_point((0, 0), old_rect, new_rect, side)
    mapped = map_edge_point(vector, old_rect, new_rect, side)
    return mapped[0] - origin[0], mapped[1] - origin[1]


def resize_panel_commands(  # noqa: PLR0915 - mask, edge sources and carrier form one resize
    state,
    panel_id,
    rect,
    boundaries,
    *,
    page_size,
    policies=None,
    record_updates=None,
    regenerate_clip=False,
):
    """Return mask, edge placement, and carrier updates for one panel."""
    record = state.panel_records[panel_id]
    frame = read_frame_layer(record.metadata)
    if frame is None:
        raise PanelFrameError("resize requires a materialized panel")
    require_panel_editable(state, panel_id, geometry=True)
    parts = state.panel_parts
    source_id, carrier = state.panel_sources[panel_id]
    required = {
        source_id,
        frame.content_group_id,
        frame.panel_group_id,
        *(e.part_layer_id for e in frame.edges),
    }
    if not required <= parts.keys():
        raise PanelFrameError("panel resize has missing parts")
    if parts[frame.panel_group_id].values["locked"]:
        raise PanelFrameError("panel is locked")
    x, y, w, h = rect
    inset = frame.clip_inset_px
    if min(w, h) <= 2 * inset:
        raise PanelFrameError("panel is too small for its clip inset")
    mask = union_clip_mask(
        tuple(map(int, page_size)), [(x + inset, y + inset, w - 2 * inset, h - 2 * inset)]
    )
    commands = [content_clip_command(state, panel_id, mask, page_size, regenerate=regenerate_clip)]
    old_rect = state.panel_rects[panel_id]
    edges = []
    from .panel_edge_geometry import (  # noqa: PLC0415
        edge_side,
        segment_endpoints,
        vector_segment_points,
    )

    for index, original_edge in enumerate(frame.edges):
        edge = original_edge
        part = parts[edge.part_layer_id]
        side = edge_side(edge, index)
        policy = (policies or {}).get(edge.edge_id, edge.raster_resize_policy)
        edge = replace(edge, boundary_ref=boundaries[side], raster_resize_policy=policy)
        if part.values["metadata"].get("loomstroke.comictools.frame_part", {}).get("span_window"):
            from .panel_merge_edges import span_window  # noqa: PLC0415

            marker = part.values["metadata"]["loomstroke.comictools.frame_part"]
            commands.append(
                _patch(
                    part,
                    {
                        "layer_mask": span_window(
                            page_size,
                            rect,
                            side,
                            edge.span,
                            window_ends=marker.get("window_ends", (False, False)),
                            width=marker.get("window_width", 0),
                        )
                    },
                )
            )
        if part.values["metadata"].get(PART_KEY, {}).get("erase_strokes"):
            base = (
                commands[-1].after["layer_mask"]
                if getattr(commands[-1], "layer_id", None) == part.layer_id
                else None
            )
            commands.append(
                _patch(
                    part,
                    {
                        "layer_mask": erased_edge_mask(
                            base,
                            part.values["metadata"],
                            segment_endpoints(rect, side, edge.span),
                            page_size,
                        )
                    },
                )
            )
        if edge.render_mode == "raster":
            anchor, part_metadata = raster_image_anchor(
                edge.raster_anchor,
                part.values["metadata"],
                part.values["transform"],
                (part.values["offset_x"], part.values["offset_y"]),
            )
            placement = edge_placement(
                anchor, *segment_endpoints(rect, side, edge.span), policy=policy
            )
            transform = LayerTransform.from_affine(Affine2D(*placement.matrix))
            commands.append(
                _patch(
                    part,
                    {
                        "transform": transform,
                        "offset_x": 0,
                        "offset_y": 0,
                        "metadata": part_metadata,
                    },
                )
            )
        else:
            objects = copy.deepcopy(part.values["vector_objects"])
            updated_objects = []
            for obj in objects:
                old_points = vector_segment_points(
                    old_rect, side, obj.style.stroke_width, edge.span, edge.owned_corners
                )
                points = vector_segment_points(
                    rect, side, obj.style.stroke_width, edge.span, edge.owned_corners
                )
                if len(obj.paths) == 1 and [a.pos for a in obj.paths[0].anchors] == list(
                    old_points
                ):
                    paths = [VectorPath([Anchor(p) for p in points], closed=False)]
                else:
                    # Edited paths retain gaps. Only their boundary coordinates move.
                    paths = [
                        replace(
                            path,
                            anchors=[
                                replace(
                                    a,
                                    pos=map_edge_point(a.pos, old_rect, rect, side),
                                    h_in=map_edge_vector(a.h_in, old_rect, rect, side),
                                    h_out=map_edge_vector(a.h_out, old_rect, rect, side),
                                )
                                for a in path.anchors
                            ],
                        )
                        for path in obj.paths
                    ]
                updated_objects.append(replace(obj, paths=paths))
            commands.append(_patch(part, {"vector_objects": updated_objects}))
        edges.append(edge)
    changed = replace(
        frame,
        edges=tuple(edges),
        region_version=frame.region_version + 1,
        clip_mask_region_version=frame.region_version + 1,
    )
    metadata = write_frame_layer(record.metadata, changed)
    meta = copy.deepcopy(carrier.meta)
    # Preserve unknown fields in the existing PanelRecord payload too.
    meta[PANEL_META_KEY]["metadata"] = metadata
    meta[PANEL_META_KEY].update(record_updates or {})
    updated = replace(carrier, meta=meta)
    commands.append(_patch(parts[source_id], {"vector_objects": [updated]}))
    return commands
