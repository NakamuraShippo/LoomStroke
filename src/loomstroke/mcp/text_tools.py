"""Existing text content, range formatting, frame stories and speaker styles."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from loomstroke.core.japanese_text.annotations import ANNOTATION_KEYS
from loomstroke.core.japanese_text.speaker_styles import apply_style, validate_registry
from loomstroke.core.japanese_text.story_commands import LinkTextFramesCommand

from .editing_common import edit_operation, invalid
from .extension_services import require_service
from .text_editing import (
    CHARACTER_FIELDS,
    FLOW_FIELDS,
    PARAGRAPH_FIELDS,
    SETTINGS_FIELDS,
    patched_text,
)
from .types import McpStructuredError, McpToolSpec, ReasonCode
from .vector_editing import ID, canvas_for, replace_object, resolve_object, schema


def _properties(fields):
    return {"type": "object", "properties": fields, "additionalProperties": False}


_TARGET = {"objectId": ID, "layerId": ID}
_FRAME = {"type": "array", "items": ID, "minItems": 3, "maxItems": 3}
TOOLS = (
    McpToolSpec(
        "loomstroke_update_text",
        "Update text content, defaults, vertical writing, frame columns/grid, "
        "character/paragraph ranges, ruby/TCY/emphasis/warichu/jidori and named styles. "
        "Ranges use Python string indexes on grapheme boundaries; saved extensions are preserved.",
        schema(
            {
                **_TARGET,
                "content": {"type": "string"},
                "settings": _properties(SETTINGS_FIELDS),
                "textFlow": _properties(FLOW_FIELDS),
                "ranges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "minimum": 0},
                            "end": {"type": "integer", "minimum": 1},
                            "character": _properties(CHARACTER_FIELDS),
                            "paragraph": _properties(PARAGRAPH_FIELDS),
                            "namedStyle": {"type": "object"},
                            "annotations": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "kind": {"enum": list(ANNOTATION_KEYS)},
                                        "options": {"type": ["object", "null"]},
                                    },
                                    "required": ["kind"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["start", "end"],
                        "additionalProperties": False,
                    },
                },
            },
            ["objectId"],
        ),
    ),
    McpToolSpec(
        "loomstroke_link_text_frames",
        "Link or detach page or document text stories using existing history. "
        "Document frame identifiers are [pageId, layerId, objectId]; "
        "orderedFrames sets an explicit chain.",
        schema(
            {
                "sourceObjectId": ID,
                "targetObjectId": ID,
                "layerId": ID,
                "sourceFrame": _FRAME,
                "targetFrame": _FRAME,
                "orderedFrames": {"type": "array", "items": _FRAME, "minItems": 2},
                "scope": {"enum": ["page", "document"]},
            },
            [],
        ),
    ),
    McpToolSpec(
        "loomstroke_speaker_styles",
        "Read/update the document speaker registry or apply an inherited style "
        "to an existing text object, retaining explicit overrides.",
        schema(
            {
                "action": {"enum": ["read", "update", "apply"]},
                "registry": {"type": "object"},
                **_TARGET,
                "styleId": ID,
                "clearOverrides": {"type": "boolean"},
            },
            ["action"],
        ),
    ),
)


async def dispatch(  # noqa: PLR0912 - text, story and document speaker ports
    service: Any, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    canvas = canvas_for(service)
    if name == "loomstroke_speaker_styles" and args["action"] == "read":
        styles = require_service(service, "pages").speaker_styles
        return {"registry": styles.read(), "usageCounts": styles.usage_counts()}
    if name == "loomstroke_update_text":
        _, obj = resolve_object(canvas, args, editing=True)
        patched_text(obj, args)
    async with edit_operation(service, name, args) as canvas:
        if name == "loomstroke_update_text":
            layer_id, current = resolve_object(canvas, args, editing=True)
            replace_object(canvas, layer_id, patched_text(current, args))
        elif name == "loomstroke_link_text_frames":
            if args.get("scope", "page") == "document":
                stories = require_service(service, "pages").document_stories
                if "orderedFrames" in args:
                    changed = stories.link_ordered(
                        [tuple(frame) for frame in args["orderedFrames"]]
                    )
                elif "sourceFrame" in args:
                    changed = stories.link(
                        tuple(args["sourceFrame"]),
                        tuple(args["targetFrame"]) if "targetFrame" in args else None,
                    )
                else:
                    raise invalid(
                        "Document linking requires sourceFrame or orderedFrames.", "sourceFrame"
                    )
            else:
                source = args.get("sourceObjectId")
                layer_id, obj = resolve_object(
                    canvas, {"objectId": source, "layerId": args.get("layerId")}, editing=True
                )
                if "targetObjectId" in args:
                    resolve_object(canvas, {"objectId": args["targetObjectId"]}, editing=True)
                command = LinkTextFramesCommand(
                    str(obj.id), args.get("targetObjectId"), layer_id=layer_id
                )
                changed = canvas.submit(command, merge_key=None)
            if not changed:
                raise invalid("The text story link was rejected.", "sourceObjectId")
        elif name == "loomstroke_speaker_styles":
            styles = require_service(service, "pages").speaker_styles
            action = args["action"]
            if action == "update":
                registry = args.get("registry")
                try:
                    validate_registry(registry)
                    changed = styles.update(deepcopy(registry))
                except ValueError as exc:
                    raise invalid(str(exc), "registry") from exc
                return {
                    "changed": bool(changed),
                    "undoable": bool(changed),
                    "registry": styles.read(),
                }
            if action != "apply":
                raise invalid("Unknown speaker action.", "action")
            layer_id, obj = resolve_object(canvas, args, editing=True)
            style = next(
                (
                    item
                    for item in styles.read().get("styles", [])
                    if item["style_id"] == args.get("styleId")
                ),
                None,
            )
            if style is None or "text_tool" not in obj.meta:
                raise invalid("A valid speaker style and text object are required.", "styleId")
            payload = apply_style(
                obj.meta["text_tool"], style, clear_overrides=args.get("clearOverrides", False)
            )
            replace_object(
                canvas, layer_id, replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload})
            )
        else:
            raise McpStructuredError(ReasonCode.INVALID_ARGUMENT, "Unknown text operation.")
        return {"changed": True, "undoable": True}
