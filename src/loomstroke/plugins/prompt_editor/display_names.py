"""Optional LivePrompt display names; generation always uses the stored values."""

from __future__ import annotations

from typing import Any

from loomstroke.plugins.common.constants import CK_LIST, CK_STRING
from loomstroke.plugins.common.snippet_models import Snippet

STRING_DISPLAY_NAME_KEY = "liveprompt_display_name"
LIST_DISPLAY_NAMES_KEY = "liveprompt_item_display_names"


def resolved_list_items(snippet: Snippet | None) -> list[str]:
    """Return list candidates in the same order as the existing chip selector."""
    if snippet is None:
        return []
    items = []
    for value in snippet.list_items:
        for part in str(value).split(","):
            stripped = part.strip()
            if stripped:
                items.append(stripped)
    if items:
        return items
    body = (snippet.body or "").strip()
    if not body:
        return []
    if "," in body:
        parts = []
        for line in body.splitlines():
            parts.extend([part.strip() for part in line.split(",") if part.strip()])
        if parts:
            return parts
    return [line.strip() for line in body.splitlines() if line.strip()]


def display_name_for(snippet: Snippet | None, kind: str, index: int = 0) -> str:
    """Read a name only when its referenced value still matches the saved item."""
    if snippet is None:
        return ""
    metadata = snippet.metadata if isinstance(snippet.metadata, dict) else {}
    if kind == CK_STRING:
        name = metadata.get(STRING_DISPLAY_NAME_KEY)
        return name.strip() if isinstance(name, str) else ""
    if kind != CK_LIST:
        return ""
    entries = metadata.get(LIST_DISPLAY_NAMES_KEY)
    items = resolved_list_items(snippet)
    if not isinstance(entries, list) or not 0 <= index < len(items) or index >= len(entries):
        return ""
    entry: Any = entries[index]
    if not isinstance(entry, dict) or entry.get("value") != items[index]:
        return ""
    name = entry.get("display_name")
    return name.strip() if isinstance(name, str) else ""


def store_display_names(
    metadata: dict[str, Any], kind: str, values: list[str], names: list[str]
) -> None:
    """Replace only the optional display-name metadata for the selected kind."""
    metadata.pop(STRING_DISPLAY_NAME_KEY, None)
    metadata.pop(LIST_DISPLAY_NAMES_KEY, None)
    if kind == CK_STRING:
        name = names[0].strip() if names else ""
        if name:
            metadata[STRING_DISPLAY_NAME_KEY] = name
    elif kind == CK_LIST:
        entries = [
            {"value": value, "display_name": name.strip()}
            for value, name in zip(values, names, strict=False)
        ]
        if any(entry["display_name"] for entry in entries):
            metadata[LIST_DISPLAY_NAMES_KEY] = entries
