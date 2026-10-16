"""Undoable book layout, spread, and master-page operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from uuid import uuid4

from loomstroke.core.book_layout_service import BookLayoutService
from loomstroke.core.book_spread_layout import resolve_spreads
from loomstroke.core.master_page_apply import capture_master_layers_from_page
from loomstroke.core.master_page_model import (
    MasterDefinition,
    masters_namespace_payload,
    read_master_definitions,
)

from .editing_common import current_revision, edit_operation, invalid
from .extension_services import require_service
from .page_tools import require_page, require_pages
from .paint_extension_arguments import ID, NAME, action_schema, object_schema, validate
from .serialization import to_jsonable
from .types import McpStructuredError, McpToolSpec, ReasonCode

LAYOUT_SCHEMA = object_schema(
    {
        "binding": {"enum": ["left", "right"]},
        "body_start": {"enum": ["auto", "recto", "verso"]},
        "default_view": {"enum": ["single", "spread"]},
        "folio_start": {"type": "integer", "minimum": 1},
        "folio_prefix": {"type": "string", "maxLength": 256},
        "folio_hidden_roles": {"type": "array", "items": ID, "uniqueItems": True, "maxItems": 128},
    }
)
ATTRIBUTES_SCHEMA = object_schema(
    {
        "role": ID,
        "folio_mode": {"enum": ["auto", "hidden", "custom"]},
        "custom_folio": {"type": ["string", "null"], "maxLength": 256},
    }
)
TOOLS = (
    McpToolSpec(
        "loomstroke_manage_book",
        "Read/update book binding, body start and folios; read/update page roles/folios; "
        "move a derived spread as one Undo entry. Spread slots are derived and never create pages.",
        action_schema(
            {
                "get": ({}, ()),
                "set_layout": ({"layout": LAYOUT_SCHEMA}, ("layout",)),
                "set_page_attributes": (
                    {"pageId": ID, "attributes": ATTRIBUTES_SCHEMA},
                    ("pageId", "attributes"),
                ),
                "move_spread": (
                    {
                        "spreadIndex": {"type": "integer", "minimum": 0},
                        "index": {"type": "integer", "minimum": 0},
                    },
                    ("spreadIndex", "index"),
                ),
            }
        ),
    ),
    McpToolSpec(
        "loomstroke_manage_masters",
        "List, capture/refresh vector content from a page, rename, delete, apply, or unlink "
        "document masters. Capture matches the GUI's vector-only master workflow; "
        "all edits use document Undo/Redo.",
        action_schema(
            {
                "list": ({}, ()),
                "capture": (
                    {
                        "pageId": ID,
                        "name": NAME,
                        "masterId": ID,
                        "targetRoles": {
                            "type": "array",
                            "items": ID,
                            "uniqueItems": True,
                            "maxItems": 128,
                        },
                    },
                    ("pageId", "name"),
                ),
                "rename": ({"masterId": ID, "name": NAME}, ("masterId", "name")),
                "delete": ({"masterId": ID}, ("masterId",)),
                "apply": (
                    {
                        "masterId": ID,
                        "pageIds": {
                            "type": "array",
                            "items": ID,
                            "minItems": 1,
                            "maxItems": 10000,
                            "uniqueItems": True,
                        },
                    },
                    ("masterId", "pageIds"),
                ),
                "unlink": ({"masterId": ID, "pageId": ID}, ("masterId", "pageId")),
            }
        ),
    ),
)


def _state(service: Any) -> tuple[Any, Any, dict[str, Any], tuple[Any, ...]]:
    pages = require_pages(service)
    document = require_service(service, "document_service")
    resolver = BookLayoutService()
    attributes = resolver.resolve_page_attributes_by_id(
        {page.id: pages.get_page_metadata(page.id) for page in pages.list_pages()}
    )
    layout = document.resolve_book_layout()
    spreads = resolve_spreads([page.id for page in pages.list_pages()], layout, attributes)
    return pages, layout, attributes, spreads


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = validate(TOOLS, name, arguments)
    if name == "loomstroke_manage_masters":
        return await _masters(service, name, args)
    pages, layout, attributes, spreads = _state(service)
    if args["action"] == "get":
        return {
            "layout": to_jsonable(layout),
            "pageAttributes": to_jsonable(attributes),
            "spreads": to_jsonable(spreads),
            "revision": current_revision(service),
        }
    async with edit_operation(service, name, args):
        pages, _, _, spreads = _state(service)
        action = args["action"]
        if action == "set_layout":
            if not callable(getattr(pages, "_book_metadata_apply_transition", None)):
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE, "Book metadata is not attached."
                )
            changed = pages.set_book_layout(args["layout"])
        elif action == "set_page_attributes":
            require_page(pages, args["pageId"])
            changed = pages.set_page_book_attributes(args["pageId"], args["attributes"])
        else:
            if args["spreadIndex"] >= len(spreads):
                raise invalid("The requested spread does not exist.", "spreadIndex")
            members = spreads[args["spreadIndex"]].page_ids()
            if args["index"] > len(pages.list_pages()) - len(members):
                raise invalid("Spread destination exceeds the remaining page sequence.", "index")
            changed = pages._move_spread(members, args["index"])
        return {"status": "applied" if changed else "noop", "revision": current_revision(service)}


def _definitions(service: Any) -> list[MasterDefinition]:
    document = require_service(service, "document_service")
    return list(
        read_master_definitions(
            {"plugins": {"loomstroke.comictools.masters": document.read_masters_metadata()}}
        )
    )


async def _masters(service: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    masters = _definitions(service)
    if args["action"] == "list":
        return {
            "masters": masters_namespace_payload(masters)["masters"],
            "revision": current_revision(service),
        }
    async with edit_operation(service, name, args):
        pages = require_pages(service)
        masters = _definitions(service)
        master_id = args.get("masterId")
        old = next((master for master in masters if master.id == master_id), None)
        if master_id is not None and old is None:
            raise invalid("The requested master does not exist.", "masterId")
        action = args["action"]
        if action in {"capture", "rename", "delete"}:
            if not callable(getattr(pages, "_masters_metadata_apply_transition", None)):
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE, "Master metadata is not attached."
                )
            if action == "capture":
                replacement = _capture_master(pages, args, old)
                master_id = replacement.id
                masters = [replacement if master.id == master_id else master for master in masters]
                masters += [] if old is not None else [replacement]
            elif action == "rename":
                masters = [
                    replace(master, name=args["name"]) if master.id == master_id else master
                    for master in masters
                ]
            else:
                masters = [master for master in masters if master.id != master_id]
            changed = pages.set_masters_namespace(masters_namespace_payload(masters))
        elif action == "apply":
            for page_id in args["pageIds"]:
                require_page(pages, page_id)
            changed = pages.apply_master_to_pages(
                masters_namespace_payload([old])["masters"][0], args["pageIds"]
            )
            if not changed:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED, "The master could not be applied."
                )
        else:
            require_page(pages, args["pageId"])
            changed = pages.unlink_master_instance(args["pageId"], master_id)
            if not changed:
                raise invalid("This page has no instance of the requested master.", "masterId")
        return {
            "status": "applied" if changed else "noop",
            "masterId": master_id,
            "revision": current_revision(service),
        }


def _capture_master(pages: Any, args: dict[str, Any], old: Any) -> MasterDefinition:
    require_page(pages, args["pageId"])
    if pages.get_active_page_id() == args["pageId"]:
        pages.persist_active_page()
    page = pages.get_page_snapshot(args["pageId"])
    if page is None:
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Page content could not be loaded.")
    return MasterDefinition(
        args.get("masterId") or f"master-{uuid4().hex}",
        args["name"],
        old.revision + 1 if old else 1,
        tuple(args.get("targetRoles", old.target_roles if old else ())),
        capture_master_layers_from_page(page),
    )
