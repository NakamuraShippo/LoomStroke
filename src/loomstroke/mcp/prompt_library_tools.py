"""MCP authoring tools for the existing Prompt Editor snippet library."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from loomstroke.plugins.common.constants import (
    ALL_SNIPPET_KINDS,
    CK_LIST,
    CK_PROMPT,
    CK_STRING,
    CK_TEXT,
    MAX_PROMPT_LENGTH,
    MAX_SNIPPET_LENGTH,
    MAX_SNIPPETS_PER_PROMPT,
    SK_LIST,
    SK_PROMPT,
    SK_TEXT,
)
from loomstroke.plugins.common.liveprompt_defaults import default_separator
from loomstroke.plugins.common.snippet_models import Snippet
from loomstroke.plugins.common.snippet_repository import SnippetRepository

from .editing_common import edit_operation, invalid
from .extension_services import get_services
from .types import McpStructuredError, McpToolSpec, ReasonCode

_TEXT = {"type": "string", "minLength": 1}
_TAGS = {"type": "array", "items": _TEXT, "maxItems": 50}
logger = logging.getLogger(__name__)
_COMPONENT = {
    "type": "object",
    "required": ["kind"],
    "properties": {
        "kind": {"type": "string", "enum": [CK_STRING, CK_LIST, CK_TEXT, CK_PROMPT]},
        "snippetId": _TEXT,
        "listItemIndex": {"type": "integer", "minimum": 0},
        "text": {"type": "string"},
        "tagFilter": {"type": "string"},
        "selectionMode": {
            "type": "string",
            "enum": ["fixed", "random", "sequential", "combinatorial"],
        },
    },
    "additionalProperties": False,
}


def _tool(name: str, description: str, properties: dict, required: list, *, read_only=False):
    return McpToolSpec(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        read_only=read_only,
        destructive=not read_only,
    )


TOOLS = (
    _tool(
        "loomstroke_list_prompt_library",
        "List Prompt Editor Library entries, including IDs for composition references.",
        {
            "kind": {"type": "string", "enum": list(ALL_SNIPPET_KINDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        [],
        read_only=True,
    ),
    _tool(
        "loomstroke_get_prompt_library_entry",
        "Read a Prompt Editor Library entry and its saved composition metadata.",
        {"snippetId": _TEXT},
        ["snippetId"],
        read_only=True,
    ),
    _tool(
        "loomstroke_create_prompt_library_entry",
        "Create a String, List, or LLM prompt entry in the Prompt Editor Library.",
        {
            "kind": {
                "type": "string",
                "enum": [kind for kind in ALL_SNIPPET_KINDS if kind != SK_PROMPT],
            },
            "title": _TEXT,
            "body": {"type": "string"},
            "tags": _TAGS,
            "listItems": {"type": "array", "items": _TEXT, "maxItems": 100},
        },
        ["kind", "title"],
    ),
    _tool(
        "loomstroke_create_prompt_composition",
        "Create a saved Prompt Editor composition from inline text and Library references.",
        {
            "title": _TEXT,
            "tags": _TAGS,
            "separator": {"type": "string"},
            "components": {
                "type": "array",
                "items": _COMPONENT,
                "minItems": 1,
                "maxItems": MAX_SNIPPETS_PER_PROMPT,
            },
        },
        ["title", "components"],
    ),
)


def _repository(service: Any) -> SnippetRepository:
    return SnippetRepository(service.safety.settings)


def _describe(snippet: Snippet) -> dict[str, Any]:
    return {
        "snippetId": snippet.id,
        "kind": snippet.kind,
        "title": snippet.title,
        "body": snippet.body,
        "tags": list(snippet.tags),
        "listItems": list(snippet.list_items),
        "metadata": dict(snippet.metadata),
    }


def _not_found() -> McpStructuredError:
    return McpStructuredError(ReasonCode.NOT_FOUND, "Prompt Library entry was not found.")


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise invalid(f"'{field}' must be non-empty and at most {limit} characters.", field)
    return value.strip()


def _tags(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise invalid("'tags' must be a list of at most 50 strings.", "tags")
    return [_text(tag, "tags", 100) for tag in value]


def _separator(raw: str) -> str:
    if not raw:
        return "\n"
    result = raw
    for escape, char in ((r"\n", "\n"), (r"\t", "\t"), (r"\r", "\r"), (r"\\", "\\")):
        result = result.replace(escape, char)
    return result


def _items(snippet: Snippet) -> list[str]:
    values = [
        part.strip() for item in snippet.list_items for part in item.split(",") if part.strip()
    ]
    if values:
        return values
    body = snippet.body.strip()
    if "," in body:
        values = [
            part.strip() for line in body.splitlines() for part in line.split(",") if part.strip()
        ]
    return values or [line.strip() for line in body.splitlines() if line.strip()]


def _composition(  # noqa: PLR0912
    repo: SnippetRepository, raw_components: list[Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    metadata: list[dict[str, Any]] = []
    parts: list[str] = []
    for index, raw in enumerate(raw_components):
        if not isinstance(raw, dict):
            raise invalid("A component must be an object.", f"components[{index}]")
        kind = raw.get("kind")
        if kind not in (CK_STRING, CK_LIST, CK_TEXT, CK_PROMPT):
            raise invalid("Unknown component kind.", f"components[{index}].kind")
        entry: dict[str, Any] = {"kind": kind}
        if kind == CK_TEXT:
            if "snippetId" in raw or "listItemIndex" in raw or "tagFilter" in raw:
                raise invalid(
                    "Inline text cannot reference a Library entry.", f"components[{index}]"
                )
            rendered = _text(raw.get("text"), f"components[{index}].text", MAX_SNIPPET_LENGTH)
            entry["text_value"] = rendered
        else:
            if "text" in raw:
                raise invalid(
                    "Referenced components cannot contain inline text.",
                    f"components[{index}].text",
                )
            snippet_id = _text(raw.get("snippetId"), f"components[{index}].snippetId", 200)
            snippet = repo.get_snippet(snippet_id)
            if snippet is None:
                raise _not_found()
            expected_kind = {CK_STRING: SK_TEXT, CK_LIST: SK_LIST, CK_PROMPT: SK_PROMPT}[kind]
            if snippet.kind != expected_kind:
                raise invalid(
                    "Referenced Library entry has a different kind.",
                    f"components[{index}].snippetId",
                )
            entry["snippet_id"] = snippet_id
            if kind == CK_LIST:
                items = _items(snippet)
                item_index = raw.get("listItemIndex", 0)
                if (
                    isinstance(item_index, bool)
                    or not isinstance(item_index, int)
                    or not 0 <= item_index < len(items)
                ):
                    raise invalid(
                        "List item index is out of range.",
                        f"components[{index}].listItemIndex",
                    )
                entry["list_item_index"] = item_index
                rendered = items[item_index]
            else:
                rendered = (snippet.body or snippet.title).strip()
            if raw.get("tagFilter"):
                entry["tag_filter"] = _text(raw["tagFilter"], f"components[{index}].tagFilter", 100)
        mode = raw.get("selectionMode", "fixed")
        if mode not in ("fixed", "random", "sequential", "combinatorial"):
            raise invalid("Unknown selection mode.", f"components[{index}].selectionMode")
        if mode != "fixed":
            entry["selection_mode"] = mode
        metadata.append(entry)
        parts.append(rendered)
    return metadata, parts


def _notify_live_panel(service: Any) -> bool:
    callback = get_services(service).get("prompt_editor_library_changed")
    if callable(callback):
        try:
            callback()
        except Exception:
            logger.exception("Prompt Editor Library refresh failed after MCP save")
            return False
    return True


async def dispatch(  # noqa: PLR0912, PLR0915 - schema branches and creation validation
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    args = dict(arguments)
    if name == "loomstroke_list_prompt_library":
        kind = args.get("kind")
        if kind is not None and kind not in ALL_SNIPPET_KINDS:
            raise invalid("Unknown Library entry kind.", "kind")
        limit = args.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise invalid("'limit' must be between 1 and 100.", "limit")
        snippets = _repository(service).list_snippets(kinds=[kind] if kind else ALL_SNIPPET_KINDS)
        return {
            "entries": [
                {"snippetId": s.id, "kind": s.kind, "title": s.title, "tags": s.tags}
                for s in snippets[:limit]
            ]
        }
    if name == "loomstroke_get_prompt_library_entry":
        snippet_id = _text(args.get("snippetId"), "snippetId", 200)
        snippet = _repository(service).get_snippet(snippet_id)
        if snippet is None:
            raise _not_found()
        return {"entry": _describe(snippet)}
    title = _text(args.get("title"), "title", 200)
    tags = _tags(args.get("tags"))
    if name == "loomstroke_create_prompt_library_entry":
        kind = args.get("kind")
        if kind not in ALL_SNIPPET_KINDS or kind == SK_PROMPT:
            raise invalid("Unknown or unsupported Library entry kind.", "kind")
        raw_items = args.get("listItems", [])
        if not isinstance(raw_items, list) or len(raw_items) > 100:
            raise invalid("'listItems' must contain at most 100 strings.", "listItems")
        if kind != SK_LIST and raw_items:
            raise invalid("Only List entries accept listItems.", "listItems")
        items = [_text(item, "listItems", MAX_SNIPPET_LENGTH) for item in raw_items]
        body = args.get("body", "")
        if not isinstance(body, str):
            raise invalid("'body' must be text.", "body")
        body = body.strip() or ("\n".join(items) if kind == SK_LIST else "")
        body = _text(body, "body", MAX_SNIPPET_LENGTH)
        snippet = Snippet(
            id=f"mcp-snippet-{uuid.uuid4().hex}",
            title=title,
            body=body,
            tags=tags,
            kind=kind,
            category=kind,
            list_items=tuple(items),
        )
    elif name == "loomstroke_create_prompt_composition":
        raw_components = args.get("components")
        if (
            not isinstance(raw_components, list)
            or not 1 <= len(raw_components) <= MAX_SNIPPETS_PER_PROMPT
        ):
            raise invalid("'components' must contain 1 to 50 entries.", "components")
        raw_separator = args.get("separator", default_separator(service.safety.settings))
        if not isinstance(raw_separator, str) or len(raw_separator) > 100:
            raise invalid("'separator' must be at most 100 characters.", "separator")
        metadata, parts = _composition(_repository(service), raw_components)
        body = _separator(raw_separator).join(part for part in parts if part)
        body = _text(body, "components", MAX_PROMPT_LENGTH)
        snippet = Snippet(
            id=f"prompt-{uuid.uuid4().hex[:12]}",
            title=title,
            body=body,
            tags=tags,
            kind=SK_PROMPT,
            category=SK_PROMPT,
            metadata={"components": metadata, "tags": tags, "separator": raw_separator},
        )
    else:
        raise invalid("Unknown Prompt Library operation.")
    async with edit_operation(service, name, args, require_canvas=False):
        if name == "loomstroke_create_prompt_composition":
            current_metadata, current_parts = _composition(_repository(service), raw_components)
            if (current_metadata, current_parts) != (metadata, parts):
                raise McpStructuredError(
                    ReasonCode.CONFLICT,
                    "A referenced Library entry changed during confirmation; retry.",
                )
        saved = _repository(service).save_snippet(snippet)
        refreshed = _notify_live_panel(service)
        result = {"entry": _describe(saved)}
        if not refreshed:
            result["warnings"] = ["Library saved but the open Prompt Editor did not refresh."]
        return result
