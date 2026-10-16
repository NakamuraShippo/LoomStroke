"""Allocate existing outer-edge sources and retain retired interior ink."""

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.edge_erasure import erased_edge_mask
from loomstroke.core.comic_panels.edge_span_mask import span_window
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.part_move_command import PanelPartMoveCommand
from loomstroke.plugins.vector_tools.common import Anchor, VectorPath

from .panel_bridge_source import bridge_edge_parts
from .panel_edge_geometry import edge_side, relative_span, vector_segment_points


def _outer(old, rect, side):
    x, y, w, h = old
    nx, ny, nw, nh = rect
    return {
        "top": abs(y - ny),
        "right": abs(x + w - nx - nw),
        "bottom": abs(y + h - ny - nh),
        "left": abs(x - nx),
    }[side] < 1e-6


def _corners(old, rect, corners):
    def points(r):
        x, y, w, h = r
        return {
            "top_left": (x, y),
            "top_right": (x + w, y),
            "bottom_left": (x, y + h),
            "bottom_right": (x + w, y + h),
        }

    a, b = points(old), points(rect)
    return tuple(
        c for c in corners if all(abs(v - u) < 1e-6 for v, u in zip(a[c], b[c], strict=True))
    )


def merge_edge_parts(  # noqa: PLR0915 - outer, retired and bridge parts form one plan
    state, panel_ids, frames, rect, boundaries, *, page_size
):
    primary = frames[panel_ids[0]]
    commands, edges, retired = [], [], []
    raw = copy.deepcopy(state.panel_records[panel_ids[0]].metadata)
    raw["frame_layer"]["edges"] = []
    for panel_id in panel_ids:
        old = state.panel_rects[panel_id]
        frame = frames[panel_id]
        raw["frame_layer"]["edges"].extend(
            copy.deepcopy(state.panel_records[panel_id].metadata["frame_layer"]["edges"])
        )
        for index, edge in enumerate(frame.edges):
            part = state.panel_parts[edge.part_layer_id]
            side = edge_side(edge, index)
            marker = copy.deepcopy(part.values["metadata"])
            if not _outer(old, rect, side):
                marker[PART_KEY]["role"] = "restore"
                commands.append(
                    PanelLayerPatchCommand(
                        part.layer_id,
                        before={
                            "metadata": part.values["metadata"],
                            "visible": part.values["visible"],
                        },
                        after={"metadata": marker, "visible": False},
                    )
                )
                retired.append(part.layer_id)
                continue
            if part.values["layer_mask"] is not None and not (
                marker[PART_KEY].get("span_window") or marker[PART_KEY].get("erase_strokes")
            ):
                raise PanelFrameError("edge has a user mask; preserve it before changing topology")
            span = relative_span(old, edge.span, rect, side)
            corners = _corners(old, rect, edge.owned_corners)
            changed = replace(
                edge, side=side, span=span, owned_corners=corners, boundary_ref=boundaries[side]
            )
            updates = {}
            if edge.render_mode == "vector":
                objects = []
                for obj in part.values["vector_objects"]:
                    updated = obj
                    expected = vector_segment_points(
                        old, side, obj.style.stroke_width, edge.span, edge.owned_corners
                    )
                    if len(obj.paths) == 1 and [a.pos for a in obj.paths[0].anchors] == list(
                        expected
                    ):
                        points = vector_segment_points(
                            rect, side, obj.style.stroke_width, span, corners
                        )
                        updated = replace(obj, paths=[VectorPath([Anchor(p) for p in points])])
                    objects.append(updated)
                updates["vector_objects"] = objects
            marker[PART_KEY]["span_window"] = True
            updates.update(metadata=marker, layer_mask=span_window(page_size, rect, side, span))
            from .panel_edge_geometry import segment_endpoints  # noqa: PLC0415

            updates["layer_mask"] = erased_edge_mask(
                updates["layer_mask"], marker, segment_endpoints(rect, side, span), page_size
            )
            commands.append(
                PanelLayerPatchCommand(
                    part.layer_id, before={key: part.values[key] for key in updates}, after=updates
                )
            )
            if part.parent_id != primary.panel_group_id:
                commands.append(
                    PanelPartMoveCommand(
                        part.layer_id, primary.panel_group_id, expected_parent_id=part.parent_id
                    )
                )
            edges.append(changed)
    commands.extend(
        bridge_edge_parts(
            state,
            edges,
            raw,
            rect,
            boundaries,
            parent_id=primary.panel_group_id,
            style=state.panel_sources[panel_ids[0]][1].style,
            page_size=page_size,
        )
    )
    edges.sort(key=lambda e: (("top", "right", "bottom", "left").index(e.side), e.span))
    raw["frame_layer"].setdefault("retired_edge_layer_ids", []).extend(retired)
    return tuple(edges), raw, commands
