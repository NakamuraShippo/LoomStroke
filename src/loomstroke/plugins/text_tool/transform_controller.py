"""Text transform state owner for ``TextTool``."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loomstroke.plugins.sdk.transform import (
    TransformGestureState,
    compute_pivot,
    preview_transform_gesture,
)
from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .common import extract_text_content, extract_word_wrap
from .editing_history import TextEditMetadataHistory
from .editing_metadata import replace_edited_text
from .properties_controller import replace_text_property


@dataclass(slots=True)
class TextTransformState:
    hit: Any
    mode: str
    handle: str
    start_rect: tuple[float, float, float, float]
    start_bounds: tuple[float, float, float, float]
    start_angle_deg: float
    press_point: tuple[float, float]
    pivot: tuple[float, float]
    press_vec: tuple[float, float] | None
    preview_rect: tuple[float, float, float, float]
    preview_angle_deg: float
    start_font_size_pt: float
    start_tracking: float
    preview_font_size_pt: float
    preview_tracking: float


class TextTransformController:
    """Own transform drag state and text preview/commit transitions."""

    def __init__(self, owner: Any, *, logger: logging.Logger) -> None:
        self._owner = owner
        self._logger = logger
        self.state: TextTransformState | None = None

    def begin(
        self,
        hit: Any,
        handle: str,
        mode: str,
        press_point: tuple[float, float],
    ) -> None:
        owner = self._owner
        # A drag is a document edit, not part of the open text transaction.
        # Commit text first, then refresh the hit (new text has no real id yet).
        if owner._inline_editor is not None:
            owner._flush_prop_transaction()
            owner._finish_inline_edit(commit=True)
            hit = owner._selected_text_hit()
            if hit is None:
                return
        object_id = str(getattr(hit.obj, "id", ""))
        start_rect = owner._payload_rect(hit.payload, fallback=hit.rect)
        start_angle = owner._payload_rotation(hit.payload)
        start_bounds = owner._rect_to_bounds(start_rect, rotation_deg=start_angle)
        payload_settings = owner._settings_from_payload(hit.payload)
        pivot = compute_pivot(handle, start_bounds)
        press_vec = None
        if mode == "scale":
            press_vec = (press_point[0] - pivot[0], press_point[1] - pivot[1])
        self.state = TextTransformState(
            hit=hit,
            mode=mode,
            handle=handle,
            start_rect=start_rect,
            start_bounds=start_bounds,
            start_angle_deg=start_angle,
            press_point=press_point,
            pivot=pivot,
            press_vec=press_vec,
            preview_rect=start_rect,
            preview_angle_deg=start_angle,
            start_font_size_pt=float(payload_settings.font_size_pt),
            start_tracking=float(payload_settings.tracking),
            preview_font_size_pt=float(payload_settings.font_size_pt),
            preview_tracking=float(payload_settings.tracking),
        )
        if owner._inline_edit_matches_object(object_id):
            owner._set_inline_shape_preview_active(True)
        self.apply_preview()
        if owner.host_api and hasattr(owner.host_api, "events"):
            owner.host_api.events.emit("tool.transform_start", {"tool_id": owner._tool_id})

    def update_preview(
        self,
        current_point: tuple[float, float],
        *,
        uniform: bool = False,
    ) -> None:
        owner = self._owner
        state = self.state
        if state is None:
            return
        mode = state.mode
        preview_rect = state.start_rect
        preview_angle = state.start_angle_deg
        preview_font_size = state.start_font_size_pt
        preview_tracking = state.start_tracking
        if mode == "move":
            dx = current_point[0] - state.press_point[0]
            dy = current_point[1] - state.press_point[1]
            x, y, w, h = state.start_rect
            if owner._is_snap_enabled():
                sx, sy = owner._snap_position(x + dx, y + dy, w, h)
                dx, dy = sx - x, sy - y
            preview_rect = (x + dx, y + dy, w, h)
        elif mode == "scale":
            if state.press_vec is None:
                return
            preview = preview_transform_gesture(
                TransformGestureState(
                    mode="scale",
                    handle=state.handle,
                    start_bounds=state.start_bounds,
                    press_point=state.press_point,
                    pivot=state.pivot,
                    press_vec=state.press_vec,
                ),
                current_point,
                uniform=uniform,
            )
            min_x, min_y, max_x, max_y = preview.bounds
            preview_rect = (
                min_x,
                min_y,
                max(1.0, max_x - min_x),
                max(1.0, max_y - min_y),
            )
        elif mode == "rotate":
            preview = preview_transform_gesture(
                TransformGestureState(
                    mode="rotate",
                    handle=state.handle,
                    start_bounds=state.start_bounds,
                    press_point=state.press_point,
                    pivot=state.pivot,
                    press_vec=state.press_vec,
                ),
                current_point,
                uniform=uniform,
            )
            preview_angle = state.start_angle_deg + preview.angle_delta_deg
        state.preview_rect = preview_rect
        state.preview_angle_deg = preview_angle
        state.preview_font_size_pt = preview_font_size
        state.preview_tracking = preview_tracking
        self.apply_preview()

    def apply_preview(self) -> None:
        owner = self._owner
        if not owner.host_api or self.state is None:
            return
        state = self.state
        object_id = str(getattr(state.hit.obj, "id", ""))
        canvas = getattr(owner.host_api, "canvas", None)
        if canvas is None:
            owner._set_inline_shape_preview_active(False)
            return
        preview_content = extract_text_content(state.hit.payload)
        if owner._inline_edit_matches_object(object_id) and owner._inline_editor is not None:
            preview_content, _ = owner._inline_preview_content_and_cursor(owner._inline_editor)
        preview_obj = self._transformed_object(state, preview_content)
        bounds = owner._rect_to_bounds(
            state.preview_rect,
            rotation_deg=state.preview_angle_deg,
        )
        try:
            canvas._vector_preview = preview_obj  # type: ignore[attr-defined]
            canvas._vector_preview_layer_id = state.hit.layer_id  # type: ignore[attr-defined]
            payload = {
                "id": object_id,
                "path": 0,
                "anchor": 0,
                "layer_id": state.hit.layer_id,
                "preview": True,
                "transform_bounds": bounds,
                "mode": "transform",
                "owner": "text_tool_transform",
            }
            owner._apply_auto_return_metadata(payload)
            canvas._vector_selection = payload  # type: ignore[attr-defined]
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
        except Exception:
            self._logger.debug("TextTool transform preview failed", exc_info=True)

    def commit(self) -> None:
        owner = self._owner
        state = self.state
        if state is None:
            return
        if owner.host_api is None:
            self.state = None
            return
        object_id = str(getattr(state.hit.obj, "id", ""))
        inline_target_active = owner._inline_edit_matches_object(object_id)
        word_wrap = extract_word_wrap(state.hit.payload)
        final_content = extract_text_content(state.hit.payload)
        if inline_target_active and owner._inline_editor is not None:
            candidate = owner._inline_editor.toPlainText()
            if candidate:
                final_content = candidate
        final_obj = self._transformed_object(state, final_content)
        if not (
            owner._inline_state is not None
            and owner._inline_state.object_id is None
            and object_id == "text-inline-preview"
        ):
            cmd = VectorReplaceCommand(
                target_id=object_id,
                new_object=final_obj,
                layer_id=state.hit.layer_id,
            )
            owner._submit_command(cmd)
        self.clear_preview()
        self.state = None
        if inline_target_active and owner._inline_state is not None:
            owner._inline_state.layer_id = state.hit.layer_id
            owner._inline_state.rect = state.preview_rect
            owner._inline_state.rotation_deg = state.preview_angle_deg
            owner._inline_state.object_ref = final_obj
            if owner._inline_state.metadata_history is None and owner._inline_editor is not None:
                owner._inline_state.metadata_history = TextEditMetadataHistory(
                    final_obj,
                    undo_steps=owner._inline_editor.document().availableUndoSteps(),
                )
            owner._inline_state.word_wrap = word_wrap
            owner._update_inline_selection_overlay(preview=True)
            owner._update_inline_text_preview()
            if owner._inline_editor is not None:
                owner._inline_editor.setFocus()
                owner._request_ime_update()
        else:
            owner._set_inline_selection_for_object(
                object_id,
                state.hit.layer_id,
                state.preview_rect,
                rotation_deg=state.preview_angle_deg,
                preview=False,
            )
        if state.mode == "scale":
            owner._sync_scaled_typography_settings(
                font_size_pt=float(state.preview_font_size_pt),
                tracking=float(state.preview_tracking),
            )
        owner._emit_text_editing()

    def _transformed_object(self, state: TextTransformState, content: str) -> Any:
        source = state.hit.obj
        owner = self._owner
        object_id = str(getattr(source, "id", ""))
        if (
            owner._inline_edit_matches_object(object_id)
            and owner._inline_state is not None
            and owner._inline_state.object_ref is not None
        ):
            source = owner._inline_state.object_ref
        result = replace_edited_text(
            source, content, rect=state.preview_rect, rotation_deg=state.preview_angle_deg
        )
        for key, before, after in (
            ("font_size_pt", state.start_font_size_pt, state.preview_font_size_pt),
            ("tracking", state.start_tracking, state.preview_tracking),
        ):
            if before != after:
                result = replace_text_property(result, key, float(after))
        return result

    def cancel(self) -> None:
        owner = self._owner
        state = self.state
        if state is None:
            return
        object_id = str(getattr(state.hit.obj, "id", ""))
        inline_target_active = owner._inline_edit_matches_object(object_id)
        self.clear_preview()
        self.state = None
        if inline_target_active and owner._inline_state is not None:
            owner._inline_state.layer_id = state.hit.layer_id
            owner._inline_state.rect = state.start_rect
            owner._inline_state.rotation_deg = state.start_angle_deg
            owner._inline_state.object_ref = state.hit.obj
            owner._update_inline_selection_overlay(preview=True)
            owner._update_inline_text_preview()
            if owner._inline_editor is not None:
                owner._inline_editor.setFocus()
                owner._request_ime_update()
        else:
            owner._set_inline_selection_for_object(
                object_id,
                state.hit.layer_id,
                state.start_rect,
                rotation_deg=state.start_angle_deg,
                preview=False,
            )
        owner._emit_text_editing()

    def discard(self) -> None:
        """Drop a page-owned gesture without restoring its old selection."""
        if self.state is None:
            return
        self.state = None
        self.clear_preview()

    def clear_preview(self) -> None:
        owner = self._owner
        if not owner.host_api:
            return
        canvas = getattr(owner.host_api, "canvas", None)
        if canvas is None:
            owner._set_inline_shape_preview_active(False)
            return
        try:
            if hasattr(canvas, "_vector_preview"):
                canvas._vector_preview = None  # type: ignore[attr-defined]
            selection = getattr(canvas, "_vector_selection", None)
            if isinstance(selection, Mapping) and selection.get("owner") == "text_tool_transform":
                canvas._vector_selection = None  # type: ignore[attr-defined]
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            # Keep the target id until its texture version has been bumped,
            # including when that text layer lives inside a group.
            if hasattr(canvas, "_vector_preview_layer_id"):
                canvas._vector_preview_layer_id = None  # type: ignore[attr-defined]
        except Exception:
            self._logger.debug("TextTool clear preview failed", exc_info=True)
