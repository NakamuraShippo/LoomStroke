"""Selection-scoped ruby and tate-chu-yoko edits through text commands/history."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any

from loomstroke.core.japanese_text.annotations import (
    ANNOTATION_KEYS,
    annotation_edit_error,
    annotation_selection,
    normalize_annotations,
    replace_annotation_range,
)
from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .common import TEXT_META_KEY, ensure_text_vector_object, extract_text_payload
from .editing_history import TextEditMetadataHistory
from .editing_metadata import replace_edited_text


class TextAnnotationsController:
    """Use the current inline selection, never a saved/stale UI range."""

    def __init__(self, tool: Any) -> None:
        self._tool = tool
        self._last_error: str | None = None
        self._last_selection: tuple[int, int, int] | None = None

    def _selection(self) -> tuple[int, int] | None:
        tool = self._tool
        editor, state = tool._inline_editor, tool._inline_state
        if editor is None or state is None or tool._transform_state is not None:
            return None
        if (
            editor.preedit_text()
            or not tool._edit_lifecycle.same_page()
            or not tool._edit_lifecycle.can_edit_layer(state.layer_id)
        ):
            return None
        selected = tool._inline_selection_range(editor)
        if selected is None:
            return None
        text = editor.toPlainText()
        bounds = annotation_selection(text, *selected)
        if bounds is None or any(
            char in text[bounds[0] : bounds[1]] for char in "\r\n\u2028\u2029"
        ):
            return None
        return bounds

    def selection_state(self) -> dict[str, Any]:
        selected = self._selection()
        signature = (id(self._tool._inline_state), *selected) if selected is not None else None
        if signature != self._last_selection:
            self._last_selection = signature
            self._last_error = None
        if selected is None:
            return {"enabled": False}
        tool = self._tool
        payload = extract_text_payload(tool._inline_state.object_ref) or {}
        document = payload.get("document")
        document = dict(document) if isinstance(document, Mapping) else {}
        document["text"] = tool._inline_editor.toPlainText()
        payload["document"] = document
        start, end = selected
        result: dict[str, Any] = {"enabled": True, "start": start, "end": end}
        result["error"] = self._last_error
        result["ruby_error"] = annotation_edit_error(
            payload, "ruby_runs", start, end, {"ruby": "?"}
        )
        result["tcy_error"] = annotation_edit_error(payload, "tcy_runs", start, end, {})
        for kind in ANNOTATION_KEYS:
            for run in normalize_annotations(
                document.get(kind), tool._inline_editor.toPlainText(), kind=kind
            ):
                if run["start"] == start and run["end"] == end:
                    result[kind] = deepcopy(dict(run))
                    break
        return result

    def apply(self, kind: str, request: Any) -> None:
        """Add or remove an annotation without replacing the editor selection."""
        selected = self._selection()
        run_key = {name.removesuffix("_runs"): name for name in ANNOTATION_KEYS}.get(kind)
        if selected is None or not isinstance(request, Mapping) or run_key is None:
            return
        self._last_selection = (id(self._tool._inline_state), *selected)
        action = request.get("action", "set")
        if action not in {"set", "remove"}:
            return
        options = {key: value for key, value in request.items() if key != "action"}
        tool = self._tool
        editor, state = tool._inline_editor, tool._inline_state
        if kind == "tcy" and action == "set" and not tool._inline_effective_settings().vertical:
            return
        content = editor.toPlainText()
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
        payload = extract_text_payload(obj)
        if payload is None:
            return
        selected_options = None if action == "remove" else options
        self._last_error = annotation_edit_error(payload, run_key, *selected, selected_options)
        if self._last_error is not None:
            tool._properties.sync(force=True)
            return
        updated = replace_annotation_range(payload, run_key, *selected, selected_options)
        if updated == payload:
            return
        meta = deepcopy(obj.meta)
        meta[TEXT_META_KEY] = updated
        new_obj = replace(obj, meta=meta)
        if state.metadata_history is None:
            state.metadata_history = TextEditMetadataHistory(
                obj, undo_steps=editor.document().availableUndoSteps()
            )
        state.metadata_history.apply_annotation_change(
            run_key, *selected, selected_options, new_obj
        )
        state.object_ref = new_obj
        if state.object_id is not None and state.layer_id is not None:
            tool._submit_command(
                VectorReplaceCommand(
                    target_id=state.object_id, new_object=new_obj, layer_id=state.layer_id
                )
            )
        tool._refresh_inline_editor_after_setting_change()
        tool._properties.sync(force=True)
