"""Additional panel changeset actions backed by the GUI's existing planners."""

import json

from jsonschema import Draft202012Validator

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.panel_content_plan import place_images_command
from loomstroke.plugins.comic_tools.panel_current_format import current_layout_command
from loomstroke.plugins.comic_tools.panel_delete_plan import delete_panel_command
from loomstroke.plugins.comic_tools.panel_edit_gate import require_editable_parts
from loomstroke.plugins.comic_tools.panel_template_plan import PanelTemplatePreview

from .panel_content_edit import content_edit_command
from .panel_edit_schema import FIELDS, PROPERTIES, REQUIRED

ADDITIONAL_ACTIONS = frozenset(
    {"create", "template", "delete", "assign_content", "place_image", "fit_content"}
)


def additional_operation_error(operation):
    allowed = FIELDS[operation["action"]] | {"action", "op", "expectedFrameDigest"}
    if operation["action"] in {"create", "template"}:
        allowed |= {"layout", "allocations"}
    if set(operation) - allowed:
        return "arguments do not belong to this panel action"
    try:
        json.dumps(operation, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return "panel action must contain finite JSON values"
    properties = {
        **PROPERTIES,
        "op": {"const": "panel_frame_edit"},
        "expectedFrameDigest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    required = ["action", "expectedFrameDigest", *REQUIRED[operation["action"]]]
    if operation["action"] in {"create", "template"}:
        required.append("layout")
    validator = Draft202012Validator(
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
    )
    if next(validator.iter_errors(operation), None):
        return "invalid panel action arguments"
    if operation["action"] in {"create", "template"}:
        return _layout_error(operation["layout"])
    return ""


def _layout_error(payload):
    try:
        layout = lt.layout_from_payload(payload)
        if layout is None:
            return "invalid panel template layout"
        nodes, node_ids, panels = [(layout.root, 0)], set(), set()
        while nodes:
            node, depth = nodes.pop()
            if depth > 16 or node.node_id in node_ids or len(node_ids) >= 255:
                return "panel layout has duplicate nodes or exceeds size/depth limits"
            node_ids.add(node.node_id)
            if node.is_leaf:
                if node.panel_id in panels:
                    return "panel layout has duplicate panel identities"
                panels.add(node.panel_id)
            nodes.extend((child, depth + 1) for child in node.children)
        if layout.gutter_px < 0 or layout.margin_px < 0:
            return "panel gutter and margin must be non-negative"
        return _next_identity_error(layout, node_ids, panels)
    except (TypeError, ValueError, KeyError, RecursionError):
        return "invalid panel template layout"


def _next_identity_error(layout, node_ids, panels):
    if any(
        pid.startswith("panel-") and pid[6:].isdigit() and int(pid[6:]) >= layout.next_panel_number
        for pid in panels
    ):
        return "panel layout next_panel_number would reuse an existing identity"
    if any(
        nid.startswith("n") and nid[1:].isdigit() and int(nid[1:]) >= layout.next_node_number
        for nid in node_ids
    ):
        return "panel layout next_node_number would reuse an existing identity"
    return ""


def additional_frame_command(canvas, state, controller, operation, asset_images):
    size = canvas.get_canvas_size()
    action = operation["action"]
    if action in {"create", "template"}:
        layout = lt.layout_from_payload(operation["layout"])
        if action == "create" and state.panel_records:
            raise PanelFrameError("panels already exist; use template or split")
        if not state.panel_records:
            return current_layout_command(controller, state, layout, page_size=size)
        preview = PanelTemplatePreview(state, layout, page_size=size).prepare()
        for item_id, target in operation.get("allocations", {}).items():
            preview.assign(item_id, target)
        if any(a.unresolved for a in preview.allocations):
            raise PanelFrameError("template has unresolved content allocations")
        return preview.command()
    pid = operation["panelId"]
    if action == "delete":
        return delete_panel_command(
            state, pid, page_size=size, disposition=operation["disposition"]
        )
    frame = read_frame_layer(state.panel_records[pid].metadata)
    require_editable_parts(state, [frame.content_group_id])
    if action == "place_image":
        image = asset_images.get(operation["assetId"])
        if image is None or image.isNull():
            raise PanelFrameError("registered image asset is unavailable")
        return place_images_command(
            state,
            [(operation.get("name", "Panel image"), image)],
            panel_ids=[pid],
            policy=operation.get("fitPolicy", "cover"),
        )
    return content_edit_command(canvas, state, operation)
