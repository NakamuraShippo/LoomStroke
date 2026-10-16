"""Discoverable panel operations over the existing preview/approved apply route."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, replace

from jsonschema import Draft202012Validator

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.service import ComicPageController

from .editing_common import check_gui_thread, invalid
from .panel_edit_hints import allocation_hints
from .panel_edit_schema import OBJECT, PREVIEW_SCHEMA, input_error
from .panel_frame_changeset import frame_geometry_summary, operation_error
from .types import McpStructuredError, McpToolSpec, ReasonCode
from .vector_editing import canvas_for, schema

TOOLS = (
    McpToolSpec(
        "loomstroke_get_panels",
        "List current Comic/Panels frames, layout, reading order, edge/content IDs and templates. "
        "Use returned revision for preview_panel_edit; no document mutation.",
        schema({}, editing=False),
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_preview_panel_edit",
        "Preview Comic/Panels create/template, split, merge, delete, resize/incline, "
        "image placement, content assignment/fit and edge rasterize/restore. "
        "Creates real clipped panel groups on apply. No canvas/history mutation. "
        "Inspect the returned preview, then pass its changeset and approvalTicket to "
        "loomstroke_apply_panel_edit. Delete requires explicit keep/delete disposition. "
        "place_image uses a registered assetId; assign_content uses an existing layerId. "
        "For unresolved contentAllocations, inspect targetPanels "
        "and resubmit explicit allocations.",
        PREVIEW_SCHEMA,
        read_only=True,
    ),
    McpToolSpec(
        "loomstroke_apply_panel_edit",
        "Apply one previewed panel edit as one Undo/Redo entry. Requires the unchanged "
        "changeset and approvalTicket from preview_panel_edit; rejects stale documents.",
        schema(
            {"changeset": OBJECT, "approvalTicket": OBJECT},
            ["changeset", "approvalTicket"],
            editing=False,
        ),
        destructive=True,
    ),
)
TOOL_BY_NAME = {spec.name: spec for spec in TOOLS}


def panel_edit_available(service):
    from .editing_readiness import document_state  # noqa: PLC0415 - avoid readiness import cycle

    return service.canvas_service is not None and document_state(service).get("present") is True


def _validate(name, args):
    from .editing_tools import (  # noqa: PLC0415 - validation without registration cycle
        _finite_values,
    )

    _finite_values(args)
    validator = Draft202012Validator(TOOL_BY_NAME[name].input_schema)
    if next(validator.iter_errors(args), None):
        raise invalid("Invalid panel tool arguments.")


def list_panels(service):
    canvas = canvas_for(service)
    state = ComicPageController().read_state(canvas, page_size=canvas.get_canvas_size())
    summary = frame_geometry_summary(canvas)
    for panel in summary["panels"]:
        pid = panel["panelId"]
        record = state.panel_records[pid]
        frame = read_frame_layer(record.metadata)
        panel.update(
            {
                "readingOrder": record.reading_order,
                "groupId": frame.panel_group_id if frame else None,
                "contentGroupId": frame.content_group_id if frame else None,
                "edges": [asdict(edge) for edge in frame.edges] if frame else [],
                "contents": (
                    [
                        {"layerId": unit.layer_id, "name": unit.name}
                        for unit in state.panel_contents.get(frame.content_group_id, ())
                    ]
                    if frame
                    else []
                ),
            }
        )
    summary["panels"].sort(key=lambda panel: panel["readingOrder"])
    context = service.get_edit_context({"includeThumbnail": False})
    return {
        **summary,
        "revision": context["contextDigest"],
        "layout": lt.layout_to_payload(state.layout) if state.layout else None,
        "templates": list(lt.TEMPLATE_IDS),
        "readingOrder": list(state.reading_order),
    }


def _operation(canvas, args):
    state = ComicPageController().read_state(canvas, page_size=canvas.get_canvas_size())
    action = args["action"]
    operation = {key: value for key, value in args.items() if key != "expectedRevision"}
    if action in {"create", "template"}:
        layout = (
            lt.layout_from_payload(args["layout"])
            if "layout" in args
            else lt.template_layout(
                args.get("templateId", "single"),
                gutter_px=args.get("gutterPx", state.layout.gutter_px if state.layout else 24),
            )
        )
        if layout is None:
            raise invalid("Invalid panel layout.", "layout")
        layout = replace(
            layout,
            margin_px=args.get("marginPx", layout.margin_px),
            gutter_px=args.get("gutterPx", layout.gutter_px),
        )
        operation = {
            "action": action,
            "layout": lt.layout_to_payload(layout),
            "allocations": args.get("allocations", {}),
        }
    elif action == "resize":
        layout = lt.layout_from_payload(args["layout"]) if "layout" in args else state.layout
        if layout is None:
            raise invalid("A current panel layout is required.", "layout")
        if "ratio" in args:
            record = state.panel_records.get(args["panelId"])
            frame = read_frame_layer(record.metadata) if record else None
            if frame and frame.layout_leaf_ids != (args["panelId"],):
                raise invalid("Use an explicit layout to resize the boundary of a merged panel.")
            layout = lt.set_split_ratio(layout, args["panelId"], args["ratio"])
        layout = replace(
            layout,
            gutter_px=args.get("gutterPx", layout.gutter_px),
            margin_px=args.get("marginPx", layout.margin_px),
        )
        operation = {
            "action": action,
            "panelId": args["panelId"],
            "layout": lt.layout_to_payload(layout),
        }
    return {
        **operation,
        "op": "panel_frame_edit",
        "expectedFrameDigest": frame_geometry_summary(canvas)["expectedFrameDigest"],
    }


async def dispatch(service, name, args):
    check_gui_thread()
    await asyncio.to_thread(_validate, name, args)
    canvas = canvas_for(service)
    if name == "loomstroke_get_panels":
        return list_panels(service)
    if name == "loomstroke_apply_panel_edit":
        operations = args["changeset"].get("operations", [])
        if (
            not isinstance(operations, list)
            or len(operations) != 1
            or not isinstance(operations[0], dict)
            or operations[0].get("op") != "panel_frame_edit"
        ):
            raise invalid("Apply exactly one previewed panel_frame_edit operation.", "changeset")
        result = await service.apply_canvas_changeset(args)
        return {**result, "panelFrames": list_panels(service)}
    reason = input_error(args)
    if reason:
        raise invalid(reason)
    context = service.get_edit_context({"includeThumbnail": False})
    if args.get("expectedRevision", context["contextDigest"]) != context["contextDigest"]:
        raise McpStructuredError(ReasonCode.CONFLICT, "The document changed before panel preview.")
    try:
        operation = _operation(canvas, args)
        reason = operation_error(operation)
        if reason:
            raise invalid(reason)
        hints = allocation_hints(canvas, operation)
        changeset = {
            "schemaVersion": "canvas_changeset.v1",
            "changesetId": "panel_" + uuid.uuid4().hex,
            "baseDocumentRevision": context["contextDigest"],
            "target": {},
            "operations": [operation],
            "blockedReasons": [],
        }
        preview = service.preview_canvas_changeset({"changeset": changeset})
    except (PanelFrameError, lt.ComicLayoutError, KeyError, ValueError) as exc:
        raise invalid(str(exc)) from exc
    return {**preview, "changeset": changeset, **hints}
