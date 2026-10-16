"""Carry object selection into built-in text tools without path-edit handles."""

from collections.abc import Mapping
from copy import deepcopy


def capture(canvas, target_tool_id):
    selection = getattr(canvas, "_vector_selection", None)
    if (
        target_tool_id == "vector_path_edit_tool"
        and isinstance(selection, Mapping)
        and selection.get("auto_return_tool") == "comic_tool"
    ):
        return deepcopy(dict(selection))
    if target_tool_id not in {"text_tool", "text_tool_vertical"}:
        return None
    if not isinstance(selection, Mapping) or selection.get("owner") == "text_tool_inline":
        return None
    return deepcopy(
        {key: selection[key] for key in ("id", "ids", "items", "layer_id") if key in selection}
    )


def restore(canvas, selection):
    # Tools which already preserve selection keep their richer current state.
    if selection and canvas is not None and getattr(canvas, "_vector_selection", None) is None:
        canvas._vector_selection = selection
