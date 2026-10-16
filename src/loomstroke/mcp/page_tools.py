"""Page structure operations through the existing document history router."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .editing_common import current_revision, edit_operation, invalid
from .paint_extension_arguments import ID, NAME, action_schema, validate
from .serialization import to_jsonable
from .types import McpStructuredError, McpToolSpec, ReasonCode

TOOLS = (
    McpToolSpec(
        "loomstroke_manage_page",
        "List, add (optionally from a template), duplicate, delete, rename, move, or activate "
        "pages. Structural edits use document Undo/Redo; the final page cannot be deleted.",
        action_schema(
            {
                "list": ({}, ()),
                "add": ({"templateId": ID, "afterPageId": ID}, ()),
                "duplicate": ({"pageId": ID}, ("pageId",)),
                "delete": ({"pageId": ID}, ("pageId",)),
                "rename": ({"pageId": ID, "name": NAME}, ("pageId", "name")),
                "move": (
                    {"pageId": ID, "index": {"type": "integer", "minimum": 0}},
                    ("pageId", "index"),
                ),
                "set_active": ({"pageId": ID}, ("pageId",)),
            }
        ),
    ),
)


def require_pages(service: Any) -> Any:
    pages = getattr(service, "page_service", None)
    if pages is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Page management is unavailable."
        )
    return pages


def require_page(pages: Any, page_id: str) -> Any:
    page = next((item for item in pages.list_pages() if item.id == page_id), None)
    if page is None:
        raise invalid("The requested page does not exist.", "pageId")
    return page


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    pages = require_pages(service)
    if args["action"] == "list":
        return {
            "pages": to_jsonable(pages.list_pages()),
            "activePageId": pages.get_active_page_id(),
            "revision": current_revision(service),
        }
    async with edit_operation(service, name, args):
        pages = require_pages(service)
        action = args["action"]
        page_id = args.get("pageId")
        if page_id is not None:
            require_page(pages, page_id)
        before = [page.id for page in pages.list_pages()]
        if action == "add":
            page_id = _add_page(pages, args, before)
            changed = True
        elif action == "duplicate":
            page_id = pages.duplicate_page(page_id)
            changed = page_id not in before
            if not changed:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "The page could not be duplicated."
                )
        else:
            changed = _edit_existing(pages, args, before)
        return {
            "status": "applied" if changed else "noop",
            "pageId": page_id,
            "activePageId": pages.get_active_page_id(),
            "revision": current_revision(service),
        }


def _add_page(pages: Any, args: dict[str, Any], before: list[str]) -> str:
    after = args.get("afterPageId")
    if after is not None:
        require_page(pages, after)
    template = args.get("templateId", "builtin.blank_like_current")
    if template not in {item.id for item in pages.list_page_templates()}:
        raise invalid("The requested page template does not exist.", "templateId")
    page_id = pages.add_page_from_template(template, after_page_id=after)
    if page_id in before:
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The page could not be created.")
    return page_id


def _edit_existing(pages: Any, args: dict[str, Any], before: list[str]) -> bool:
    page_id = args["pageId"]
    action = args["action"]
    if action == "delete":
        if len(before) <= 1:
            raise invalid("The final page cannot be deleted.", "pageId")
        pages.delete_page(page_id)
        if page_id in {item.id for item in pages.list_pages()}:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The page could not be deleted.")
        return True
    if action == "rename":
        old_name = require_page(pages, page_id).name
        pages.rename_page(page_id, args["name"])
        if require_page(pages, page_id).name != args["name"]:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The page could not be renamed.")
        return old_name != args["name"]
    if action == "move":
        if args["index"] >= len(before):
            raise invalid("'index' must refer to an existing logical page position.", "index")
        pages.move_page(page_id, args["index"])
        after_order = [item.id for item in pages.list_pages()]
        if after_order[args["index"]] != page_id:
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The page could not be moved.")
        return before != after_order
    changed = pages.get_active_page_id() != page_id
    pages.set_active_page(page_id)
    if pages.get_active_page_id() != page_id:
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "The page could not be activated.")
    return changed
