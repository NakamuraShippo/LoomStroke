"""Guarded, command-backed editing of a dialogue row on its owning page."""

from dataclasses import dataclass
from typing import Any

from .service import text_props_from_object


@dataclass(frozen=True)
class DialogueEdit:
    page_id: str
    balloon_id: str
    text: str


def begin_edit(tool: Any, page_id: str, balloon_id: str) -> DialogueEdit:
    pages = tool.host_api.pages
    if pages.get_active_page_id() != page_id:
        pages.set_active_page(page_id)
    if pages.get_active_page_id() != page_id:
        raise ValueError("The dialogue page could not be opened.")
    state = tool.read_state()
    if state is None or balloon_id not in state.balloons:
        raise ValueError("The balloon no longer exists.")
    obj = state.text_objects.get(balloon_id)
    return DialogueEdit(page_id, balloon_id, text_props_from_object(obj).text if obj else "")


def commit_edit(tool: Any, edit: DialogueEdit, text: str) -> bool:
    """Return whether a command was submitted; reject stale or misrouted edits."""
    if text == edit.text:
        return False
    if tool.host_api.pages.get_active_page_id() != edit.page_id:
        raise ValueError("The active page changed. Reopen the dialogue to edit it.")
    state = tool.read_state()
    if state is None or edit.balloon_id not in state.balloons:
        raise ValueError("The balloon no longer exists.")
    obj = state.text_objects.get(edit.balloon_id)
    current = text_props_from_object(obj).text if obj else ""
    if current != edit.text:
        raise ValueError("The dialogue changed elsewhere. Reopen it before editing.")
    if not tool.set_balloon_text(edit.balloon_id, text):
        raise ValueError("The dialogue could not be updated.")
    return True
