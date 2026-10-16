"""Page template library operations using the existing template store."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loomstroke.core.page_templates import TemplateCaptureOptions

from .editing_common import edit_operation, invalid
from .page_tools import require_page, require_pages
from .paint_extension_arguments import ID, NAME, action_schema, validate
from .serialization import to_jsonable
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_page_templates",
        "List/preview, capture a page, duplicate, rename, delete, or categorize page templates. "
        "Writes use the application's template library; library edits have no document Undo, "
        "matching the GUI. Built-in templates are read-only.",
        action_schema(
            {
                "list": ({}, ()),
                "preview": ({"templateId": ID}, ("templateId",)),
                "capture": (
                    {
                        "pageId": ID,
                        "name": NAME,
                        "includeVectorObjects": {"type": "boolean"},
                        "includeGuides": {"type": "boolean"},
                        "includeRasterArtwork": {"type": "boolean"},
                    },
                    ("pageId", "name"),
                ),
                "duplicate": ({"templateId": ID}, ("templateId",)),
                "rename": ({"templateId": ID, "name": NAME}, ("templateId", "name")),
                "delete": ({"templateId": ID}, ("templateId",)),
                "set_category": (
                    {"templateId": ID, "category": {"type": "string", "maxLength": 256}},
                    ("templateId", "category"),
                ),
            }
        ),
    ),
)


def _template(pages: Any, template_id: str, *, writable: bool = False) -> Any:
    template = next((item for item in pages.list_page_templates() if item.id == template_id), None)
    if template is None:
        raise invalid("The requested template does not exist.", "templateId")
    if writable and template.source != "user":
        raise invalid("Built-in templates cannot be modified.", "templateId")
    return template


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    pages = require_pages(service)
    action = args["action"]
    if action == "list":
        return {"templates": to_jsonable(pages.list_page_templates())}
    if action == "preview":
        _template(pages, args["templateId"])
        payload = pages.get_page_template_preview(args["templateId"])
        if not payload:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE, "This template has no preview."
            )
        asset_id = service._register_asset_payload(
            payload, kind="reference_image", source="page_template", dimensions={}
        )
        return {"templateId": args["templateId"], "assetId": asset_id, "mimeType": "image/png"}
    async with edit_operation(service, name, args):
        pages = require_pages(service)
        template_id = args.get("templateId")
        if template_id:
            _template(pages, template_id, writable=action not in {"duplicate"})
        if action == "capture":
            require_page(pages, args["pageId"])
            template_id = pages.save_page_as_template(
                args["pageId"],
                args["name"],
                options=TemplateCaptureOptions(
                    args.get("includeVectorObjects", True),
                    args.get("includeGuides", False),
                    args.get("includeRasterArtwork", False),
                ),
            )
            applied = template_id is not None
        elif action == "duplicate":
            template_id = pages.duplicate_page_template(template_id)
            applied = template_id is not None
        elif action == "rename":
            applied = pages.rename_page_template(template_id, args["name"])
        elif action == "set_category":
            applied = pages.set_page_template_category(template_id, args["category"])
        else:
            applied = pages.delete_page_template(template_id)
        if not applied:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "The template library operation failed."
            )
        return {"status": "applied", "templateId": template_id, "undoable": False}
