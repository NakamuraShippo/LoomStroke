"""Apply character and paragraph overrides through existing text history."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace

from loomstroke.core.japanese_text.annotations import annotation_selection
from loomstroke.core.japanese_text.character_fonts import FONT_KEYS, font_runs, valid_font_value
from loomstroke.core.japanese_text.character_geometry import (
    GEOMETRY_LIMITS,
    geometry_runs,
    geometry_values,
)
from loomstroke.core.japanese_text.character_styles import (
    apply_character_override,
    color_at,
    color_runs,
)
from loomstroke.core.japanese_text.named_styles import (
    apply_named_style,
    capture_style,
    named_styles,
)
from loomstroke.core.japanese_text.paragraph_styles import (
    apply_paragraph_alignment,
    apply_paragraph_style,
    paragraph_style,
)
from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .common import TEXT_META_KEY, ensure_text_vector_object
from .editing_history import TextEditMetadataHistory
from .editing_metadata import replace_edited_text


def selection_color(tool, default):
    editor, state = tool._inline_editor, tool._inline_state
    if editor is None or state is None:
        return default, False
    selected = tool._inline_selection_range(editor)
    active = selected is not None or bool(editor.preedit_text())
    if selected is None or state.object_ref is None:
        return default, active
    payload = state.object_ref.meta.get(TEXT_META_KEY, {})
    return color_at(color_runs(payload), selected[0], default), active


def apply_selection_color(tool, color):
    return apply_selection_style(tool, "color_rgba", color)


def selection_font_values(tool):
    editor, state = tool._inline_editor, tool._inline_state
    if editor is None or state is None or state.object_ref is None:
        return {}
    selected = tool._inline_selection_range(editor)
    if selected is None:
        return {}
    runs = font_runs(state.object_ref.meta.get(TEXT_META_KEY, {}))
    values = next((values for start, end, values in runs if start <= selected[0] < end), {})
    payload = state.object_ref.meta[TEXT_META_KEY]
    return {
        **values,
        **next(
            (v for a, b, v in geometry_runs(payload) if a <= selected[0] < b), geometry_values({})
        ),
        **paragraph_style(payload, selected[0]),
        "named_styles": named_styles(payload),
    }


def apply_selection_style(tool, key, value):  # noqa: PLR0911 - edit lifecycle guards
    """True means the range edit handled or deliberately rejected the request."""
    editor, state = tool._inline_editor, tool._inline_state
    if key == "named_style" and not isinstance(value, Mapping):
        return True
    if editor is None or state is None:
        return False
    if editor.preedit_text():
        tool._properties.sync(force=True)
        return True
    selected = tool._inline_selection_range(editor)
    if selected is None:
        return False
    if not tool._edit_lifecycle.same_page() or not tool._edit_lifecycle.can_edit_layer(
        state.layer_id
    ):
        return True
    content = editor.toPlainText()
    bounds = annotation_selection(content, *selected)
    if bounds is None:
        return True
    obj = state.object_ref
    if obj is None:
        obj = ensure_text_vector_object(
            object_id=state.object_id or "text-inline-preview",
            content=content,
            rect=state.rect,
            settings=tool._inline_effective_settings(),
            word_wrap=state.word_wrap,
            rotation_deg=state.rotation_deg,
        )
    obj = replace_edited_text(obj, content)
    payload = obj.meta[TEXT_META_KEY]
    if key == "named_style":
        value = _named_request(payload, bounds[0], value)
    value = _validated_value(key, value)
    updated = _range_override(payload, bounds, key, value)
    if updated == payload:
        return True
    meta = deepcopy(obj.meta)
    meta[TEXT_META_KEY] = updated
    new_obj = replace(obj, meta=meta)
    if state.metadata_history is None:
        state.metadata_history = TextEditMetadataHistory(
            obj, undo_steps=editor.document().availableUndoSteps()
        )
    state.metadata_history.apply_range_format(key, *bounds, value, new_obj)
    state.object_ref = new_obj
    if state.object_id is not None and state.layer_id is not None:
        tool._submit_command(
            VectorReplaceCommand(
                target_id=state.object_id, new_object=new_obj, layer_id=state.layer_id
            )
        )
    tool._refresh_inline_editor_after_setting_change()
    tool._properties.sync(force=True)
    return True


def _validated_value(key, value):
    if key in FONT_KEYS:
        return valid_font_value(key, value)
    if key in GEOMETRY_LIMITS:
        return geometry_values({key: value})[key]
    return value


def _range_override(payload, bounds, key, value):
    paragraph_keys = {
        "paragraph_flow": "vertical_flow",
        "paragraph_typography": "japanese",
        "paragraph_columns": "paragraph_columns",
    }
    if key in paragraph_keys:
        return apply_paragraph_style(payload, *bounds, {paragraph_keys[key]: value})
    if key == "named_style":
        return apply_named_style(payload, *bounds, value)
    if key == "paragraph_align":
        return apply_paragraph_alignment(payload, *bounds, value)
    return apply_character_override(payload, *bounds, key, value)


def _named_request(payload, index, request):
    value = deepcopy(request)
    if value.get("action") == "register":
        value["definition"] = {
            "name": value.get("name", ""),
            "kind": value.get("kind", "character"),
            "values": capture_style(payload, index, value.get("kind", "character")),
        }
    return value
