"""Frame operations for the existing confirmed CanvasChangeset apply route."""

import hashlib
import json
import math
import re
from dataclasses import asdict

from loomstroke.core.comic_panels.drawing_state import panel_input_busy
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.panel_convex_geometry import with_cut_skews
from loomstroke.plugins.comic_tools.panel_edge_plan import rasterize_edge_command
from loomstroke.plugins.comic_tools.panel_edge_restore_plan import restore_vector_edge_command
from loomstroke.plugins.comic_tools.panel_merge_plan import merge_panel_command
from loomstroke.plugins.comic_tools.panel_repair_preview import PanelRepairPreview
from loomstroke.plugins.comic_tools.panel_split_plan import split_preview
from loomstroke.plugins.comic_tools.service import ComicPageController

from .panel_edit_plans import (
    ADDITIONAL_ACTIONS,
    additional_frame_command,
    additional_operation_error,
)

OPERATION = "panel_frame_edit"
ACTIONS = (
    frozenset({"split", "merge", "resize", "incline", "rasterize_edge", "restore_edge"})
    | ADDITIONAL_ACTIONS
)


def apply_frame_operation(canvas, operation, command=None, *, asset_images=None):
    before = {
        layer.id: (layer.parent_id, layer.texture_version) for layer in canvas._flatten_layers()
    }
    if command is None:
        command = prepare_frame_operation(canvas, operation, asset_images=asset_images)
    if not command.apply(canvas):
        raise PanelFrameError(command.last_error)
    changed = [
        layer.id
        for layer in canvas._flatten_layers()
        if before.get(layer.id) != (layer.parent_id, layer.texture_version)
    ]
    return command, changed


def frame_digest(state):
    payload = {
        "layout": lt.layout_to_payload(state.layout) if state.layout else None,
        "records": {pid: r.to_meta() for pid, r in state.panel_records.items()},
        "parts": sorted(
            (
                part.layer_id,
                part.parent_id,
                part.texture_version,
                {
                    "locked": part.values["locked"],
                    "visible": part.values["visible"],
                    "mask_edit_mode": part.values["mask_edit_mode"],
                    "mask_enabled": part.values["mask_enabled"],
                    "opacity": part.values["opacity"],
                    "transform": asdict(part.values["transform"]),
                    "offset": (part.values["offset_x"], part.values["offset_y"]),
                },
            )
            for part in state.panel_parts.values()
        ),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def frame_geometry_summary(canvas):
    """Read geometry and references only; no pixels, AI, saliency or mutation."""
    state = ComicPageController().read_state(canvas, page_size=canvas.get_canvas_size())
    return {
        "expectedFrameDigest": frame_digest(state),
        "issues": list(state.panel_issues),
        "panels": [
            {
                "panelId": pid,
                "bounds": list(rect),
                "points": (
                    list(state.panel_polygons[pid].points) if pid in state.panel_polygons else None
                ),
                "materialized": read_frame_layer(state.panel_records[pid].metadata) is not None,
            }
            for pid, rect in state.panel_rects.items()
        ],
    }


def operation_error(operation):  # noqa: PLR0911, PLR0912 - one rejection per operation field
    action = operation.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        return "unsupported panel frame action"
    if action in ADDITIONAL_ACTIONS:
        return additional_operation_error(operation)
    if not isinstance(operation.get("panelId"), str) or not operation["panelId"]:
        return "panel frame action requires panelId"
    digest = operation.get("expectedFrameDigest")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return "panel frame action requires an exact source digest"
    if action in {"rasterize_edge", "restore_edge", "incline"} and (
        not isinstance(operation.get("edgeId"), str) or not operation["edgeId"]
    ):
        return "panel frame action requires edgeId"
    if action == "merge" and (
        not isinstance(operation.get("secondaryPanelId"), str) or not operation["secondaryPanelId"]
    ):
        return "merge requires secondaryPanelId"
    if action == "split" and (
        operation.get("orientation") not in {"vertical", "horizontal"}
        or type(operation.get("count", 2)) is not int
        or not 2 <= operation.get("count", 2) <= 8
    ):
        return "invalid panel split orientation or count"
    if action == "split":
        ratio = operation.get("ratio", 0.5)
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(ratio)
            or not 0 < ratio < 1
        ):
            return "split requires a ratio between zero and one"
        allocations = operation.get("allocations", {})
        if not isinstance(allocations, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in allocations.items()
        ):
            return "split allocations must map item ids to panel ids"
        policies = operation.get("imagePolicies", {})
        if not isinstance(policies, dict) or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or value not in {"move", "copy", "shared"}
            for key, value in policies.items()
        ):
            return "split image policies must map item ids to move, copy, or shared"
    if action == "incline":
        value = operation.get("skew")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return "incline requires a finite skew"
    if action == "resize":
        try:
            layout = lt.layout_from_payload(operation.get("layout"))
        except (TypeError, ValueError, KeyError, IndexError):
            layout = None
        if layout is None:
            return "resize requires a valid layout payload"
    return ""


def prepare_frame_operation(canvas, operation, *, asset_images=None):  # noqa: PLR0911, PLR0912
    if panel_input_busy(canvas):
        raise PanelFrameError("finish the current stroke or text composition first")
    error = operation_error(operation)
    if error:
        raise PanelFrameError(error)
    controller = ComicPageController()
    size = canvas.get_canvas_size()
    state = controller.read_state(canvas, page_size=size)
    if state.panel_issues or frame_digest(state) != operation["expectedFrameDigest"]:
        raise PanelFrameError("panel frame source changed after preview")
    if operation["action"] in {"create", "template"}:
        return additional_frame_command(canvas, state, controller, operation, asset_images or {})
    panel_id, action = operation["panelId"], operation["action"]
    if panel_id not in state.panel_records:
        raise PanelFrameError("panel frame target is missing")
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is None or state.panel_parts[frame.panel_group_id].values["locked"]:
        raise PanelFrameError("panel frame target is unconverted or locked")
    if action in ADDITIONAL_ACTIONS:
        return additional_frame_command(canvas, state, controller, operation, asset_images or {})
    if action == "rasterize_edge":
        return rasterize_edge_command(state, panel_id, operation["edgeId"])
    if action == "restore_edge":
        return restore_vector_edge_command(state, panel_id, operation["edgeId"])
    if action == "merge":
        return merge_panel_command(state, panel_id, operation["secondaryPanelId"], page_size=size)
    if action == "split":
        preview = split_preview(
            state,
            panel_id,
            operation["orientation"],
            page_size=size,
            count=operation.get("count", 2),
            ratio=operation.get("ratio", 0.5),
        )
        for item_id, target in operation.get("allocations", {}).items():
            preview.assign(item_id, target)
        for item_id, policy in operation.get("imagePolicies", {}).items():
            if item_id not in {a.item_id for a in preview.allocations} or policy not in {
                "move",
                "copy",
                "shared",
            }:
                raise PanelFrameError("invalid split image handling policy")
            preview.image_policies[item_id] = policy
        # MCP must explicitly decide every unresolved geometric allocation.
        if any(a.unresolved for a in preview.allocations):
            raise PanelFrameError("split has unresolved content allocations")
        return preview.command()
    if action == "incline":
        frame = read_frame_layer(state.panel_records[panel_id].metadata)
        edge = (
            next((e for e in frame.edges if e.edge_id == operation["edgeId"]), None)
            if frame
            else None
        )
        if edge is None or edge.boundary_ref.kind != "cut":
            raise PanelFrameError("incline requires a split boundary")
        ref = edge.boundary_ref

        def changed(node):
            if node.node_id == ref.node_id:
                skews = list(node.cut_skews or (0,) * (len(node.children) - 1))
                skews[ref.index] = operation["skew"]
                return with_cut_skews(state.layout, node.node_id, skews, page_size=size)
            return next((layout for child in node.children if (layout := changed(child))), None)

        layout = changed(state.layout.root)
        if layout is None:
            raise PanelFrameError("inclined cut node is missing")
    else:
        layout = lt.layout_from_payload(operation["layout"])
    return PanelRepairPreview(state, layout, page_size=size).command()
