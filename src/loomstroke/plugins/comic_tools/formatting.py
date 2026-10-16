"""Scoped comic typography changes through the existing balloon command builder."""

from dataclasses import fields, replace

from .service import TextProps, text_props_from_object

FORMAT_FIELDS = frozenset(field.name for field in fields(TextProps)) - {"text"}


def apply_format(tool, page_id, balloon_id, changes):
    """Patch only explicit fields; never replace text or change the active page."""
    if not changes.keys() <= FORMAT_FIELDS:
        raise ValueError("Unsupported comic format field")
    if tool.host_api.pages.get_active_page_id() != page_id:
        raise ValueError("The active page changed")
    state = tool.read_state()
    if state is None or balloon_id not in state.balloons:
        raise ValueError("The balloon no longer exists")
    props = text_props_from_object(state.text_objects.get(balloon_id))
    updated = replace(props, **changes)
    if props == updated:
        return False
    command = tool.controller.update_balloon_command(
        state, state.balloons[balloon_id], text=props.text, text_props=updated
    )
    if not tool._submit(command):
        raise ValueError("The format could not be updated")
    return True
