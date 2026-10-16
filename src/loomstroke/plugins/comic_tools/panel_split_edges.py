"""Split outer ink into lossless display parts; create only the new cut ink."""

import copy
import math
import uuid
from dataclasses import replace

from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask, rebase_erasure
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer, write_frame_layer
from loomstroke.core.comic_panels.models import FrameEdge, PanelFrameError
from loomstroke.core.comic_panels.part_move_command import PanelPartMoveCommand
from loomstroke.core.comic_panels.part_structure_command import PanelLayerInsertCommand
from loomstroke.plugins.vector_tools.common import Anchor, VectorObject, VectorPath

from .model import PANEL_META_KEY
from .panel_edge_geometry import edge_side, segment_endpoints, vector_segment_points
from .panel_edge_restore_geometry import retain_fragment_endpoints
from .panel_fragment_anchor import fragment_anchor as _anchor
from .panel_merge_edges import _outer, span_window


def _fragment(old, edge, rect, side):
    if not _outer(rect, old, side):
        return None
    a, b = segment_endpoints(old, side, edge.span)
    c, d = segment_endpoints(rect, side)
    axis = 0 if side in {"top", "bottom"} else 1
    lo, hi = max(min(a[axis], b[axis]), min(c[axis], d[axis])), min(
        max(a[axis], b[axis]), max(c[axis], d[axis])
    )
    if hi - lo <= 1e-6:
        return None
    length = d[axis] - c[axis]
    points = (lo, hi) if length > 0 else (hi, lo)
    return tuple(max(0.0, min(1.0, (v - c[axis]) / length)) for v in points)


def _owned(side, span):
    if side not in {"top", "bottom"}:
        return ()
    start, end = ("top_left", "top_right") if side == "top" else ("bottom_right", "bottom_left")
    return tuple(c for c, owns in ((start, span[0] == 0), (end, span[1] == 1)) if owns)


def split_edge_parts(  # noqa: PLR0912, PLR0915 - atomic fragment and cut ownership
    state, panel_id, children, layout_command, *, page_size
):
    """Amend the detached command, retaining every source before topology changes."""
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    original_rect = state.panel_rects[panel_id]
    conversions = {c.panel_id: c for c in layout_command.commands if hasattr(c, "_frame")}
    child_frames = {panel_id: frame, **{pid: c._frame for pid, c in conversions.items()}}
    old_ids = {e.part_layer_id for e in frame.edges}
    commands = [c for c in layout_command.commands if getattr(c, "layer_id", None) not in old_ids]
    patches, allocated, raws = [], {c.panel_id: [] for c in children}, {}
    for child in children:
        raw = copy.deepcopy(state.panel_records[panel_id].metadata)
        raw["frame_layer"]["edges"] = copy.deepcopy(raw["frame_layer"]["edges"])
        raws[child.panel_id] = raw
    used = set()
    for index, edge in enumerate(frame.edges):
        side = edge_side(edge, index)
        part = state.panel_parts[edge.part_layer_id]
        for child in children:
            span = _fragment(original_rect, edge, child.rect, side)
            if span is None:
                continue
            new = edge.part_layer_id in used
            edge_id = f"panel-edge-fragment-{uuid.uuid4().hex}" if new else edge.edge_id
            part_id = edge_id + "-layer" if new else edge.part_layer_id
            metadata = copy.deepcopy(part.values["metadata"])
            if part.values["layer_mask"] is not None and not (
                metadata[PART_KEY].get("span_window") or metadata[PART_KEY].get("erase_strokes")
            ):
                raise PanelFrameError("edge has a user mask; preserve it before splitting")
            a, b = segment_endpoints(child.rect, side, span)
            c, d = segment_endpoints(original_rect, side, edge.span)
            metadata = rebase_erasure(metadata, (c, d), (a, b))
            raw_edges = raws[child.panel_id]["frame_layer"]["edges"]
            raw_index = next(i for i, e in enumerate(raw_edges) if e["edge_id"] == edge.edge_id)
            raw_edges[raw_index] = retain_fragment_endpoints(raw_edges[raw_index], (c, d), (a, b))
            cuts = (math.dist(a, c) > 1e-6, math.dist(b, d) > 1e-6)
            width = state.panel_sources[panel_id][1].style.stroke_width
            metadata[PART_KEY].update(
                edge_id=edge_id, span_window=True, window_ends=list(cuts), window_width=width
            )
            changed = replace(
                edge,
                edge_id=edge_id,
                part_layer_id=part_id,
                side=side,
                span=span,
                owned_corners=_owned(side, span),
            )
            if edge.render_mode == "raster":
                changed = replace(
                    changed, raster_anchor=_anchor(edge, part, child.rect, side, span, metadata)
                )
            mask = span_window(page_size, child.rect, side, span, window_ends=cuts, width=width)
            mask = erased_edge_mask(mask, metadata, (a, b), page_size)
            if new:
                objects = [
                    replace(
                        obj,
                        id=edge_id if obj.id == edge.edge_id else f"{edge_id}-{i}",
                        meta={**copy.deepcopy(obj.meta), "layer_id": part_id},
                    )
                    for i, obj in enumerate(part.values["vector_objects"])
                ]
                values = {
                    k: part.values[k]
                    for k in (
                        "name",
                        "layer_type",
                        "image",
                        "offset_x",
                        "offset_y",
                        "transform",
                        "visible",
                        "opacity",
                        "blend_mode",
                        "vector_kind",
                        "mask_enabled",
                    )
                    if k in part.values
                }
                values.update(
                    name=edge_id, vector_objects=objects, metadata=metadata, layer_mask=mask
                )
                patches.append(
                    PanelLayerInsertCommand(
                        part_id,
                        parent_id=child_frames[child.panel_id].panel_group_id,
                        attributes=values,
                    )
                )
                original_raw = next(
                    e
                    for e in raws[child.panel_id]["frame_layer"]["edges"]
                    if e["edge_id"] == edge.edge_id
                )
                clone = copy.deepcopy(original_raw)
                clone.update(edge_id=edge_id, part_layer_id=part_id)
                raws[child.panel_id]["frame_layer"]["edges"].append(clone)
            else:
                patches.append(
                    PanelLayerPatchCommand(
                        part_id,
                        before={
                            "metadata": part.values["metadata"],
                            "layer_mask": part.values["layer_mask"],
                        },
                        after={"metadata": metadata, "layer_mask": mask},
                    )
                )
                parent_id = child_frames[child.panel_id].panel_group_id
                if part.parent_id != parent_id:
                    patches.append(
                        PanelPartMoveCommand(part_id, parent_id, expected_parent_id=part.parent_id)
                    )
            used.add(edge.part_layer_id)
            allocated[child.panel_id].append(changed)
        if edge.part_layer_id not in used:
            metadata = copy.deepcopy(part.values["metadata"])
            metadata[PART_KEY]["role"] = "restore"
            patches.append(
                PanelLayerPatchCommand(
                    part.layer_id,
                    before={"metadata": part.values["metadata"], "visible": part.values["visible"]},
                    after={"metadata": metadata, "visible": False},
                )
            )
    for child in children:
        target = child_frames[child.panel_id]
        conversion = conversions.get(child.panel_id)
        for index, side in enumerate(("top", "right", "bottom", "left")):
            if any(e.side == side for e in allocated[child.panel_id]):
                if conversion:
                    default = target.edges[index]
                    patches.append(
                        PanelLayerPatchCommand(
                            default.part_layer_id,
                            before={"visible": True},
                            after={"visible": False},
                        )
                    )
                continue
            if conversion:
                default = target.edges[index]
                allocated[child.panel_id].append(replace(default, side=side))
                continue
            edge_id = f"panel-edge-cut-{uuid.uuid4().hex}"
            part_id = edge_id + "-layer"
            style = replace(state.panel_sources[panel_id][1].style, cap="butt")
            points = vector_segment_points(
                child.rect, side, style.stroke_width, (0.0, 1.0), _owned(side, (0.0, 1.0))
            )
            obj = VectorObject(edge_id, [VectorPath([Anchor(p) for p in points])], style)
            patches.append(
                PanelLayerInsertCommand(
                    part_id,
                    parent_id=target.panel_group_id,
                    attributes={
                        "name": edge_id,
                        "layer_type": "vector",
                        "vector_objects": [obj],
                        "metadata": {PART_KEY: {"version": 1, "role": "edge", "edge_id": edge_id}},
                    },
                )
            )
            # The updated carrier already contains the new cut's boundary reference.
            carrier_patch = next(
                c
                for c in commands
                if getattr(c, "layer_id", None) == state.panel_sources[panel_id][0]
            )
            planned = read_frame_layer(
                carrier_patch.after["vector_objects"][0].meta[PANEL_META_KEY]["metadata"]
            )
            boundary = next(
                e.boundary_ref for i, e in enumerate(planned.edges) if edge_side(e, i) == side
            )
            allocated[child.panel_id].append(
                FrameEdge(
                    edge_id,
                    boundary,
                    "vector",
                    part_id,
                    owned_corners=_owned(side, (0.0, 1.0)),
                    side=side,
                )
            )
        edges = tuple(
            sorted(
                allocated[child.panel_id],
                key=lambda e: (("top", "right", "bottom", "left").index(e.side), e.span),
            )
        )
        if conversion:
            layer_id, carrier = conversion._carrier_id, conversion._carrier
            before = [carrier]
            target_frame = target
        else:
            layer_id = state.panel_sources[panel_id][0]
            carrier_patch = next(c for c in commands if getattr(c, "layer_id", None) == layer_id)
            carrier = carrier_patch.after["vector_objects"][0]
            before = carrier_patch.after["vector_objects"]
            target_frame = read_frame_layer(carrier.meta[PANEL_META_KEY]["metadata"])
        meta = copy.deepcopy(carrier.meta)
        meta[PANEL_META_KEY]["metadata"] = write_frame_layer(
            raws[child.panel_id], replace(target_frame, edges=edges)
        )
        patches.append(
            PanelLayerPatchCommand(
                layer_id,
                before={"vector_objects": before},
                after={"vector_objects": [replace(carrier, meta=meta)]},
            )
        )
    layout_command.commands = tuple(commands + patches)
    return layout_command


def requires_edge_allocation(state, panel_id):
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is None:
        return False
    rect = state.panel_rects[panel_id]
    for index, edge in enumerate(frame.edges):
        if edge.render_mode == "raster":
            return True
        if (
            state.panel_parts[edge.part_layer_id]
            .values["metadata"]
            .get(PART_KEY, {})
            .get("erase_strokes")
        ):
            return True
        side = edge_side(edge, index)
        for obj in state.panel_parts[edge.part_layer_id].values["vector_objects"]:
            nominal = vector_segment_points(
                rect, side, obj.style.stroke_width, edge.span, edge.owned_corners
            )
            if len(obj.paths) != 1 or [a.pos for a in obj.paths[0].anchors] != list(nominal):
                return True
    return False
