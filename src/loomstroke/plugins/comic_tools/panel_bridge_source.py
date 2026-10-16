"""Reuse matching retained source across a gap when split fragments reunite."""

import copy
import uuid
from dataclasses import replace
from itertools import pairwise

from loomstroke.core.comic_panels.edge_resize import edge_placement
from loomstroke.core.comic_panels.edge_span_mask import span_window
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.models import FrameEdge, PanelFrameError
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath

from .panel_edge_geometry import segment_endpoints, vector_segment_points
from .panel_edge_restore_geometry import retain_fragment_endpoints
from .panel_fragment_anchor import gap_anchor


def retained_bridge(state, first, second, raw, *, rect, side, span, edge_id, parent_id, page_size):
    if not first.vector_restore_ref or first.vector_restore_ref != second.vector_restore_ref:
        return None
    if first.render_mode != second.render_mode:
        return None
    source, other = (state.panel_parts[e.part_layer_id] for e in (first, second))
    keys = (
        ()
        if first.render_mode == "raster"
        else (
            "image",
            "transform",
            "offset_x",
            "offset_y",
            "opacity",
            "blend_mode",
        )
    )
    if any(source.values[key] != other.values[key] for key in keys):
        return None
    vectors, other_vectors = source.values["vector_objects"], other.values["vector_objects"]
    if first.render_mode != "raster" and (
        [(v.paths, v.style) for v in vectors] != [(v.paths, v.style) for v in other_vectors]
    ):
        return None
    part_id = edge_id + "-layer"
    metadata = copy.deepcopy(source.values["metadata"])
    marker = metadata[PART_KEY]
    marker.update(edge_id=edge_id, span_window=True)
    marker.pop("window_ends", None)
    marker.pop("window_width", None)
    values = {
        key: source.values[key]
        for key in (
            "layer_type",
            "image",
            "transform",
            "offset_x",
            "offset_y",
            "opacity",
            "blend_mode",
            "vector_kind",
            "mask_enabled",
            "visible",
        )
    }
    values.update(
        name=edge_id,
        metadata=metadata,
        layer_mask=span_window(page_size, rect, side, span),
        vector_objects=[
            replace(obj, id=f"{edge_id}-{i}", meta={**copy.deepcopy(obj.meta), "layer_id": part_id})
            for i, obj in enumerate(vectors)
        ],
    )
    edge = replace(first, edge_id=edge_id, part_layer_id=part_id, span=span, owned_corners=())
    if edge.render_mode == "raster":
        registered, effective, metadata = gap_anchor(first, second, source, other)
        metadata[PART_KEY].update(edge_id=edge_id, span_window=True)
        metadata[PART_KEY].pop("window_ends", None)
        metadata[PART_KEY].pop("window_width", None)
        edge = replace(edge, raster_anchor=registered)
        placement = edge_placement(
            effective, *segment_endpoints(rect, side, span), policy=edge.raster_resize_policy
        )
        values.update(
            metadata=metadata,
            offset_x=0,
            offset_y=0,
            transform=LayerTransform.from_affine(Affine2D(*placement.matrix)),
        )
    entry = next(e for e in raw["frame_layer"]["edges"] if e["edge_id"] == first.edge_id)
    entry = retain_fragment_endpoints(
        entry, segment_endpoints(rect, side, first.span), segment_endpoints(rect, side, span)
    )
    entry.update(edge_id=edge_id, part_layer_id=part_id)
    return edge, entry, PanelLayerInsertCommand(part_id, parent_id=parent_id, attributes=values)


def bridge_edge_parts(state, edges, raw, rect, boundaries, *, parent_id, style, page_size):
    commands = []
    for side in ("top", "right", "bottom", "left"):
        pieces = sorted((e for e in edges if e.side == side), key=lambda e: e.span)
        if not pieces:
            raise PanelFrameError("merged region has an uncovered frame side")
        for first, second in pairwise(pieces):
            if second.span[0] <= first.span[1] + 1e-9:
                continue
            span = (first.span[1], second.span[0])
            edge_id = f"panel-edge-bridge-{uuid.uuid4().hex}"
            retained = retained_bridge(
                state,
                first,
                second,
                raw,
                rect=rect,
                side=side,
                span=span,
                edge_id=edge_id,
                parent_id=parent_id,
                page_size=page_size,
            )
            if retained:
                edge, entry, command = retained
                edges.append(edge)
                raw["frame_layer"]["edges"].append(entry)
                commands.append(command)
                continue
            part_id = edge_id + "-layer"
            source = state.panel_parts[first.part_layer_id]
            objects = source.values["vector_objects"]
            ink_style = objects[0].style if objects else style
            points = vector_segment_points(rect, side, ink_style.stroke_width, span, ())
            obj = VectorObject(
                edge_id, [VectorPath([Anchor(p) for p in points])], replace(ink_style, cap="butt")
            )
            commands.append(
                PanelLayerInsertCommand(
                    part_id,
                    parent_id=parent_id,
                    attributes={
                        "name": edge_id,
                        "layer_type": "vector",
                        "vector_objects": [obj],
                        "opacity": source.values["opacity"],
                        "layer_mask": span_window(page_size, rect, side, span),
                        "metadata": {
                            PART_KEY: {
                                "version": 1,
                                "role": "edge",
                                "edge_id": edge_id,
                                "span_window": True,
                            }
                        },
                    },
                )
            )
            edges.append(
                FrameEdge(edge_id, boundaries[side], "vector", part_id, side=side, span=span)
            )
    return commands
