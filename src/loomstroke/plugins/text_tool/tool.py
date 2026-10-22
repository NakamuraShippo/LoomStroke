from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QGuiApplication,
    QPalette,
    QTextOption,
    QTransform,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from loomstroke.core.japanese_text.grapheme_clusters import grapheme_spans
from loomstroke.core.japanese_text.typography import typography_from_payload
from loomstroke.core.path_text.metadata import path_settings
from loomstroke.core.text_edit_geometry import python_index_from_utf16
from loomstroke.host_api import HostAPI, StrokePoint
from loomstroke.plugins.sdk.base import PluginBase, PluginMetadata, PluginType
from loomstroke.plugins.sdk.tool import (
    ContextToolbarSpec,
    ToolActionGroup,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
)
from loomstroke.plugins.sdk.transform import hit_test_handle_aabb  # noqa: E402
from loomstroke.plugins.vector_tools.common import (
    VectorCreateCommand,
    VectorObject,
    VectorReplaceCommand,
)

from .annotations_controller import TextAnnotationsController
from .box_interaction import box_control_at, clear_hover_cursor, send_selection_pointer
from .character_style_controller import apply_selection_color, apply_selection_style
from .common import (  # noqa: E402
    TEXT_TOOL_ID,
    TEXT_TOOL_IDS,
    TEXT_TOOL_VERTICAL_ID,
    VERTICAL_FLOW_LEFT,
    TextToolSettings,
    ensure_text_vector_object,
    extract_text_content,
    extract_text_payload,
    extract_word_wrap,
    is_text_vector_object,
    load_custom_fonts_from_settings,
    load_text_tool_settings,
    merge_line_height_run,
    merge_tracking_run,
    normalize_vertical_flow,
    save_text_tool_setting,
    vector_bbox_from_paths,
)
from .edit_lifecycle import TextEditLifecycle
from .editing_history import TextEditMetadataHistory
from .editing_metadata import clone_text_for_edit, replace_edited_text
from .inline_edit_controller import InlineEditState as _InlineEditState  # noqa: E402
from .inline_edit_controller import (
    InlineTextEditController,
)
from .inline_edit_controller import InlineTextEditor as _InlineTextEditor
from .layer_naming import sync_text_layer_name
from .path_text_controller import PathTextController
from .path_text_hit import path_body_contains
from .properties_controller import (
    TextPropertiesController,
    replace_text_property,
    text_settings_dict,
)
from .settings_ui import TextToolSettingsUI  # noqa: E402
from .transform_controller import (
    TextTransformController,
)
from .transform_controller import TextTransformState as _TransformState  # noqa: E402
from .typography_controller import apply_typography, session_typography

logger = logging.getLogger(__name__)


class _VectorDeleteCommand:
    """Undoable delete: apply()=remove, revert()=re-insert."""

    def __init__(self, vector_object: VectorObject, *, layer_id: str) -> None:
        self.vector_object = vector_object
        self.layer_id = layer_id
        self._insert_index: int | None = None

    def apply(self, canvas: Any) -> bool:
        layers = getattr(canvas, "_layers", None) or []
        for layer in layers:
            if getattr(layer, "id", None) != self.layer_id:
                continue
            vec_objects = getattr(layer, "vector_objects", None) or []
            for i, v in enumerate(vec_objects):
                if getattr(v, "id", None) == self.vector_object.id:
                    self._insert_index = i
                    remove_fn = getattr(canvas, "remove_vector_object", None)
                    if callable(remove_fn):
                        return bool(
                            remove_fn(
                                self.layer_id,
                                i,
                                recompose=True,
                                vec_id=self.vector_object.id,
                            )
                        )
                    return False
        return False

    def revert(self, canvas: Any) -> bool:
        add_fn = getattr(canvas, "add_vector_object", None)
        if callable(add_fn):
            result = add_fn(
                self.vector_object,
                layer_id=self.layer_id,
                recompose=True,
            )
            return result is not None
        return False


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("TextTool", text, use_fallback=True)


_TEXT_TOOL_ICON_SVG = (
    "<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'>"
    "<rect x='3' y='2' width='18' height='20' rx='3' fill='#14532d'/>"
    "<path d='M7 7h10v2h-4v8h-2V9H7z' fill='white'/>"
    "</svg>"
)

_TEXT_TOOL_VERTICAL_ICON_SVG = (
    "<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'>"
    "<rect x='3' y='2' width='18' height='20' rx='3' fill='#14532d'/>"
    "<path d='M9 6h2v12H9zm4 0h2v12h-2z' fill='white'/>"
    "</svg>"
)


@dataclass(slots=True)
class _TextHit:
    layer_id: str | None
    obj: Any
    rect: tuple[float, float, float, float]
    payload: dict[str, Any]


@dataclass(slots=True)
class _VectorSelectionHit:
    layer_id: str
    obj: Any
    payload: dict[str, Any] | None
    rect: tuple[float, float, float, float]


def _force_transparent_inline_editor(editor: QPlainTextEdit) -> None:
    """Keep QPlainTextEdit/viewport from painting the dark theme base color."""
    transparent = QColor(0, 0, 0, 0)
    for widget in (editor, editor.viewport()):
        if widget is None:
            continue
        widget.setAutoFillBackground(False)
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        widget.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Base, transparent)
        palette.setColor(QPalette.ColorRole.Window, transparent)
        widget.setPalette(palette)


class TextTool:
    def __init__(
        self,
        *,
        tool_id: str = TEXT_TOOL_ID,
        display_name: str = "Text",
        order: int = 34,
        shortcut: str | None = "T",
        shortcut_description: str | None = "Activate Text Tool",
        forced_vertical: bool | None = None,
        icon_svg: str | None = None,
    ) -> None:
        self.host_api: HostAPI | None = None
        self._tool_id = str(tool_id)
        self._display_name = str(display_name)
        self._order = int(order)
        self._shortcut = shortcut
        self._shortcut_description = shortcut_description
        self._forced_vertical = forced_vertical
        self._icon_svg = (
            icon_svg
            if isinstance(icon_svg, str) and icon_svg.strip()
            else (_TEXT_TOOL_VERTICAL_ICON_SVG if bool(forced_vertical) else _TEXT_TOOL_ICON_SVG)
        )
        self._pending_commands: list[Any] = []
        self._press_pos: tuple[float, float] | None = None
        self._last_pos: tuple[float, float] | None = None
        self._press_hit: _TextHit | None = None
        self._hover_cursor_set: bool = False
        self._inline_controller = InlineTextEditController(
            editor_factory=_InlineTextEditor,
            state_factory=_InlineEditState,
            force_transparent_editor=_force_transparent_inline_editor,
            logger=logger,
        )
        self._transform_controller = TextTransformController(self, logger=logger)
        self._auto_return_tool_id: str | None = None
        self._auto_return_origin_id: str | None = None
        self._settings_ui = TextToolSettingsUI(
            begin_transaction=self._begin_edit_transaction,
            end_transaction=self._end_edit_transaction,
        )
        self._properties = TextPropertiesController(self)
        self._edit_lifecycle = TextEditLifecycle(self)
        self._annotations = TextAnnotationsController(self)
        self._path_text = PathTextController(self)

    @property
    def _inline_editor(self) -> _InlineTextEditor | None:
        return self._inline_controller.editor

    @_inline_editor.setter
    def _inline_editor(self, value: _InlineTextEditor | None) -> None:
        self._inline_controller.editor = value

    @property
    def _inline_state(self) -> _InlineEditState | None:
        return self._inline_controller.state

    @_inline_state.setter
    def _inline_state(self, value: _InlineEditState | None) -> None:
        self._inline_controller.state = value

    @property
    def _inline_canvas_widget(self) -> QWidget | None:
        return self._inline_controller.canvas_widget

    @_inline_canvas_widget.setter
    def _inline_canvas_widget(self, value: QWidget | None) -> None:
        self._inline_controller.canvas_widget = value

    @property
    def _inline_finishing(self) -> bool:
        return bool(self._inline_controller.finishing)

    @_inline_finishing.setter
    def _inline_finishing(self, value: bool) -> None:
        self._inline_controller.finishing = bool(value)

    @property
    def _inline_shape_preview_active(self) -> bool:
        return bool(self._inline_controller.shape_preview_active)

    @_inline_shape_preview_active.setter
    def _inline_shape_preview_active(self, value: bool) -> None:
        self._inline_controller.shape_preview_active = bool(value)

    @property
    def _inline_hover_handles(self) -> bool:
        return bool(self._inline_controller.hover_handles)

    @_inline_hover_handles.setter
    def _inline_hover_handles(self, value: bool) -> None:
        self._inline_controller.hover_handles = bool(value)

    @property
    def _transform_state(self) -> _TransformState | None:
        return self._transform_controller.state

    @_transform_state.setter
    def _transform_state(self, value: _TransformState | None) -> None:
        self._transform_controller.state = value

    @property
    def _prop_debounce_timer(self) -> Any | None:
        return self._settings_ui.prop_debounce_timer

    @_prop_debounce_timer.setter
    def _prop_debounce_timer(self, value: Any | None) -> None:
        self._settings_ui.prop_debounce_timer = value

    @property
    def _prop_txn_active(self) -> bool:
        return bool(self._settings_ui.prop_txn_active)

    @_prop_txn_active.setter
    def _prop_txn_active(self, value: bool) -> None:
        self._settings_ui.prop_txn_active = bool(value)

    def _ts(self, text: str) -> str:
        return _ts(text)

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id=self._tool_id,
            display_name=self._ts(self._display_name),
            category="text",
            cursor="ibeam",
            icon_svg=self._icon_svg,
            shortcut=self._shortcut,
            shortcut_category=self._ts("Tools"),
            shortcut_description=(
                self._ts(self._shortcut_description) if self._shortcut_description else None
            ),
            order=self._order,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        settings = self._settings()
        tags = ["text", "typography"]
        if self._forced_vertical is True:
            tags.append("vertical")
        properties = self._settings_ui.build_property_specs(
            settings,
            forced_vertical=self._forced_vertical is True,
            translate=_ts,
        )
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "text",
            tags=tags,
            group_id="text.tools",
            group_label=_ts("Text"),
            group_order=40,
            icon_svg=descriptor.icon_svg,
            properties=properties,
            action_groups=[
                ToolActionGroup(
                    id="text_convert",
                    label=_ts("Convert"),
                    actions=[
                        ToolActionSpec(
                            key="text.convert_selection_to_area",
                            label=_ts("Selection To Area"),
                            kind="button",
                        ),
                        ToolActionSpec(
                            key="text.along_path",
                            label=self._path_text_label(),
                            kind="button",
                        ),
                    ],
                )
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        is_vertical = bool(self._forced_vertical)
        return ContextToolbarSpec(
            show_trigger="text_editing",
            anchor_mode="custom",
            mode="settings",
            build_widget_fn=lambda parent, on_changed: _build_text_ctx(
                parent, on_changed, is_vertical=is_vertical
            ),
        )

    def get_context_anchor_rect(self) -> QRect | None:
        """Return the bounding box of the active text object in scene coords."""
        # Prefer inline editor rect
        state = self._inline_state
        if state is not None:
            x, y, w, h = state.rect
            return QRect(int(x), int(y), int(w), int(h))
        # Fall back to the currently selected/focused text object
        hit = self._selected_text_hit()
        if hit is not None:
            x, y, w, h = hit.rect
            return QRect(int(x), int(y), int(w), int(h))
        return None

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        load_custom_fonts_from_settings(host.settings)

    def enable(self) -> None:
        self._reset_state()
        self._sync_settings_on_enable()
        self._properties.connect()
        self._properties.sync()
        self._edit_lifecycle.connect()

    def disable(self) -> None:
        self._path_text.close()
        if hasattr(self, "_link_picker"):
            self._link_picker.cancel()
        self._edit_lifecycle.disconnect()
        self._properties.disconnect()
        self._flush_prop_transaction()
        self._cancel_transform()
        self._finish_inline_edit(commit=True)
        self._clear_inline_selection_if_owned()
        self._clear_auto_return_context(strip_canvas=False)
        self._reset_state()
        self._pending_commands.clear()
        clear_hover_cursor(self)

    def deactivate(self) -> None:
        self.disable()
        self.host_api = None

    def _emit_text_editing(self) -> None:
        """Emit tool.text_editing to show the context toolbar."""
        if self.host_api and hasattr(self.host_api, "events"):
            self.host_api.events.emit("tool.text_editing", {"tool_id": self._tool_id})

    def on_pointer_down(  # noqa: PLR0912
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        if self._path_text.pointer("press", pt):
            return
        if hasattr(self, "_link_picker") and self._link_picker.pointer(pt, click=True):
            self._reset_state()
            return
        self._flush_prop_transaction()
        inline_editor_active = self._inline_editor is not None and self._inline_state is not None
        had_active_context = inline_editor_active or self._selected_text_hit() is not None
        self._press_had_active_context = had_active_context
        inline_object_id = (
            str(self._inline_state.object_id)
            if (
                inline_editor_active
                and self._inline_state is not None
                and self._inline_state.object_id
            )
            else None
        )
        if self._transform_state is not None:
            self._cancel_transform()
        self._hydrate_auto_return_context_from_canvas()
        x = float(pt.pos[0])
        y = float(pt.pos[1])
        selected_hit = self._selected_text_hit()
        if selected_hit is not None:
            sel_rot = self._payload_rotation(selected_hit.payload)
            transform_handle = (
                None
                if path_settings(selected_hit.payload) is not None
                and path_body_contains(selected_hit.payload, x, y)
                else self._hit_test_rotated_handle(
                    x, y, selected_hit.rect, sel_rot, include_move_area=False
                )
            )
            if transform_handle is not None:
                selected_id = str(getattr(selected_hit.obj, "id", ""))
                if inline_editor_active and inline_object_id and selected_id != inline_object_id:
                    self._finish_inline_edit(commit=True)
                handle_name, mode = transform_handle
                self._begin_transform(selected_hit, handle_name, mode, (x, y))
                if self.host_api:
                    self.host_api.input.pointer_capture(self.descriptor().id)
                return
        press_hit = self._find_text_object_at(x, y)
        # Retry with padding so clicks near the bounding box still select
        if press_hit is None:
            press_hit = self._find_text_object_at(x, y, pad=8.0)
        # Include new text held only in the preview, using its visible glyphs
        # for path text and the existing frame behavior for ordinary text.
        if press_hit is None and selected_hit is not None:
            sel_rot = self._payload_rotation(selected_hit.payload)
            selected_contains = (
                path_body_contains(selected_hit.payload, x, y, pad=8.0)
                if path_settings(selected_hit.payload) is not None
                else self._point_in_rotated_rect(x, y, selected_hit.rect, sel_rot, pad=8.0)
            )
            if selected_contains:
                press_hit = selected_hit
        if inline_editor_active:
            press_id = str(getattr(press_hit.obj, "id", "")) if press_hit is not None else None
            inline_is_new = self._inline_state is not None and self._inline_state.object_id is None
            same_object = (inline_object_id and press_id == inline_object_id) or (
                inline_is_new and press_id == "text-inline-preview"
            )
            if same_object:
                if send_selection_pointer(self, "press", (x, y)):
                    self._reset_state()
                    if self.host_api:
                        self.host_api.input.pointer_capture(self.descriptor().id)
                    return
                self._press_pos = (x, y)
                self._last_pos = (x, y)
                self._press_hit = press_hit
                self._emit_text_editing()
                if self.host_api:
                    self.host_api.input.pointer_capture(self.descriptor().id)
                return
            self._finish_inline_edit(commit=True)
            press_hit = self._find_text_object_at(x, y)
            if press_hit is None:
                press_hit = self._find_text_object_at(x, y, pad=8.0)
        if self._maybe_auto_return_to_move_tool(pt, press_hit=press_hit):
            return
        if press_hit is not None:
            self._edit_existing_text(press_hit)
            if send_selection_pointer(self, "press", (x, y)):
                self._reset_state()
                if self.host_api:
                    self.host_api.input.pointer_capture(self.descriptor().id)
                return
        self._press_pos = (x, y)
        self._last_pos = (x, y)
        self._press_hit = press_hit
        if self._press_hit is not None:
            self._focus_text_hit(self._press_hit)
            self._emit_text_editing()
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        last: StrokePoint | None = None
        for point in pts:
            last = point
        if last is None:
            return
        if self._path_text.pointer("move", last):
            return
        if hasattr(self, "_link_picker") and self._link_picker.pointer(last):
            return
        current = (float(last.pos[0]), float(last.pos[1]))
        if send_selection_pointer(self, "move", current):
            return
        if self._transform_state is not None:
            shift = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._update_transform_preview(current, uniform=shift)
            return
        if (
            self._press_hit is not None
            and self._press_pos is not None
            and self._drag_distance(self._press_pos, current) > 4.0
        ):
            self._begin_transform(
                self._press_hit,
                "move-body",
                "move",
                self._press_pos,
            )
            self._update_transform_preview(current)
            self._last_pos = current
            return
        self._last_pos = current

    def on_pointer_up(self, pt: StrokePoint) -> None:
        if self._path_text.pointer("release", pt):
            return
        if self.host_api:
            self.host_api.input.release_capture(self.descriptor().id)
        if send_selection_pointer(self, "release", (float(pt.pos[0]), float(pt.pos[1]))):
            self._reset_state()
            return
        if self._transform_state is not None:
            self._update_transform_preview((float(pt.pos[0]), float(pt.pos[1])))
            self._commit_transform()
            self._reset_state()
            return

        if self._press_pos is None:
            return

        self._last_pos = (float(pt.pos[0]), float(pt.pos[1]))
        moved = self._drag_distance(self._press_pos, self._last_pos)

        if self._press_hit is not None and moved <= 4.0:
            if self._inline_editor is not None and self._inline_state is not None:
                inline_id = self._inline_state.object_id
                hit_id = str(getattr(self._press_hit.obj, "id", ""))
                if inline_id and str(inline_id) == hit_id:
                    self._focus_text_hit(self._press_hit)
                    self._emit_text_editing()
                    self._reset_state()
                    return
            self._edit_existing_text(self._press_hit)
            self._reset_state()
            return
        if self._press_hit is not None:
            self._focus_text_hit(self._press_hit)
            self._emit_text_editing()
            self._reset_state()
            return

        if getattr(self, "_press_had_active_context", False):
            # Was editing/selecting — deselect, then create new (Illustrator-style).
            self._clear_inline_selection_if_owned()
        if self.host_api:
            canvas = getattr(self.host_api, "canvas", None)
            if canvas is not None and self._start_area_text_from_raster_selection(
                canvas,
                click_pos=self._last_pos,
                show_no_selection=False,
            ):
                self._reset_state()
                return
        # Always create a new text at the click position.
        self._create_new_text()
        self._reset_state()

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        if self._path_text.pointer("hover", pt):
            return
        if hasattr(self, "_link_picker") and self._link_picker.pointer(pt):
            return
        from ..sdk.cursors import apply_hover_cursor, cursor_for_handle  # noqa: PLC0415

        x = float(pt.pos[0])
        y = float(pt.pos[1])
        cursor: QCursor | None = None
        selected_hit = self._selected_text_hit()
        if selected_hit is not None:
            sel_rot = self._payload_rotation(selected_hit.payload)
            handle_result = self._hit_test_rotated_handle(
                x, y, selected_hit.rect, sel_rot, include_move_area=False
            )
            if handle_result is not None:
                cursor = cursor_for_handle(*handle_result)
            elif self._point_in_rotated_rect(x, y, selected_hit.rect, sel_rot):
                cursor = QCursor(Qt.CursorShape.IBeamCursor)
        # During inline editing, show/hide transform handles based on hover
        # near the text boundary (same UX for both vertical and horizontal).
        if self._inline_editor is not None and self._inline_state is not None:
            state = self._inline_state
            rot = state.rotation_deg
            near_boundary = cursor is not None or (
                selected_hit is not None
                and self._point_in_rotated_rect(x, y, selected_hit.rect, rot, pad=24.0)
                and not self._point_in_rotated_rect(x, y, selected_hit.rect, rot, pad=-16.0)
            )
            if near_boundary != self._inline_hover_handles:
                self._inline_hover_handles = near_boundary
                self._update_inline_selection_overlay(preview=True)
        widget = self._canvas_widget()
        if widget is not None:
            if cursor is None:
                widget.unsetCursor()
            else:
                widget.setCursor(cursor)
        else:
            self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:
        if not pressed:
            return False
        key_lower = key.lower()
        if key_lower in {"escape", "esc"}:
            if self._path_text.cancel_drag():
                return True
            if hasattr(self, "_link_picker") and self._link_picker.source is not None:
                self._link_picker.cancel()
                return True
            if self._transform_state is not None:
                self._cancel_transform()
            elif self._inline_editor is not None:
                # Illustrator-style: Escape commits (preserves content).
                self._finish_inline_edit(commit=True)
            else:
                # No inline editor — deselect current text object.
                self._clear_inline_selection_if_owned()
            self._reset_state()
            return True
        if key_lower in {"delete", "backspace"}:
            return self._handle_delete_key()
        return False

    def _handle_delete_key(self) -> bool:
        """Delete selected text object (non-inline) or pass through to editor."""
        if self._inline_editor is not None:
            # Inline editing active — let QPlainTextEdit handle delete/backspace.
            return False
        hit = self._selected_text_hit()
        if hit is None or hit.layer_id is None:
            return False
        obj = hit.obj
        obj_id = str(getattr(obj, "id", ""))
        if not obj_id:
            return False
        self._clear_inline_selection_if_owned()
        # Submit a delete command (apply=remove, revert=re-insert for undo).
        cmd = _VectorDeleteCommand(obj, layer_id=hit.layer_id)
        self._submit_command(cmd)
        self._show_status(_ts("Text deleted."))
        return True

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        del delta, pt
        return False

    def on_action(self, action_key: str, payload: dict[str, Any] | None = None) -> list[Any]:
        if action_key == "text.along_path":
            self._path_text.begin()
            return []
        if action_key == "text.context_menu":
            self._show_context_menu(payload)
            return []
        if action_key != "text.convert_selection_to_area":
            return []
        # Use the raster selection mask as a shaped text area.
        if not self.host_api:
            return []
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return []
        if self._convert_selected_point_text_to_area():
            return []
        vector_area = self._shape_area_from_vector_selection(canvas)
        if vector_area is not None:
            rect, shape_frame = vector_area
            started = self._start_inline_edit(
                initial_text="",
                rect=rect,
                word_wrap=True,
                layer_id=None,
                target_hit=None,
            )
            if started:
                self._apply_selection_shape_frame(shape_frame)
            else:
                self._show_status(_ts("Inline text editor is not available."))
            return []
        self._start_area_text_from_raster_selection(canvas, show_no_selection=True)
        return []

    def get_settings(self) -> dict[str, Any]:
        return self._properties.current_values()

    def set_settings(self, settings: dict[str, Any]) -> None:
        for key, value in dict(settings).items():
            self.set_setting(key, value)

    def set_setting(self, key: str, value: Any) -> None:  # noqa: PLR0911, PLR0912
        if key == "path_text_action":
            self._path_text.begin()
            return
        if not self.host_api:
            return
        hit = self._selected_text_hit()
        layer_id = self._inline_state.layer_id if self._inline_state is not None else None
        if not self._edit_lifecycle.can_edit_layer(hit.layer_id if hit is not None else layer_id):
            self._properties.sync(force=True)
            return
        if key == "typography":
            apply_typography(self, value)
            return
        if key == "text_flow":
            from .frame_flow_controller import prepare_inline_frame  # noqa: PLC0415

            prepare_inline_frame(self)
            if self._inline_editor is None:
                self._ensure_prop_transaction()
            self._apply_settings_to_selected_text(key, value)
            self._refresh_inline_editor_after_setting_change()
            self._properties.sync(force=True)
            return
        if key == "link_frames":
            from .frame_flow_controller import apply_link_request  # noqa: PLC0415

            apply_link_request(self, value)
            return
        if key == "paragraph_columns":
            apply_selection_style(self, key, value)
            return
        if key in {
            "named_style",
            "horizontal_scale",
            "vertical_scale",
            "baseline_shift_em",
            "before_space_em",
            "after_space_em",
        }:
            apply_selection_style(self, key, value)
            return
        if key in {"ruby", "tcy", "emphasis", "warichu", "jidori"}:
            self._annotations.apply(key, value)
            return
        if key == "word_wrap":
            value = bool(value)
        elif key == "font_size_pt":
            try:
                value = max(1.0, min(512.0, float(value)))
            except (TypeError, ValueError):
                return
        elif key == "vertical":
            value = bool(value)
            if self._forced_vertical is not None:
                value = bool(self._forced_vertical)
        elif key == "vertical_flow":
            value = normalize_vertical_flow(value, default=VERTICAL_FLOW_LEFT)
            if apply_selection_style(self, "paragraph_flow", value):
                return
        elif key == "line_height":
            try:
                value = max(0.5, min(4.0, float(value)))
            except (TypeError, ValueError):
                return
            # Per-selection line_height: if inline editor has selection, apply to range only
            if self._inline_editor is not None and self._inline_state is not None:
                sel = self._inline_selection_range(self._inline_editor)
                if sel is not None:
                    self._apply_line_height_to_selection(sel[0], sel[1], value)
                    return
        elif key == "tracking":
            try:
                value = max(-64.0, min(128.0, float(value)))
            except (TypeError, ValueError):
                return
            # Per-selection tracking: if inline editor has selection, apply to range only
            if self._inline_editor is not None and self._inline_state is not None:
                sel = self._inline_selection_range(self._inline_editor)
                if sel is not None:
                    self._apply_tracking_to_selection(sel[0], sel[1], value)
                    return
        elif key == "align":
            value = str(value).strip().lower()
            if value not in {"left", "center", "right", "justify"}:
                return
            if apply_selection_style(self, "paragraph_align", value):
                return
        elif key in {"padding_top", "padding_right", "padding_bottom", "padding_left"}:
            try:
                value = max(0.0, min(256.0, float(value)))
            except (TypeError, ValueError):
                return
        elif key == "color_rgba":
            if not isinstance(value, (list, tuple)) or len(value) < 4:
                return
            try:
                value = [
                    max(0, min(255, round(float(value[0])))),
                    max(0, min(255, round(float(value[1])))),
                    max(0, min(255, round(float(value[2])))),
                    max(0, min(255, round(float(value[3])))),
                ]
            except (TypeError, ValueError, OverflowError):
                return
            if apply_selection_color(self, value):
                return
        elif key == "rotate_half_width":
            value = bool(value)
        elif key in {"font_family", "font_style"}:
            value = str(value)
        else:
            return
        if key in {"font_family", "font_style", "font_size_pt"} and apply_selection_style(
            self, key, value
        ):
            return
        save_text_tool_setting(self.host_api.settings, key, value)
        if key in {
            "word_wrap",
            "font_family",
            "font_style",
            "font_size_pt",
            "color_rgba",
            "vertical",
            "vertical_flow",
            "align",
            "line_height",
            "tracking",
            "padding_top",
            "padding_right",
            "padding_bottom",
            "padding_left",
            "rotate_half_width",
        }:
            # When not in an inline editing session, wrap property changes
            # in a debounced transaction so rapid wheel adjustments become
            # a single undo entry (500 ms idle → commit).
            if self._inline_editor is None:
                self._ensure_prop_transaction()
            self._apply_settings_to_selected_text(key, value)
        self._refresh_inline_editor_after_setting_change()
        self._properties.sync()

    def build_commands(self) -> list[Any]:
        commands = list(self._pending_commands)
        self._pending_commands.clear()
        return commands

    def create_options_widget(self) -> QWidget | None:
        return None

    def _reset_state(self) -> None:
        self._press_pos = None
        self._last_pos = None
        self._press_hit = None

    def _hydrate_auto_return_context_from_canvas(self) -> None:
        if not self.host_api:
            self._auto_return_tool_id = None
            self._auto_return_origin_id = None
            return
        canvas = getattr(self.host_api, "canvas", None)
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if not isinstance(selection, Mapping):
            self._auto_return_tool_id = None
            self._auto_return_origin_id = None
            return
        auto_tool = selection.get("auto_return_tool")
        auto_origin = selection.get("auto_return_origin_id")
        self._auto_return_tool_id = auto_tool if isinstance(auto_tool, str) and auto_tool else None
        self._auto_return_origin_id = (
            auto_origin if isinstance(auto_origin, str) and auto_origin else None
        )

    def _has_auto_return_context(self) -> bool:
        return bool(self._auto_return_tool_id and self._auto_return_origin_id)

    def _apply_auto_return_metadata(self, payload: dict[str, Any]) -> None:
        if not self._has_auto_return_context():
            return
        payload["auto_return_tool"] = self._auto_return_tool_id
        payload["auto_return_origin_id"] = self._auto_return_origin_id

    def _clear_auto_return_context(self, *, strip_canvas: bool) -> None:
        if strip_canvas and self.host_api:
            canvas = getattr(self.host_api, "canvas", None)
            selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
            if isinstance(selection, Mapping) and (
                "auto_return_tool" in selection or "auto_return_origin_id" in selection
            ):
                stripped = dict(selection)
                stripped.pop("auto_return_tool", None)
                stripped.pop("auto_return_origin_id", None)
                try:  # noqa: SIM105
                    canvas._vector_selection = stripped  # type: ignore[attr-defined]  # duck-typed canvas attr
                except Exception:
                    logger.debug("vector selection strip failed", exc_info=True)
        self._auto_return_tool_id = None
        self._auto_return_origin_id = None

    def _activate_tool(self, tool_id: str) -> bool:
        host = self.host_api
        if not host:
            return False
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        activate = getattr(tool_manager, "activate_tool", None) if tool_manager else None
        if not callable(activate):
            return False
        try:
            activate(tool_id)
        except Exception:
            return False
        return True

    def _simulate_click_on_active_tool(self, pt: StrokePoint) -> None:
        host = self.host_api
        if not host:
            return
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        tool = getattr(tool_manager, "_active_tool", None) if tool_manager is not None else None
        if tool is None:
            return
        down = getattr(tool, "on_pointer_down", None)
        up = getattr(tool, "on_pointer_up", None)
        if not callable(down) or not callable(up):
            return
        try:
            down(pt, alt=False, shift=False, ctrl=False, meta=False)
        except TypeError:
            try:
                down(pt)
            except Exception:
                return
        except Exception:
            return
        try:
            up(pt)
        except Exception:
            return

    def _maybe_auto_return_to_move_tool(
        self,
        pt: StrokePoint,
        *,
        press_hit: _TextHit | None,
    ) -> bool:
        if not self._has_auto_return_context():
            return False
        origin_id = self._auto_return_origin_id
        if (
            origin_id
            and press_hit is not None
            and str(getattr(press_hit.obj, "id", "")) == origin_id
        ):
            return False
        tool_id = self._auto_return_tool_id
        if not tool_id:
            return False
        if not self._activate_tool(tool_id):
            return False
        self._clear_auto_return_context(strip_canvas=True)
        self._simulate_click_on_active_tool(pt)
        return True

    def _settings(self) -> TextToolSettings:
        if not self.host_api:
            return TextToolSettings()
        settings = load_text_tool_settings(self.host_api.settings)
        settings.typography = session_typography(self)
        if self._forced_vertical is not None:
            settings.vertical = bool(self._forced_vertical)
        return settings

    def _sync_settings_on_enable(self) -> None:
        if not self.host_api:
            return
        settings = self._settings()
        if self._forced_vertical is not None:
            save_text_tool_setting(
                self.host_api.settings,
                "vertical",
                bool(self._forced_vertical),
            )
        save_text_tool_setting(
            self.host_api.settings,
            "vertical_flow",
            normalize_vertical_flow(settings.vertical_flow, default=VERTICAL_FLOW_LEFT),
        )

        tool_manager = getattr(self.host_api, "_tool_manager", None) or getattr(
            self.host_api,
            "tool_manager",
            None,
        )
        sync = getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        if not callable(sync):
            return
        payload = text_settings_dict(settings)
        for tool_id in self._iter_registered_text_tool_ids(tool_manager):
            for key, value in payload.items():
                with suppress(Exception):
                    sync(tool_id, key, value)

    def _iter_registered_text_tool_ids(self, tool_manager: Any) -> list[str]:
        has_tool = getattr(tool_manager, "has_tool", None) if tool_manager else None
        if callable(has_tool):
            registered = [
                tool_id for tool_id in TEXT_TOOL_IDS if bool(self._safe_has_tool(has_tool, tool_id))
            ]
            if registered:
                return registered
        return [self._tool_id]

    @staticmethod
    def _safe_has_tool(has_tool: Any, tool_id: str) -> bool:
        try:
            return bool(has_tool(tool_id))
        except Exception:
            return False

    def _inline_effective_settings(
        self,
        *,
        state: _InlineEditState | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> TextToolSettings:
        current = state or self._inline_state
        current_payload = payload
        if current_payload is None and current is not None and current.object_ref is not None:
            current_payload = extract_text_payload(current.object_ref)
        if current_payload is not None:
            settings = self._settings_from_payload(current_payload)
        else:
            settings = self._settings()
        return settings

    @staticmethod
    def _is_leftward_vertical_flow(settings: TextToolSettings) -> bool:
        return bool(
            settings.vertical
            and normalize_vertical_flow(
                settings.vertical_flow,
                default=VERTICAL_FLOW_LEFT,
            )
            == VERTICAL_FLOW_LEFT
        )

    def _autosize_rect_for_settings(
        self,
        rect: tuple[float, float, float, float],
        *,
        content: str,
        settings: TextToolSettings,
    ) -> tuple[float, float, float, float]:
        measured_w, measured_h = self._measure_text(content if content else " ", settings)
        x, y, prev_w, _ = rect
        if self._is_leftward_vertical_flow(settings):
            x = (x + prev_w) - measured_w
        return (x, y, measured_w, measured_h)

    def _sync_auto_size_rect_for_inline(
        self,
        state: _InlineEditState,
        *,
        content: str,
        settings: TextToolSettings,
    ) -> None:
        if (
            state.object_ref is not None
            and path_settings(extract_text_payload(state.object_ref)) is not None
        ):
            return
        if state.word_wrap:
            # Both new and existing Area Text wrap inside the user's frame.
            return
        measure_settings = settings
        if not state.word_wrap:
            # Point Text: measure with neutral tracking/line_height
            measure_settings = _dc_replace(settings, tracking=0.0, line_height=1.0)
        from .typography_measurement import measure_japanese_text  # noqa: PLC0415

        payload = extract_text_payload(state.object_ref) if state.object_ref is not None else None
        if payload is not None and content != extract_text_content(payload):
            # IME composition changes preview ranges without touching the
            # committed edit state. Measure that same detached preview data.
            preview_cursor = None
            if self._inline_editor is not None:
                preview_content, cursor = self._inline_preview_content_and_cursor(
                    self._inline_editor
                )
                if preview_content == content:
                    preview_cursor = self._inline_preview_edit_end(self._inline_editor, cursor)
            payload = extract_text_payload(
                replace_edited_text(state.object_ref, content, cursor_position=preview_cursor)
            )
        advanced_size = measure_japanese_text(
            content if content else " ", settings, font=self._build_font(settings), payload=payload
        )
        measured_w, measured_h = advanced_size or self._measure_text(
            content if content else " ", measure_settings
        )
        x, y, old_w, _old_h = state.rect
        if settings.vertical:
            new_w = measured_w if not state.word_wrap else old_w
            if new_w != old_w and self._is_leftward_vertical_flow(settings):
                # Keep right edge fixed for left-flowing vertical text.
                x = (x + old_w) - new_w
            state.rect = (x, y, new_w, measured_h)
        else:
            state.rect = (x, y, measured_w, measured_h)

    def _inline_needs_canvas_preview(
        self,
        *,
        state: _InlineEditState | None = None,
        settings: TextToolSettings | None = None,
    ) -> bool:
        # Always use canvas preview so the editing view matches the final render.
        return True

    def _inline_edit_matches_object(self, object_id: str) -> bool:
        if self._inline_editor is None or self._inline_state is None:
            return False
        inline_object_id = self._inline_state.object_id
        if not inline_object_id:
            # New (uncommitted) text box uses "text-inline-preview" as id
            return object_id == "text-inline-preview"
        return str(inline_object_id) == str(object_id)

    def _inline_transform_preview_active(self, state: _InlineEditState | None = None) -> bool:
        transform = self._transform_state
        current = state or self._inline_state
        if transform is None or current is None:
            return False
        transform_id = str(getattr(transform.hit.obj, "id", ""))
        if not current.object_id:
            # New (uncommitted) text box
            return transform_id == "text-inline-preview"
        return str(current.object_id) == transform_id

    @staticmethod
    def _request_ime_update() -> None:
        with suppress(Exception):
            ime = QGuiApplication.inputMethod()
            if ime is not None:
                ime.update(Qt.InputMethodQuery.ImCursorRectangle)

    @staticmethod
    def _clamp_text_index(index: int, text_length: int) -> int:
        return max(0, min(int(text_length), int(index)))

    def _inline_preview_content_and_cursor(
        self,
        editor: _InlineTextEditor,
    ) -> tuple[str, int]:
        committed = editor.toPlainText()
        cursor = editor.textCursor()
        committed_cursor = (
            python_index_from_utf16(committed, cursor.position())
            if cursor is not None
            else len(committed)
        )
        preedit_text = editor.preedit_text()
        if not preedit_text:
            return (
                committed,
                self._clamp_text_index(committed_cursor, len(committed)),
            )

        start = self._clamp_text_index(editor.preedit_start(), len(committed))
        replace_length = max(0, int(editor.preedit_replace_length()))
        end = self._clamp_text_index(start + replace_length, len(committed))
        preview_content = f"{committed[:start]}{preedit_text}{committed[end:]}"

        preedit_cursor = self._clamp_text_index(
            editor.preedit_cursor_offset(),
            len(preedit_text),
        )
        preview_cursor = self._clamp_text_index(
            start + preedit_cursor,
            len(preview_content),
        )
        return (preview_content, preview_cursor)

    def _inline_preview_edit_end(self, editor: _InlineTextEditor, cursor_position: int) -> int:
        """Locate the replacement end independently of the IME's display caret."""
        preedit_text = editor.preedit_text()
        if not preedit_text:
            return cursor_position
        start = self._clamp_text_index(editor.preedit_start(), len(editor.toPlainText()))
        return start + len(preedit_text)

    def _inline_selection_range(
        self,
        editor: _InlineTextEditor,
    ) -> tuple[int, int] | None:
        cursor = editor.textCursor()
        if cursor is None:
            return None
        text = editor.toPlainText()
        text_length = len(text)
        anchor = python_index_from_utf16(text, cursor.anchor())
        position = python_index_from_utf16(text, cursor.position())
        if anchor == position:
            return None
        start = min(anchor, position)
        end = max(anchor, position)
        if start < 0 or end > text_length:
            return None
        return (start, end)

    @staticmethod
    def _strip_non_text_shape_meta(meta: Any) -> dict[str, Any] | None:
        if not isinstance(meta, Mapping):
            return None
        cleaned = dict(meta)
        cleaned.pop("shape_manager_parametric", None)
        return cleaned

    def _shape_frame_from_vector(
        self,
        obj: Any,
        rect: tuple[float, float, float, float],
    ) -> dict[str, Any] | None:
        x, y, w, h = rect
        if w <= 1e-6 or h <= 1e-6:
            return None
        raw_paths = getattr(obj, "paths", None) or []
        serialized_paths: list[dict[str, Any]] = []
        for path in raw_paths:
            anchors = getattr(path, "anchors", None) or []
            serialized_anchors: list[dict[str, Any]] = []
            for anchor in anchors:
                pos = getattr(anchor, "pos", None)
                if not (isinstance(pos, tuple) and len(pos) >= 2):
                    continue
                try:
                    ax = float(pos[0])
                    ay = float(pos[1])
                except (TypeError, ValueError):
                    continue
                payload_anchor: dict[str, Any] = {
                    "kind": str(getattr(anchor, "kind", "corner") or "corner"),
                    "pos": [
                        (ax - x) / w,
                        (ay - y) / h,
                    ],
                }
                h_in = getattr(anchor, "h_in", None)
                if isinstance(h_in, tuple) and len(h_in) >= 2:
                    with suppress(TypeError, ValueError):
                        payload_anchor["h_in"] = [
                            float(h_in[0]) / w,
                            float(h_in[1]) / h,
                        ]
                h_out = getattr(anchor, "h_out", None)
                if isinstance(h_out, tuple) and len(h_out) >= 2:
                    with suppress(TypeError, ValueError):
                        payload_anchor["h_out"] = [
                            float(h_out[0]) / w,
                            float(h_out[1]) / h,
                        ]
                serialized_anchors.append(payload_anchor)
            if serialized_anchors:
                serialized_paths.append(
                    {
                        "closed": bool(getattr(path, "closed", False)),
                        "anchors": serialized_anchors,
                    }
                )
        if not serialized_paths:
            return None
        return {"paths": serialized_paths, "handle_space": "anchor_delta"}

    def _shape_area_from_vector_selection(
        self, canvas: Any
    ) -> tuple[tuple[float, float, float, float], dict[str, Any]] | None:
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, Mapping):
            return None

        selected_ids: list[tuple[str, str | None]] = []
        items = selection.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                vec_id = item.get("id")
                if isinstance(vec_id, str):
                    layer_hint = item.get("layer_id")
                    selected_ids.append(
                        (vec_id, layer_hint if isinstance(layer_hint, str) else None)
                    )
        else:
            ids = selection.get("ids")
            if isinstance(ids, list):
                layer_hint = selection.get("layer_id")
                layer_id = layer_hint if isinstance(layer_hint, str) else None
                for vec_id in ids:
                    if isinstance(vec_id, str):
                        selected_ids.append((vec_id, layer_id))
            else:
                vec_id = selection.get("id")
                if isinstance(vec_id, str):
                    layer_hint = selection.get("layer_id")
                    selected_ids.append(
                        (vec_id, layer_hint if isinstance(layer_hint, str) else None)
                    )
        if not selected_ids:
            return None

        objects_getter = getattr(canvas, "get_vector_objects", None)
        objects = objects_getter() if callable(objects_getter) else []
        for target_id, layer_hint in selected_ids:
            for entry in objects:
                layer_id: str | None = None
                obj: Any = entry
                if isinstance(entry, tuple) and len(entry) >= 2:
                    layer_raw, obj = entry[0], entry[1]
                    layer_id = layer_raw if isinstance(layer_raw, str) else None
                if layer_hint is not None and layer_id not in (None, layer_hint):
                    continue
                if str(getattr(obj, "id", "")) != target_id:
                    continue
                if is_text_vector_object(obj):
                    continue
                paths = getattr(obj, "paths", None) or []
                if not paths or any(not getattr(path, "closed", False) for path in paths):
                    continue
                bounds = vector_bbox_from_paths(obj)
                if bounds is None:
                    continue
                min_x, min_y, max_x, max_y = bounds
                rect = (
                    float(min_x),
                    float(min_y),
                    max(1.0, float(max_x - min_x)),
                    max(1.0, float(max_y - min_y)),
                )
                shape_frame = self._shape_frame_from_vector(obj, rect)
                if shape_frame is None:
                    continue
                shape_frame = dict(shape_frame)
                shape_frame["source"] = "vector_selection"
                shape_frame["source_object_id"] = target_id
                return rect, shape_frame
        return None

    def _start_area_text_from_raster_selection(
        self,
        canvas: Any,
        *,
        click_pos: tuple[float, float] | None = None,
        show_no_selection: bool = False,
    ) -> bool:
        bounds_getter = getattr(canvas, "get_selection_bounds", None)
        bounds = bounds_getter() if callable(bounds_getter) else None
        rect = self._rect_from_selection_bounds(bounds)
        if rect is None:
            if show_no_selection:
                self._show_status(_ts("No selection. Draw a selection first."))
            return False
        if click_pos is not None and not self._point_in_axis_aligned_rect(click_pos, rect):
            return False
        qrect = QRect(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
        shape_frame = self._shape_frame_from_selection_mask(canvas, qrect)
        started = self._start_inline_edit(
            initial_text="",
            rect=rect,
            word_wrap=True,
            layer_id=None,
            target_hit=None,
        )
        if started and shape_frame is not None:
            self._apply_selection_shape_frame(shape_frame)
        if not started:
            self._show_status(_ts("Inline text editor is not available."))
        return True

    @staticmethod
    def _rect_from_selection_bounds(bounds: Any) -> tuple[float, float, float, float] | None:
        if bounds is None:
            return None
        if hasattr(bounds, "isEmpty") and bounds.isEmpty():
            return None
        try:
            if all(hasattr(bounds, attr) for attr in ("x", "y", "width", "height")):
                rect = (
                    float(bounds.x()),
                    float(bounds.y()),
                    float(bounds.width()),
                    float(bounds.height()),
                )
            elif isinstance(bounds, (tuple, list)) and len(bounds) == 4:
                rect = (float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3]))
            else:
                return None
        except (TypeError, ValueError):
            return None
        if rect[2] <= 0.0 or rect[3] <= 0.0:
            return None
        return rect

    @staticmethod
    def _point_in_axis_aligned_rect(
        point: tuple[float, float],
        rect: tuple[float, float, float, float],
    ) -> bool:
        px, py = point
        x, y, w, h = rect
        return x <= px <= x + w and y <= py <= y + h

    @staticmethod
    def _with_shape_frame_meta(obj: VectorObject, shape_frame: Mapping[str, Any]) -> VectorObject:
        meta = dict(getattr(obj, "meta", {}) or {})
        payload = meta.get("text_tool")
        payload_dict = dict(payload) if isinstance(payload, Mapping) else {}
        layout = payload_dict.get("layout")
        layout_dict = dict(layout) if isinstance(layout, Mapping) else {}
        layout_dict["wrap_mode"] = "shape"
        payload_dict["layout"] = layout_dict
        payload_dict["shape_frame"] = dict(shape_frame)
        meta["text_tool"] = payload_dict
        return VectorObject(
            id=obj.id,
            paths=list(obj.paths),
            style=obj.style,
            meta=meta,
            preview=bool(getattr(obj, "preview", False)),
        )

    @staticmethod
    def _shape_frame_from_selection_mask(canvas: Any, bounds: Any) -> dict[str, Any] | None:
        """Encode the selection mask within *bounds* as a base64 PNG shape_frame."""
        mask_getter = getattr(canvas, "get_selection_mask", None)
        mask = mask_getter() if callable(mask_getter) else None
        if mask is None or mask.isNull():
            return None
        from PySide6.QtCore import QBuffer, QIODevice, QRect  # noqa: PLC0415

        brect = QRect(int(bounds.x()), int(bounds.y()), int(bounds.width()), int(bounds.height()))
        cropped = mask.copy(brect)
        if cropped.isNull():
            return None
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        cropped.save(buf, "PNG")
        buf.close()
        import base64  # noqa: PLC0415

        mask_b64 = base64.b64encode(bytes(buf.data())).decode("ascii")
        return {"mask_png": mask_b64}

    def _apply_selection_shape_frame(self, shape_frame: dict[str, Any]) -> None:
        """Inject *shape_frame* into the current inline editing state."""
        state = self._inline_state
        if state is None:
            return
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        preview = getattr(canvas, "_vector_preview", None)
        if preview is not None:
            updated = self._with_shape_frame_meta(preview, shape_frame)
            state.object_ref = replace_edited_text(updated, self._inline_editor.toPlainText())
            if state.metadata_history is None:
                state.metadata_history = TextEditMetadataHistory(
                    state.object_ref,
                    undo_steps=self._inline_editor.document().availableUndoSteps(),
                )
            canvas._vector_preview = updated  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()

    @staticmethod
    def _with_inline_preview_meta(
        obj: VectorObject,
        *,
        cursor_pos: int,
        selection_start: int | None = None,
        selection_end: int | None = None,
    ) -> VectorObject:
        meta = dict(getattr(obj, "meta", {}) or {})
        payload = meta.get("text_tool")
        payload_dict = dict(payload) if isinstance(payload, Mapping) else {}
        inline_preview = payload_dict.get("inline_preview")
        inline_dict = dict(inline_preview) if isinstance(inline_preview, Mapping) else {}
        inline_dict["cursor_pos"] = max(0, int(cursor_pos))
        inline_dict["show_cursor"] = True
        if (
            selection_start is not None
            and selection_end is not None
            and int(selection_start) < int(selection_end)
        ):
            inline_dict["selection_start"] = max(0, int(selection_start))
            inline_dict["selection_end"] = max(0, int(selection_end))
        else:
            inline_dict.pop("selection_start", None)
            inline_dict.pop("selection_end", None)
        payload_dict["inline_preview"] = inline_dict
        meta["text_tool"] = payload_dict
        return VectorObject(
            id=obj.id,
            paths=list(obj.paths),
            style=obj.style,
            meta=meta,
            preview=bool(getattr(obj, "preview", False)),
        )

    def _selected_vector_hit(self) -> _VectorSelectionHit | None:  # noqa: PLR0911, PLR0912
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return None
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return None
        try:
            objects = list(getter())
        except Exception:
            return None
        if not objects:
            return None

        selection = getattr(canvas, "_vector_selection", None)
        candidates: list[tuple[str, str | None]] = []
        if isinstance(selection, Mapping):
            items = selection.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    item_id = item.get("id")
                    if not isinstance(item_id, str):
                        continue
                    layer_id = item.get("layer_id")
                    candidates.append(
                        (
                            item_id,
                            str(layer_id) if isinstance(layer_id, str) else None,
                        )
                    )
            else:
                selected_id = selection.get("id")
                selected_layer = selection.get("layer_id")
                if isinstance(selected_id, str):
                    candidates.append(
                        (
                            selected_id,
                            str(selected_layer) if isinstance(selected_layer, str) else None,
                        )
                    )

        if not candidates:
            text_hit = self._selected_text_hit()
            if text_hit is not None:
                return _VectorSelectionHit(
                    layer_id=text_hit.layer_id,
                    obj=text_hit.obj,
                    payload=text_hit.payload,
                    rect=text_hit.rect,
                )
            return None

        for candidate_id, candidate_layer in candidates:
            for layer_id, obj in reversed(objects):
                object_id = getattr(obj, "id", None)
                if str(object_id) != candidate_id:
                    continue
                if candidate_layer is not None and str(layer_id) != candidate_layer:
                    continue
                payload = extract_text_payload(obj)
                rect = self._object_rect(obj, payload or {})
                if rect is None:
                    bounds = vector_bbox_from_paths(obj)
                    if bounds is None:
                        continue
                    rect = (
                        float(bounds[0]),
                        float(bounds[1]),
                        max(1.0, float(bounds[2]) - float(bounds[0])),
                        max(1.0, float(bounds[3]) - float(bounds[1])),
                    )
                return _VectorSelectionHit(
                    layer_id=str(layer_id),
                    obj=obj,
                    payload=payload,
                    rect=rect,
                )
        # Candidates existed but none matched a committed object — fall back
        # to _selected_text_hit (handles uncommitted preview objects).
        text_hit = self._selected_text_hit()
        if text_hit is not None:
            return _VectorSelectionHit(
                layer_id=text_hit.layer_id,
                obj=text_hit.obj,
                payload=text_hit.payload,
                rect=text_hit.rect,
            )
        return None

    def _find_text_object_at(self, x: float, y: float, *, pad: float = 0.0) -> _TextHit | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        getter = getattr(canvas, "get_vector_objects", None) if canvas else None
        if not callable(getter):
            return None
        try:
            objects = list(getter())
        except Exception:
            return None
        for layer_id, obj in reversed(objects):
            if not is_text_vector_object(obj):
                continue
            payload = extract_text_payload(obj)
            if payload is None:
                continue
            rect = self._object_rect(obj, payload)
            if rect is None:
                continue
            rotation = self._payload_rotation(payload)
            hit = (
                path_body_contains(payload, x, y, pad=pad)
                if path_settings(payload) is not None
                else self._point_in_rotated_rect(x, y, rect, rotation, pad=pad)
            )
            if hit:
                return _TextHit(layer_id=str(layer_id), obj=obj, rect=rect, payload=payload)
        return None

    def _focus_text_hit(self, hit: _TextHit) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        set_active_layer = getattr(canvas, "set_active_layer", None)
        if callable(set_active_layer):
            with suppress(Exception):
                set_active_layer(hit.layer_id)
        object_id = str(getattr(hit.obj, "id", ""))
        if not object_id:
            return
        rect = self._payload_rect(hit.payload, fallback=hit.rect)
        bounds = self._rect_to_bounds(rect, rotation_deg=self._payload_rotation(hit.payload))
        try:
            payload = {
                "id": object_id,
                "path": 0,
                "anchor": 0,
                "layer_id": hit.layer_id,
                "preview": False,
                "transform_bounds": bounds,
                "mode": "transform",
                "owner": "text_tool_focus",
            }
            self._apply_auto_return_metadata(payload)
            canvas._vector_selection = payload  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
        except Exception:
            logger.debug("TextTool focus selection failed", exc_info=True)
        self._properties.sync()

    @staticmethod
    def _point_in_rect(
        x: float, y: float, rect: tuple[float, float, float, float], *, pad: float = 0.0
    ) -> bool:
        rx, ry, rw, rh = rect
        return (rx - pad) <= x <= (rx + rw + pad) and (ry - pad) <= y <= (ry + rh + pad)

    @staticmethod
    def _point_in_rotated_rect(
        x: float,
        y: float,
        rect: tuple[float, float, float, float],
        rotation_deg: float,
        *,
        pad: float = 0.0,
    ) -> bool:
        """Hit-test a point against a rotated rectangle.

        Transforms the point into the rectangle's local coordinate system
        (inverse rotation around the rect centre) then does an AABB check.
        """
        rx, ry, rw, rh = rect
        if abs(rotation_deg) < 1e-6:
            return (rx - pad) <= x <= (rx + rw + pad) and (ry - pad) <= y <= (ry + rh + pad)
        cx = rx + rw * 0.5
        cy = ry + rh * 0.5
        rad = math.radians(-rotation_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        dx = x - cx
        dy = y - cy
        lx = cx + dx * cos_a - dy * sin_a
        ly = cy + dx * sin_a + dy * cos_a
        return (rx - pad) <= lx <= (rx + rw + pad) and (ry - pad) <= ly <= (ry + rh + pad)

    def _hit_test_rotated_handle(
        self,
        x: float,
        y: float,
        rect: tuple[float, float, float, float],
        rotation_deg: float,
        *,
        include_move_area: bool = False,
    ) -> tuple[str, str] | None:
        """Hit-test transform handles using the AABB that the canvas displays.

        The canvas draws the transform overlay on the *rotated* AABB, so we
        must hit-test against that same AABB to keep handle positions in sync.
        """
        bounds = self._rect_to_bounds(rect, rotation_deg=rotation_deg)
        if include_move_area:
            return hit_test_handle_aabb(x, y, bounds, include_move_area=True)
        widget = self._canvas_widget()
        viewport = getattr(widget, "viewport", None)
        getter = getattr(viewport, "get_transform", None)
        transform = getter() if callable(getter) else QTransform()
        return box_control_at(x, y, bounds, transform)

    def _create_new_text(self) -> None:
        if not self.host_api or self._press_pos is None:
            return
        settings = self._settings()
        drag_end = self._last_pos or self._press_pos
        moved = self._drag_distance(self._press_pos, drag_end)
        if moved > 4.0:
            # Area Text: use drag rect
            rect = self._rect_from_drag(self._press_pos, drag_end)
        else:
            # Point Text: minimal cursor-sized rect (auto-expands on input)
            # No padding for Point Text — padding is for Area Text only.
            fs = max(8.0, settings.font_size_pt)
            if settings.vertical:
                box_w = fs
                box_h = fs * 1.5
            else:
                box_w = fs * 0.7
                box_h = fs * 1.3
            if settings.vertical:
                # Place the first column centre at the click x position.
                # Renderer draws column_x at (x + w - ascent) for left flow
                # and (x + ascent) for right flow; ascent ≈ 0.8 * fs.
                approx_ascent = fs * 0.8
                if self._is_leftward_vertical_flow(settings):
                    rect_x = self._press_pos[0] - box_w + approx_ascent
                else:
                    rect_x = self._press_pos[0] - approx_ascent
            else:
                rect_x = self._press_pos[0]
            rect = (rect_x, self._press_pos[1], box_w, box_h)

        # Point Text (click): no word wrap, auto-expands on input.
        # Area Text (drag): word wrap within the dragged frame.
        is_area = moved > 4.0
        # Ensure a vector layer exists so the preview can render.
        # _ensure_text_layer creates one if needed but does NOT commit
        # the text object yet — that happens in _finish_inline_edit.
        target_layer_id, created_layer_id = self._ensure_text_layer()
        started = self._start_inline_edit(
            initial_text="",
            rect=rect,
            word_wrap=is_area,
            layer_id=target_layer_id,
            target_hit=None,
        )
        if started and self._inline_state is not None and created_layer_id:
            self._inline_state.created_layer_id = created_layer_id
        if not started:
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            self._inline_controller.discard_empty_created_layer(canvas, created_layer_id)
            self._show_status(_ts("Inline text editor is not available."))

    @staticmethod
    def _font_based_box_size(
        settings: TextToolSettings,
    ) -> tuple[float, float]:
        """Return (width, height) for an initial text box based on font size."""
        fs = max(8.0, settings.font_size_pt)
        pad_w = settings.padding_left + settings.padding_right
        pad_h = settings.padding_top + settings.padding_bottom
        _CHARS = 12
        _LINES = 3
        if settings.vertical:
            col_w = fs * settings.line_height
            char_h = fs * 1.5 + settings.tracking
            width = max(320.0, col_w * _LINES + pad_w)
            height = max(320.0, char_h * _CHARS + pad_h)
        else:
            char_w = fs * 0.7
            line_h = fs * 1.3 * settings.line_height
            width = max(320.0, char_w * _CHARS + pad_w)
            height = max(line_h + pad_h, line_h * _LINES + pad_h)
        return (width, height)

    def _edit_existing_text(self, hit: _TextHit) -> None:
        if not self.host_api:
            return
        current = extract_text_content(hit.payload)
        word_wrap = extract_word_wrap(hit.payload)
        started = self._start_inline_edit(
            initial_text=current,
            rect=hit.rect,
            word_wrap=word_wrap,
            layer_id=hit.layer_id,
            target_hit=hit,
        )
        if not started:
            self._show_status(_ts("Inline text editor is not available."))

    def _convert_selected_point_text_to_area(self) -> bool:
        """Convert the selected Point Text object to Area Text when applicable."""
        if self._inline_editor is not None and self._inline_state is not None:
            state = self._inline_state
            if state.word_wrap:
                return True
            state.word_wrap = True
            content = self._inline_editor.toPlainText()
            settings = self._inline_effective_settings(state=state)
            object_id = state.object_id or "text-inline-preview"
            source = state.object_ref
            if source is None:
                source = ensure_text_vector_object(
                    object_id=object_id,
                    content=content,
                    rect=state.rect,
                    settings=settings,
                    word_wrap=True,
                    rotation_deg=state.rotation_deg,
                )
            new_obj = replace_text_property(replace_edited_text(source, content), "word_wrap", True)
            state.object_ref = new_obj
            if state.metadata_history is None:
                state.metadata_history = TextEditMetadataHistory(
                    new_obj, undo_steps=self._inline_editor.document().availableUndoSteps()
                )
            if state.object_id:
                cmd = VectorReplaceCommand(
                    target_id=state.object_id,
                    new_object=new_obj,
                    layer_id=state.layer_id,
                )
                self._submit_command(cmd)
            self._sync_inline_editor_geometry()
            self._update_inline_text_preview()
            return True

        hit = self._selected_text_hit()
        if hit is None:
            return False
        if extract_word_wrap(hit.payload):
            return True

        object_id = str(getattr(hit.obj, "id", ""))
        if not object_id:
            return False
        rect = self._payload_rect(hit.payload, fallback=hit.rect)
        new_obj = replace_text_property(hit.obj, "word_wrap", True)
        cmd = VectorReplaceCommand(
            target_id=object_id,
            new_object=new_obj,
            layer_id=hit.layer_id,
        )
        self._submit_command(cmd)
        self._set_inline_selection_for_object(
            object_id,
            hit.layer_id,
            rect,
            rotation_deg=self._payload_rotation(hit.payload),
            preview=False,
        )
        return True

    def _start_inline_edit(
        self,
        *,
        initial_text: str,
        rect: tuple[float, float, float, float],
        word_wrap: bool,
        layer_id: str | None,
        target_hit: _TextHit | None,
    ) -> bool:
        from .native_color import new_ink_text_object  # noqa: PLC0415

        self._finish_inline_edit(commit=True)
        if not self._edit_lifecycle.can_edit_layer(layer_id):
            return False
        canvas_widget = self._canvas_widget()
        if canvas_widget is None:
            return False
        object_id = None
        if target_hit is not None:
            raw_object_id = getattr(target_hit.obj, "id", None)
            if raw_object_id:
                object_id = str(raw_object_id)
        self._edit_lifecycle.begin()
        started = self._inline_controller.start(
            initial_text=initial_text,
            rect=rect,
            word_wrap=word_wrap,
            layer_id=layer_id,
            object_id=object_id,
            object_ref=(
                clone_text_for_edit(target_hit.obj)
                if target_hit is not None
                else new_ink_text_object(self, initial_text, rect, word_wrap)
            ),
            rotation_deg=(
                self._payload_rotation(target_hit.payload) if target_hit is not None else 0.0
            ),
            is_new=(target_hit is None),
            canvas_widget=canvas_widget,
            on_text_changed=self._on_inline_text_changed,
            on_cursor_changed=self._on_inline_cursor_changed,
            on_preedit_changed=self._on_inline_preedit_changed,
            finish_requested=lambda commit: self._finish_inline_edit(commit=commit),
            sync_geometry=self._sync_inline_editor_geometry,
            make_handle_hit_test=self._make_editor_handle_hit_test,
            update_selection_overlay=self._update_inline_selection_overlay,
            update_text_preview=self._update_inline_text_preview,
            request_ime_update=self._request_ime_update,
            begin_edit_transaction=self._begin_edit_transaction,
            emit_text_editing=self._emit_text_editing,
        )
        if started and self._inline_state.object_ref is not None:
            self._inline_state.metadata_history = TextEditMetadataHistory(
                self._inline_state.object_ref,
                undo_steps=self._inline_editor.document().availableUndoSteps(),
            )
        if started:
            self._properties.sync(force=True)
            self._path_text.attach()
        return started

    def _shift_tracking_runs_on_edit(
        self,
        state: _InlineEditState,
        editor: _InlineTextEditor,
    ) -> None:
        """Update detached content and ranges relative to the previous edit."""
        obj_ref = state.object_ref
        if obj_ref is None or extract_text_payload(obj_ref) is None:
            return
        new_text = editor.toPlainText()
        cursor = editor.textCursor()
        cursor_pos = (
            python_index_from_utf16(new_text, cursor.position()) if cursor else len(new_text)
        )
        undo_steps = editor.document().availableUndoSteps()
        if state.metadata_history is None:
            state.metadata_history = TextEditMetadataHistory(
                obj_ref, undo_steps=max(0, undo_steps - 1)
            )
        state.object_ref = state.metadata_history.update(
            obj_ref, new_text, undo_steps=undo_steps, cursor_position=cursor_pos
        )

    def _on_inline_text_changed(self) -> None:
        state = self._inline_state
        editor = self._inline_editor
        if state is None or editor is None:
            return
        # Shift tracking_runs when text changes
        self._shift_tracking_runs_on_edit(state, editor)
        settings = self._inline_effective_settings(state=state)
        content, _ = self._inline_preview_content_and_cursor(editor)
        self._sync_auto_size_rect_for_inline(
            state,
            content=content,
            settings=settings,
        )
        self._sync_inline_editor_geometry()
        self._update_inline_selection_overlay(preview=True)
        self._update_inline_text_preview()

    def _on_inline_preedit_changed(self) -> None:
        state = self._inline_state
        editor = self._inline_editor
        if state is None or editor is None:
            return
        settings = self._inline_effective_settings(state=state)
        content, _ = self._inline_preview_content_and_cursor(editor)
        self._sync_auto_size_rect_for_inline(
            state,
            content=content,
            settings=settings,
        )
        self._sync_inline_editor_geometry()
        self._update_inline_selection_overlay(preview=True)
        self._update_inline_text_preview()
        self._properties.sync()

    def _on_inline_cursor_changed(self) -> None:
        if self._inline_editor is None or self._inline_state is None:
            return
        self._request_ime_update()
        self._update_inline_text_preview()
        self._properties.sync()

    def _finish_inline_edit(self, *, commit: bool) -> None:  # noqa: PLR0911, PLR0912
        if self._inline_finishing:
            return
        editor = self._inline_editor
        state = self._inline_state
        if editor is None or state is None:
            self._set_inline_shape_preview_active(False)
            return

        same_page = self._edit_lifecycle.same_page()
        commit = commit and same_page and self._edit_lifecycle.can_edit_layer(state.layer_id)
        # Save preview meta (especially shape_frame) BEFORE clearing,
        # because _clear_inline_text_preview sets _vector_preview = None.
        _preview_meta: dict[str, Any] | None = None
        _canvas_tmp = getattr(self.host_api, "canvas", None) if self.host_api else None
        _prev_obj = getattr(_canvas_tmp, "_vector_preview", None) if _canvas_tmp else None
        if _prev_obj is not None:
            _pm = getattr(_prev_obj, "meta", None)
            if isinstance(_pm, Mapping):
                _preview_meta = dict(_pm)

        self._path_text.detach_editor()
        finished = self._inline_controller.take_finish_context()
        if finished is None:
            return
        text_value = finished.text_value
        state = finished.state

        self._clear_inline_text_preview()

        content = text_value
        rect = state.rect
        settings = self._inline_effective_settings(state=state)

        try:
            if not commit:
                if state.object_id and same_page:
                    self._set_inline_selection_for_object(
                        state.object_id,
                        state.layer_id,
                        rect,
                        rotation_deg=state.rotation_deg,
                        preview=False,
                    )
                else:
                    self._clear_inline_selection_if_owned()
                return

            if not content.strip("\n"):
                if state.object_id:
                    self._set_inline_selection_for_object(
                        state.object_id,
                        state.layer_id,
                        rect,
                        rotation_deg=state.rotation_deg,
                        preview=False,
                    )
                else:
                    self._clear_inline_selection_if_owned()
                return

            if state.object_id:
                current_hit = self._find_text_object_by_id(state.object_id, layer_id=state.layer_id)
                if current_hit is None:
                    self._clear_inline_selection_if_owned()
                    return
                if content == extract_text_content(current_hit.payload):
                    self._set_inline_selection_for_object(
                        state.object_id,
                        state.layer_id,
                        rect,
                        rotation_deg=state.rotation_deg,
                        preview=False,
                    )
                    return
                new_obj = replace_edited_text(
                    state.object_ref,
                    content,
                    rect=rect,
                    rotation_deg=state.rotation_deg,
                )
                cmd = VectorReplaceCommand(
                    target_id=state.object_id,
                    new_object=new_obj,
                    layer_id=state.layer_id,
                )
                self._submit_command(cmd)
                sync_text_layer_name(
                    self,
                    state.layer_id,
                    state.object_id,
                    content,
                    previous_content=extract_text_content(current_hit.payload),
                )
                self._set_inline_selection_for_object(
                    state.object_id,
                    state.layer_id,
                    rect,
                    rotation_deg=state.rotation_deg,
                    preview=False,
                )
                return

            target_layer_id = state.layer_id
            created_layer_id = state.created_layer_id
            if target_layer_id is None:
                target_layer_id, created_layer_id = self._ensure_text_layer()
            if target_layer_id is None:
                self._clear_inline_selection_if_owned()
                self._show_status(_ts("Text layer could not be created."))
                return
            new_obj = ensure_text_vector_object(
                object_id=None,
                content=content,
                rect=rect,
                settings=settings,
                word_wrap=state.word_wrap,
                rotation_deg=state.rotation_deg,
                previous_meta=_preview_meta,
            )
            new_obj = _dc_replace(
                replace_edited_text(
                    state.object_ref if state.object_ref is not None else new_obj,
                    content,
                    rect=rect,
                    rotation_deg=state.rotation_deg,
                ),
                id=new_obj.id,
            )
            cmd = VectorCreateCommand(
                new_obj,
                layer_id=target_layer_id,
                merge_key="text_tool.create",
                created_layer_id=created_layer_id,
            )
            self._submit_command(cmd)
            sync_text_layer_name(self, target_layer_id, new_obj.id, content)
            self._set_inline_selection_for_object(
                str(getattr(new_obj, "id", "")),
                target_layer_id,
                rect,
                rotation_deg=state.rotation_deg,
                preview=False,
            )
        finally:
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            if same_page:
                self._inline_controller.discard_empty_created_layer(canvas, state.created_layer_id)
            self._edit_lifecycle.clear()
            self._end_edit_transaction()

    def _set_inline_selection_for_object(
        self,
        object_id: str,
        layer_id: str | None,
        rect: tuple[float, float, float, float],
        *,
        rotation_deg: float,
        preview: bool,
    ) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        bounds = self._rect_to_bounds(rect, rotation_deg=rotation_deg)
        try:
            payload = {
                "id": object_id,
                "path": 0,
                "anchor": 0,
                "layer_id": layer_id,
                "preview": bool(preview),
                "transform_bounds": bounds,
                "mode": "transform",
                "owner": "text_tool_inline",
            }
            self._apply_auto_return_metadata(payload)
            canvas._vector_selection = payload  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
        except Exception:
            logger.debug("TextTool set inline selection failed", exc_info=True)

    def _refresh_inline_editor_after_setting_change(self) -> None:
        if self._inline_editor is None or self._inline_state is None:
            return
        state = self._inline_state
        # For new (uncommitted) text boxes, rebuild object_ref with current global
        # settings so that _inline_effective_settings picks up the latest values.
        if state.object_id is None and state.object_ref is None:
            content = self._inline_editor.toPlainText()
            settings = self._settings()
            new_obj = ensure_text_vector_object(
                object_id="text-inline-preview",
                content=content,
                rect=state.rect,
                settings=settings,
                word_wrap=state.word_wrap,
                rotation_deg=state.rotation_deg,
                previous_meta=getattr(state.object_ref, "meta", None),
            )
            state.object_ref = new_obj
            state.metadata_history = TextEditMetadataHistory(
                new_obj, undo_steps=self._inline_editor.document().availableUndoSteps()
            )
        content, _ = self._inline_preview_content_and_cursor(self._inline_editor)
        settings = self._inline_effective_settings(state=state)
        self._sync_auto_size_rect_for_inline(
            state,
            content=content,
            settings=settings,
        )
        self._sync_inline_editor_geometry()
        # _update_inline_text_preview handles overlay + recompose internally
        self._update_inline_text_preview()

    def _update_inline_selection_overlay(self, *, preview: bool) -> None:
        if not self.host_api or self._inline_state is None:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        state = self._inline_state
        settings = self._inline_effective_settings(state=state)
        bounds = self._rect_to_bounds(state.rect, rotation_deg=state.rotation_deg)
        object_id = state.object_id or "text-inline-preview"
        payload: dict[str, Any] = {
            "id": object_id,
            "path": 0,
            "anchor": 0,
            "layer_id": state.layer_id,
            "preview": bool(preview),
            "mode": "transform",
            "owner": "text_tool_inline",
        }
        # Show bounding box / transform handles only on hover during inline editing.
        if getattr(self, "_inline_hover_handles", False) or self._transform_state is not None:
            payload["transform_bounds"] = bounds
        if (
            state.object_id is not None
            and preview
            and (
                settings.vertical
                or (not self._inline_uses_shape_layout(state))
                or self._inline_shape_preview_active
            )
        ):
            payload["hide_source_text"] = True
        if state.object_id is None:
            payload["items"] = [{"id": object_id}]
            payload["overlay_layer_id"] = state.layer_id
        self._apply_auto_return_metadata(payload)
        try:
            canvas._vector_selection = payload  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
        except Exception:
            logger.debug("TextTool update inline selection failed", exc_info=True)

    def _clear_inline_selection_if_owned(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, Mapping):
            return
        if selection.get("owner") != "text_tool_inline":
            return
        try:
            canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
        except Exception:
            logger.debug("TextTool clear inline selection failed", exc_info=True)

    def _selected_text_hit(self) -> _TextHit | None:  # noqa: PLR0911, PLR0912
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return None
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, Mapping):
            return None
        selected_id = selection.get("id")
        if not selected_id:
            return None
        selected_id = str(selected_id)
        selected_layer = selection.get("layer_id")
        getter = getattr(canvas, "get_vector_objects", None)
        if not callable(getter):
            return None
        try:
            objects = list(getter())
        except Exception:
            return None
        for layer_id, obj in reversed(objects):
            if str(getattr(obj, "id", "")) != selected_id:
                continue
            if selected_layer not in (None, str(layer_id)):
                continue
            if not is_text_vector_object(obj):
                continue
            payload = extract_text_payload(obj)
            if payload is None:
                continue
            rect = self._object_rect(obj, payload)
            if rect is None:
                continue
            return _TextHit(layer_id=str(layer_id), obj=obj, rect=rect, payload=payload)
        # Fallback: new (uncommitted) text box exists only as _vector_preview
        state = self._inline_state
        if state is not None and state.object_id is None and selected_id == "text-inline-preview":
            canvas_obj = getattr(canvas, "_vector_preview", None)
            obj_ref = canvas_obj if canvas_obj is not None else state.object_ref
            if obj_ref is not None:
                fb_payload = extract_text_payload(obj_ref)
                if fb_payload is None:
                    fb_payload = {
                        "x": state.rect[0],
                        "y": state.rect[1],
                        "width": state.rect[2],
                        "height": state.rect[3],
                        "rotation_deg": state.rotation_deg,
                    }
                fb_rect = self._object_rect(obj_ref, fb_payload)
                if fb_rect is None:
                    fb_rect = state.rect
                fb_layer_id = state.layer_id or selected_layer
                return _TextHit(
                    layer_id=fb_layer_id,
                    obj=obj_ref,
                    rect=fb_rect,
                    payload=fb_payload,
                )
        return None

    def _find_text_object_by_id(
        self,
        object_id: str,
        *,
        layer_id: str | None = None,
    ) -> _TextHit | None:
        if not self.host_api or not object_id:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        getter = getattr(canvas, "get_vector_objects", None) if canvas else None
        if not callable(getter):
            return None
        try:
            objects = list(getter())
        except Exception:
            return None
        for current_layer_id, obj in reversed(objects):
            if str(getattr(obj, "id", "")) != str(object_id):
                continue
            if layer_id not in (None, str(current_layer_id)):
                continue
            if not is_text_vector_object(obj):
                continue
            payload = extract_text_payload(obj)
            if payload is None:
                continue
            rect = self._object_rect(obj, payload)
            if rect is None:
                continue
            return _TextHit(
                layer_id=str(current_layer_id),
                obj=obj,
                rect=rect,
                payload=payload,
            )
        return None

    def _apply_settings_to_selected_text(self, key: str, value: Any) -> None:
        if not self.host_api or self._transform_state is not None:
            return
        # New (uncommitted) text box: update inline state directly, skip command
        inline_new = self._inline_state is not None and self._inline_state.object_id is None
        if inline_new:
            state = self._inline_state
            if state.object_ref is not None:
                state.object_ref = replace_text_property(state.object_ref, key, value)
            if key == "word_wrap":
                state.word_wrap = bool(value)
            return
        hit = self._selected_text_hit()
        if hit is None and self._inline_state is not None and self._inline_state.object_id:
            hit = self._find_text_object_by_id(
                self._inline_state.object_id,
                layer_id=self._inline_state.layer_id,
            )
        if hit is None:
            return
        content = extract_text_content(hit.payload)
        if not content and key != "text_flow":
            return
        word_wrap = bool(value) if key == "word_wrap" else extract_word_wrap(hit.payload)
        rect = self._payload_rect(hit.payload, fallback=hit.rect)
        rotation_deg = self._payload_rotation(hit.payload)
        new_obj = replace_text_property(hit.obj, key, value)
        if key == "text_flow":
            word_wrap = True
        if new_obj is hit.obj:
            return
        cmd = VectorReplaceCommand(
            target_id=str(getattr(hit.obj, "id", "")),
            new_object=new_obj,
            layer_id=hit.layer_id,
        )
        self._submit_command(cmd)
        if self._inline_edit_matches_object(str(getattr(hit.obj, "id", ""))):
            self._inline_state.rect = rect
            self._inline_state.rotation_deg = rotation_deg
            local_ref = self._inline_state.object_ref
            self._inline_state.object_ref = replace_text_property(
                local_ref if local_ref is not None else new_obj, key, value
            )
            self._inline_state.word_wrap = word_wrap
            self._update_inline_selection_overlay(preview=True)
        else:
            self._set_inline_selection_for_object(
                str(getattr(hit.obj, "id", "")),
                hit.layer_id,
                rect,
                rotation_deg=rotation_deg,
                preview=False,
            )

    def _apply_tracking_to_selection(
        self, sel_start: int, sel_end: int, tracking_value: float
    ) -> None:
        """Apply tracking to the selected character range only (tracking_runs)."""
        state = self._inline_state
        if state is None:
            return
        obj_ref = state.object_ref
        payload = extract_text_payload(obj_ref) if obj_ref is not None else None
        existing_runs: list[dict[str, Any]] = []
        if payload is not None:
            raw = payload.get("tracking_runs")
            if isinstance(raw, (list, tuple)):
                existing_runs = list(raw)
        content, _ = self._inline_preview_content_and_cursor(self._inline_editor)
        text_length = len(content)
        new_runs = merge_tracking_run(
            existing_runs, sel_start, sel_end, tracking_value, text_length
        )
        if obj_ref is None:
            obj_ref = ensure_text_vector_object(
                object_id=state.object_id or "text-inline-preview",
                content=content,
                rect=state.rect,
                settings=self._inline_effective_settings(state=state),
                word_wrap=state.word_wrap,
                rotation_deg=state.rotation_deg,
            )
        new_obj = replace_text_property(
            replace_edited_text(obj_ref, content), "tracking_runs", new_runs
        )
        state.object_ref = new_obj
        if state.metadata_history is not None:
            state.metadata_history.apply_range_format(
                "tracking_runs", sel_start, sel_end, tracking_value, new_obj
            )
        else:
            state.metadata_history = TextEditMetadataHistory(
                new_obj, undo_steps=self._inline_editor.document().availableUndoSteps()
            )
        # For committed objects, also submit a replace command
        if state.object_id is not None and state.layer_id is not None:
            cmd = VectorReplaceCommand(
                target_id=state.object_id,
                new_object=new_obj,
                layer_id=state.layer_id,
            )
            self._submit_command(cmd)
        self._update_inline_text_preview()

    def _apply_line_height_to_selection(
        self, sel_start: int, sel_end: int, line_height_value: float
    ) -> None:
        """Apply line_height to the selected character range only (line_height_runs)."""
        state = self._inline_state
        if state is None:
            return
        obj_ref = state.object_ref
        payload = extract_text_payload(obj_ref) if obj_ref is not None else None
        existing_runs: list[dict[str, Any]] = []
        if payload is not None:
            raw = payload.get("line_height_runs")
            if isinstance(raw, (list, tuple)):
                existing_runs = list(raw)
        content, _ = self._inline_preview_content_and_cursor(self._inline_editor)
        text_length = len(content)
        new_runs = merge_line_height_run(
            existing_runs, sel_start, sel_end, line_height_value, text_length
        )
        if obj_ref is None:
            obj_ref = ensure_text_vector_object(
                object_id=state.object_id or "text-inline-preview",
                content=content,
                rect=state.rect,
                settings=self._inline_effective_settings(state=state),
                word_wrap=state.word_wrap,
                rotation_deg=state.rotation_deg,
            )
        new_obj = replace_text_property(
            replace_edited_text(obj_ref, content), "line_height_runs", new_runs
        )
        state.object_ref = new_obj
        if state.metadata_history is not None:
            state.metadata_history.apply_range_format(
                "line_height_runs", sel_start, sel_end, line_height_value, new_obj
            )
        else:
            state.metadata_history = TextEditMetadataHistory(
                new_obj, undo_steps=self._inline_editor.document().availableUndoSteps()
            )
        if state.object_id is not None and state.layer_id is not None:
            cmd = VectorReplaceCommand(
                target_id=state.object_id,
                new_object=new_obj,
                layer_id=state.layer_id,
            )
            self._submit_command(cmd)
        self._update_inline_text_preview()

    def _begin_transform(
        self,
        hit: _TextHit,
        handle: str,
        mode: str,
        press_point: tuple[float, float],
    ) -> None:
        if not self._edit_lifecycle.can_edit_layer(hit.layer_id):
            return
        self._edit_lifecycle.begin()
        self._transform_controller.begin(hit, handle, mode, press_point)

    def _update_transform_preview(
        self, current_point: tuple[float, float], *, uniform: bool = False
    ) -> None:
        self._transform_controller.update_preview(current_point, uniform=uniform)

    def _apply_transform_preview(self) -> None:
        self._transform_controller.apply_preview()

    def _commit_transform(self) -> None:
        self._transform_controller.commit()

    def _cancel_transform(self) -> None:
        self._transform_controller.cancel()

    def _clear_text_preview(self) -> None:
        self._transform_controller.clear_preview()

    def _settings_from_payload(self, payload: Mapping[str, Any]) -> TextToolSettings:
        fallback = self._settings()
        style_defaults: Mapping[str, Any] | None = None
        style_map = payload.get("style_map")
        if isinstance(style_map, Mapping):
            style_candidate = style_map.get("default")
            if isinstance(style_candidate, Mapping):
                style_defaults = style_candidate

        color_raw = (
            style_defaults.get("color_rgba", list(fallback.color_rgba))
            if style_defaults is not None
            else payload.get("color_rgba", list(fallback.color_rgba))
        )
        color = fallback.color_rgba
        if isinstance(color_raw, (list, tuple)) and len(color_raw) >= 4:
            try:
                color = (
                    max(0, min(255, round(float(color_raw[0])))),
                    max(0, min(255, round(float(color_raw[1])))),
                    max(0, min(255, round(float(color_raw[2])))),
                    max(0, min(255, round(float(color_raw[3])))),
                )
            except (TypeError, ValueError):
                color = fallback.color_rgba

        align_raw: Any = payload.get("align", fallback.align)
        line_height_raw: Any = payload.get("line_height", fallback.line_height)
        vertical_flow_raw: Any = payload.get("vertical_flow", fallback.vertical_flow)
        document = payload.get("document")
        if isinstance(document, Mapping):
            paragraphs = document.get("paragraphs")
            if isinstance(paragraphs, Sequence) and paragraphs:
                first = paragraphs[0]
                if isinstance(first, Mapping):
                    align_raw = first.get("align", align_raw)
                    line_height_raw = first.get("line_height", line_height_raw)
                    vertical_flow_raw = first.get("vertical_flow", vertical_flow_raw)

        align = str(align_raw).strip().lower()
        if align not in {"left", "center", "right", "justify"}:
            align = fallback.align

        font_family_raw = payload.get("font_family", fallback.font_family)
        font_style_raw = payload.get("font_style", fallback.font_style)
        font_size_raw = payload.get("font_size_pt", fallback.font_size_pt)
        vertical_raw = payload.get("vertical", fallback.vertical)
        tracking_raw = payload.get("tracking", fallback.tracking)
        layout_sec = payload.get("layout")
        _ls = layout_sec if isinstance(layout_sec, Mapping) else {}

        def _pad(side: str, fb: float) -> float:
            raw = _ls.get(f"padding_{side}", payload.get(f"padding_{side}", fb))
            try:
                return max(0.0, min(256.0, float(raw)))
            except (TypeError, ValueError):
                return fb

        pad_top = _pad("top", fallback.padding_top)
        pad_right = _pad("right", fallback.padding_right)
        pad_bottom = _pad("bottom", fallback.padding_bottom)
        pad_left = _pad("left", fallback.padding_left)
        if style_defaults is not None:
            font_family_raw = style_defaults.get("font_family", font_family_raw)
            font_style_raw = style_defaults.get("font_style", font_style_raw)
            font_size_raw = style_defaults.get("font_size_pt", font_size_raw)
            vertical_raw = style_defaults.get("vertical", vertical_raw)
            tracking_raw = style_defaults.get("tracking", tracking_raw)

        try:
            font_size_pt = float(font_size_raw)
        except (TypeError, ValueError):
            font_size_pt = fallback.font_size_pt
        try:
            line_height = float(line_height_raw)
        except (TypeError, ValueError):
            line_height = fallback.line_height
        try:
            tracking = float(tracking_raw)
        except (TypeError, ValueError):
            tracking = fallback.tracking
        return TextToolSettings(
            word_wrap=extract_word_wrap(payload),
            font_family=str(font_family_raw or ""),
            font_style=str(font_style_raw or ""),
            font_size_pt=max(1.0, min(512.0, font_size_pt)),
            color_rgba=color,
            vertical=bool(vertical_raw),
            vertical_flow=normalize_vertical_flow(
                vertical_flow_raw,
                default=fallback.vertical_flow,
            ),
            align=align,
            line_height=max(0.5, min(4.0, line_height)),
            tracking=max(-64.0, min(128.0, tracking)),
            padding_top=pad_top,
            padding_right=pad_right,
            padding_bottom=pad_bottom,
            padding_left=pad_left,
            rotate_half_width=bool(
                (style_defaults or {}).get(
                    "rotate_half_width", payload.get("rotate_half_width", False)
                )
            ),
            typography=typography_from_payload(payload),
        )

    @staticmethod
    def _payload_rect(
        payload: Mapping[str, Any],
        *,
        fallback: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        frame = payload.get("frame")
        if isinstance(frame, Mapping):
            try:
                x = float(frame.get("x", payload.get("x")))
                y = float(frame.get("y", payload.get("y")))
                w = max(1.0, float(frame.get("width", payload.get("width"))))
                h = max(1.0, float(frame.get("height", payload.get("height"))))
                return (x, y, w, h)
            except (TypeError, ValueError):
                logger.debug("frame bbox parse failed", exc_info=True)
        try:
            x = float(payload.get("x"))
            y = float(payload.get("y"))
            w = max(1.0, float(payload.get("width")))
            h = max(1.0, float(payload.get("height")))
            return (x, y, w, h)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _payload_rotation(payload: Mapping[str, Any]) -> float:
        transform = payload.get("transform")
        if isinstance(transform, Mapping):
            try:
                angle = float(transform.get("rotation_deg", payload.get("rotation_deg", 0.0)))
            except (TypeError, ValueError):
                angle = 0.0
        else:
            try:
                angle = float(payload.get("rotation_deg", 0.0))
            except (TypeError, ValueError):
                angle = 0.0
        if not math.isfinite(angle):
            return 0.0
        return angle

    def _sync_inline_editor_geometry(self, *args: Any) -> None:
        del args
        editor = self._inline_editor
        state = self._inline_state
        canvas_widget = self._inline_canvas_widget
        if editor is None or state is None or canvas_widget is None:
            return
        min_x, min_y, max_x, max_y = self._rect_to_bounds(
            state.rect, rotation_deg=state.rotation_deg
        )
        widget_rect = self._canvas_rect_to_widget_rect(
            canvas_widget, (min_x, min_y, max_x - min_x, max_y - min_y)
        )
        if widget_rect is None:
            return
        left, top, width, height = widget_rect
        settings = self._inline_effective_settings(state=state)
        font = self._build_font(settings)
        zoom = self._canvas_zoom(canvas_widget)
        font.setPointSizeF(max(1.0, float(settings.font_size_pt) * zoom))
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing,
            float(settings.tracking) * zoom,
        )
        editor.setFont(font)
        editor.set_vertical_text_layout(
            enabled=settings.vertical,
            vertical_flow=settings.vertical_flow,
            align=settings.align,
            line_height=settings.line_height,
        )
        if settings.vertical:
            wrap_mode = getattr(QPlainTextEdit.LineWrapMode, "FixedPixelWidth", None)
            if wrap_mode is None:
                wrap_mode = getattr(QPlainTextEdit.LineWrapMode, "FixedColumnWidth", None)
            if wrap_mode is None:
                wrap_mode = QPlainTextEdit.LineWrapMode.WidgetWidth
            editor.setLineWrapMode(wrap_mode)
            set_wrap_width = getattr(editor, "setLineWrapColumnOrWidth", None)
            if callable(set_wrap_width):
                set_wrap_width(1)
            editor.setWordWrapMode(QTextOption.WrapMode.WrapAnywhere)
        else:
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            editor.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        r, g, b, _a = settings.color_rgba
        # Always use canvas preview rendering for WYSIWYG editing.
        hide_editor_selection = True
        text_alpha = 0
        selection_style = (
            "QPlainTextEdit {"
            "selection-background-color: transparent;"
            "selection-color: transparent;"
            "}"
            "QPlainTextEdit::selection {"
            "background: transparent;"
            "color: transparent;"
            "}"
            if hide_editor_selection
            else ""
        )
        style = (
            "QPlainTextEdit {"
            f"color: rgba({r}, {g}, {b}, {text_alpha});"
            "background: transparent;"
            "background-color: rgba(0, 0, 0, 0);"
            "border: 0px;"
            "padding: 0px;"
            "}"
            'QPlainTextEdit[readOnly="false"] {'
            "background: transparent;"
            "background-color: rgba(0, 0, 0, 0);"
            "}"
            "QPlainTextEdit QWidget {"
            "background: transparent;"
            "background-color: rgba(0, 0, 0, 0);"
            "}"
            "QPlainTextEdit::viewport {"
            "background: transparent;"
            "background-color: rgba(0, 0, 0, 0);"
            "}"
            "QPlainTextEdit #qt_scrollarea_viewport {"
            "background: transparent;"
            "background-color: rgba(0, 0, 0, 0);"
            "}"
        )
        if selection_style:
            style += selection_style
        editor.setStyleSheet(style)
        _force_transparent_inline_editor(editor)
        editor.setGeometry(
            math.floor(left),
            math.floor(top),
            max(12, math.ceil(left + width) - math.floor(left)),
            max(12, math.ceil(top + height) - math.floor(top)),
        )
        # Keep cursor width >= 1 so cursorRect() returns a non-degenerate
        # rectangle for IME candidate window positioning on Windows.
        # The cursor bar is invisible because text colour alpha is 0.
        editor.setCursorWidth(1)
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        preview = getattr(canvas, "_vector_preview", None)
        layout_payload = extract_text_payload(preview or state.object_ref)
        if layout_payload is not None:
            to_widget = canvas_widget.viewport.canvas_to_widget
            x, y, _w, _h = state.rect
            ox, oy = to_widget(x, y)
            xx, xy = to_widget(x + 1.0, y)
            yx, yy = to_widget(x, y + 1.0)
            frame_to_editor = QTransform(
                xx - ox, xy - oy, yx - ox, yy - oy, ox - editor.x(), oy - editor.y()
            )
            editor.set_text_layout_payload(layout_payload, frame_to_editor=frame_to_editor)
        self._path_text.sync_body_wrap(editor)
        self._request_ime_update()

    def _inline_uses_shape_layout(self, state: _InlineEditState | None = None) -> bool:
        current = state or self._inline_state
        if current is None:
            return False
        obj = current.object_ref
        if obj is None:
            return False
        payload = extract_text_payload(obj)
        if payload is None:
            return False
        layout = payload.get("layout")
        if isinstance(layout, Mapping):
            wrap_mode = str(layout.get("wrap_mode", "")).strip().lower()
            if wrap_mode == "shape":
                return True
        return isinstance(payload.get("shape_frame"), Mapping)

    def _update_inline_text_preview(self) -> None:
        if not self.host_api:
            self._set_inline_shape_preview_active(False)
            return
        editor = self._inline_editor
        state = self._inline_state
        if editor is None or state is None:
            self._set_inline_shape_preview_active(False)
            return
        if self._inline_transform_preview_active(state):
            return
        payload = extract_text_payload(state.object_ref) if state.object_ref is not None else None
        settings = self._inline_effective_settings(state=state, payload=payload)
        if not self._inline_needs_canvas_preview(state=state, settings=settings):
            self._set_inline_shape_preview_active(False)
            self._clear_inline_text_preview()
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        content, cursor_pos = self._inline_preview_content_and_cursor(editor)
        selection = self._inline_selection_range(editor)
        # Only a new selection-based area may carry its shape on the preview.
        # Existing text is rebuilt from detached committed edit state, never IME data.
        prev_meta = getattr(state.object_ref, "meta", None)
        existing_preview = getattr(canvas, "_vector_preview", None) if canvas else None
        if state.object_ref is None and existing_preview is not None:
            ep_meta = getattr(existing_preview, "meta", None)
            if isinstance(ep_meta, Mapping) and "text_tool" in ep_meta:
                prev_meta = ep_meta
        has_shape = self._inline_uses_shape_layout(state)
        if not has_shape and isinstance(prev_meta, Mapping):
            tt = prev_meta.get("text_tool")
            if isinstance(tt, Mapping) and isinstance(tt.get("shape_frame"), Mapping):
                has_shape = True
        wrap_override = "shape" if has_shape else None
        if state.object_ref is not None:
            preview_obj = replace_edited_text(
                state.object_ref,
                content,
                rect=state.rect,
                rotation_deg=state.rotation_deg,
                cursor_position=self._inline_preview_edit_end(editor, cursor_pos),
            )
        else:
            preview_obj = ensure_text_vector_object(
                object_id=state.object_id or "text-inline-preview",
                content=content,
                rect=state.rect,
                settings=settings,
                word_wrap=state.word_wrap,
                wrap_mode_override=wrap_override,
                rotation_deg=state.rotation_deg,
                previous_meta=prev_meta,
            )
        selection_start = None
        selection_end = None
        if selection is not None:
            selection_start = selection[0]
            selection_end = selection[1]
        preview_obj = self._with_inline_preview_meta(
            preview_obj,
            cursor_pos=cursor_pos,
            selection_start=selection_start,
            selection_end=selection_end,
        )
        layout_payload = extract_text_payload(preview_obj)
        if layout_payload is not None:
            editor.set_text_layout_payload(
                layout_payload, zoom=self._canvas_zoom(self._inline_canvas_widget)
            )
        try:
            canvas._vector_preview = preview_obj  # type: ignore[attr-defined]  # duck-typed canvas attr
            canvas._vector_preview_layer_id = self._path_text.preview_layer_id(state)
            # Set flag directly and sync geometry without triggering
            # _update_inline_selection_overlay (which calls _recompose).
            # The overlay update below will do a single _recompose.
            was_active = self._inline_shape_preview_active
            self._inline_shape_preview_active = True
            if (
                not was_active
                and self._inline_editor is not None
                and self._inline_state is not None
            ):
                self._sync_inline_editor_geometry()
            self._update_inline_selection_overlay(preview=True)
        except Exception:
            self._inline_shape_preview_active = False
            logger.debug("TextTool inline shape preview failed", exc_info=True)

    def _clear_inline_text_preview(self) -> None:
        self._set_inline_shape_preview_active(False)
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return
        try:
            selection = getattr(canvas, "_vector_selection", None)
            owner = selection.get("owner") if isinstance(selection, Mapping) else None
            preview = getattr(canvas, "_vector_preview", None)
            if preview is None and owner != "text_tool_inline":
                return
            if owner in {None, "text_tool_inline"}:
                canvas._vector_preview = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(canvas, "notify_vector_preview_changed"):
                    canvas.notify_vector_preview_changed()
                canvas._vector_preview_layer_id = None  # type: ignore[attr-defined]  # duck-typed canvas attr
        except Exception:
            logger.debug("TextTool clear inline preview failed", exc_info=True)

    def _set_inline_shape_preview_active(self, active: bool) -> None:
        if not self._inline_controller.set_shape_preview_active(active):
            return
        if self._inline_editor is not None and self._inline_state is not None:
            self._sync_inline_editor_geometry()
            self._update_inline_selection_overlay(preview=True)

    def _disconnect_inline_canvas_signals(self) -> None:
        self._inline_controller.disconnect_canvas_signals()

    def _canvas_rect_to_widget_rect(
        self,
        canvas_widget: QWidget,
        rect: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float] | None:
        viewport = getattr(canvas_widget, "viewport", None)
        to_widget = getattr(viewport, "canvas_to_widget", None) if viewport else None
        if not callable(to_widget):
            return None
        x, y, w, h = rect
        corners = (
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
        )
        mapped: list[tuple[float, float]] = []
        for cx, cy in corners:
            try:
                wx, wy = to_widget(float(cx), float(cy))
                mapped.append((float(wx), float(wy)))
            except Exception:
                return None
        xs = [pt[0] for pt in mapped]
        ys = [pt[1] for pt in mapped]
        left = min(xs)
        top = min(ys)
        right = max(xs)
        bottom = max(ys)
        return (left, top, max(1.0, right - left), max(1.0, bottom - top))

    @staticmethod
    def _canvas_zoom(canvas_widget: QWidget) -> float:
        viewport = getattr(canvas_widget, "viewport", None)
        state = getattr(viewport, "state", None) if viewport else None
        try:
            zoom = float(getattr(state, "zoom", 1.0))
        except Exception:
            zoom = 1.0
        if not math.isfinite(zoom) or zoom <= 0.0:
            return 1.0
        return zoom

    def _canvas_widget(self) -> QWidget | None:
        if not self.host_api:
            return None
        ui = getattr(self.host_api, "ui", None)
        getter = getattr(ui, "get_main_window", None) if ui else None
        if not callable(getter):
            return None
        try:
            main_window = getter()
        except Exception:
            return None
        canvas_widget = getattr(main_window, "canvas", None)
        viewport = getattr(canvas_widget, "viewport", None)
        if isinstance(canvas_widget, QWidget) and viewport is not None:
            return canvas_widget
        return None

    @staticmethod
    def _rect_to_bounds(
        rect: tuple[float, float, float, float],
        *,
        rotation_deg: float = 0.0,
    ) -> tuple[float, float, float, float]:
        x, y, w, h = rect
        min_x = float(x)
        min_y = float(y)
        max_x = float(x + w)
        max_y = float(y + h)
        if abs(rotation_deg) <= 1e-6:
            return (min_x, min_y, max_x, max_y)
        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        rad = math.radians(float(rotation_deg))
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        corners = (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        )
        rotated: list[tuple[float, float]] = []
        for px, py in corners:
            dx = px - center_x
            dy = py - center_y
            rotated.append(
                (
                    center_x + (dx * cos_a) - (dy * sin_a),
                    center_y + (dx * sin_a) + (dy * cos_a),
                )
            )
        xs = [point[0] for point in rotated]
        ys = [point[1] for point in rotated]
        return (min(xs), min(ys), max(xs), max(ys))

    def _make_editor_handle_hit_test(self, canvas_widget: QWidget) -> Any:
        """Return a callback ``(widget_x, widget_y) -> bool``.

        The callback returns ``True`` when the widget-local position lands on a
        transform handle (scale / rotate) so that the mouse event should be
        forwarded to the canvas rather than handled by the inline editor.
        """
        tool_ref = self
        if self._inline_editor is not None:
            self._inline_editor._hover_leave_callback = lambda: clear_hover_cursor(tool_ref)

        def _test(wx: float, wy: float) -> bool:
            state = tool_ref._inline_state
            if state is None:
                return False
            viewport = getattr(canvas_widget, "viewport", None)
            to_canvas = getattr(viewport, "widget_to_canvas", None) if viewport else None
            editor = tool_ref._inline_editor
            if not callable(to_canvas) or editor is None:
                return False
            # editor-local → widget-global → canvas coordinates
            from PySide6.QtCore import QPointF  # noqa: PLC0415

            global_pos = editor.mapToParent(QPointF(wx, wy))
            try:
                cx, cy = to_canvas(float(global_pos.x()), float(global_pos.y()))
            except Exception:
                return False
            if tool_ref._path_text.handle_at(float(cx), float(cy)) is not None:
                return True
            if path_settings(extract_text_payload(state.object_ref)) is not None:
                return False
            rotation = state.rotation_deg if hasattr(state, "rotation_deg") else 0.0
            result = tool_ref._hit_test_rotated_handle(
                float(cx), float(cy), state.rect, rotation, include_move_area=False
            )
            return result is not None

        return _test

    @staticmethod
    def _path_text_label() -> str:
        from .path_text_controls import ts  # noqa: PLC0415

        return ts("Text Along Path")

    @staticmethod
    def _rect_from_drag(
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        x0, y0 = start
        x1, y1 = end
        x = min(x0, x1)
        y = min(y0, y1)
        w = max(1.0, abs(x1 - x0))
        h = max(1.0, abs(y1 - y0))
        return (x, y, w, h)

    @staticmethod
    def _drag_distance(start: tuple[float, float], end: tuple[float, float]) -> float:
        return math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1]))

    def _measure_text(self, content: str, settings: TextToolSettings) -> tuple[float, float]:
        from .typography_measurement import measure_japanese_text  # noqa: PLC0415

        text = content if content else " "
        font = self._build_font(settings)
        advanced_size = measure_japanese_text(text, settings, font=font)
        if advanced_size is not None:
            return advanced_size
        metrics = QFontMetricsF(font)
        if settings.vertical:
            columns = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            col_count = max(1, len(columns))
            max_chars = max(1, max((len(grapheme_spans(column)) for column in columns), default=1))
            col_width = max(metrics.height(), settings.font_size_pt)
            width = col_width * settings.line_height * col_count
            char_step = metrics.lineSpacing() + settings.tracking
            height = char_step * max_chars
            return (max(8.0, width), max(8.0, height))

        lines = text.splitlines() or [text]
        width = 0.0
        for line in lines:
            line_width = metrics.horizontalAdvance(line if line else " ")
            width = max(width, line_width)
        height = metrics.lineSpacing() * settings.line_height * max(1, len(lines))
        return (max(8.0, width + 8.0), max(8.0, height + 8.0))

    @staticmethod
    def _font_family_is_available(family: str) -> bool:
        requested = family.strip()
        if not requested:
            return False
        try:
            has_family = getattr(QFontDatabase, "hasFamily", None)
            if callable(has_family):
                return bool(has_family(requested))
            return any(
                str(candidate).casefold() == requested.casefold()
                for candidate in QFontDatabase.families()
            )
        except Exception:
            logger.debug("Failed to inspect available font families", exc_info=True)
            return True

    def _build_font(self, settings: TextToolSettings) -> QFont:
        family = settings.font_family.strip()
        style = settings.font_style.strip()
        font = QFont()
        if family and self._font_family_is_available(family):
            font = QFont(family)
        elif family:
            logger.debug("Text font family unavailable; using fallback font: %s", family)
        if family and style and font.family().casefold() == family.casefold():
            try:
                candidate = QFontDatabase.font(
                    family,
                    style,
                    round(settings.font_size_pt),
                )
                if (
                    candidate is not None
                    and candidate.family()
                    and candidate.family().casefold() == family.casefold()
                ):
                    font = candidate
            except Exception:
                logger.debug("Failed to resolve font style: %s / %s", family, style)
        font.setPointSizeF(max(1.0, float(settings.font_size_pt)))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(settings.tracking))
        return font

    def _object_rect(
        self,
        obj: Any,
        payload: Mapping[str, Any],
    ) -> tuple[float, float, float, float] | None:
        # Prefer the payload's x/y/width/height (unrotated frame) so that
        # rotated text boxes are handled correctly.  Fall back to the path
        # bounding box only when the payload does not carry explicit geometry.
        x = payload.get("x")
        y = payload.get("y")
        w = payload.get("width")
        h = payload.get("height")
        try:
            rx = float(x)
            ry = float(y)
            rw = max(1.0, float(w))
            rh = max(1.0, float(h))
            return (rx, ry, rw, rh)
        except (TypeError, ValueError):
            logger.debug("payload bbox parse failed", exc_info=True)

        bounds = vector_bbox_from_paths(obj)
        if bounds is not None:
            min_x, min_y, max_x, max_y = bounds
            return (min_x, min_y, max(1.0, max_x - min_x), max(1.0, max_y - min_y))

        return None

    def _ensure_text_layer(self) -> tuple[str | None, str | None]:  # noqa: PLR0911, PLR0912
        """Return ``(layer_id, created_layer_id)``.

        *created_layer_id* is non-None only when a brand-new layer was
        created so that ``VectorCreateCommand`` can clean it up on undo.
        """
        if not self.host_api:
            return None, None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._set_inline_shape_preview_active(False)
            return None, None
        layers = getattr(canvas, "_layers", None)
        if not isinstance(layers, list) or not layers:
            return None, None

        active_id = None
        current_layer = getattr(canvas, "current_layer", None)
        if callable(current_layer):
            try:
                active_ref = current_layer()
                active_id = getattr(active_ref, "id", None)
            except Exception:
                active_id = None

        active_layer = None
        if isinstance(active_id, str):
            for layer in layers:
                if getattr(layer, "id", None) == active_id:
                    active_layer = layer
                    break

        if (
            active_layer is not None
            and self._layer_is_text_layer(active_layer)
            and self._edit_lifecycle.can_edit_layer(str(active_layer.id))
        ):
            return str(active_layer.id), None

        name_index = 1
        for layer in layers:
            name = str(getattr(layer, "name", ""))
            if name.lower().startswith("text layer"):
                name_index += 1
        layer_name = _ts("Text Layer {index}").format(index=name_index)

        add_vector_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_vector_layer):
            try:
                ref = add_vector_layer(layer_name, vector_kind="text")
                layer_id = getattr(ref, "id", None)
                if isinstance(layer_id, str):
                    return layer_id, layer_id
            except Exception:
                logger.debug("add_vector_layer failed for Text Tool", exc_info=True)

        add_layer = getattr(canvas, "add_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer(layer_name, layer_type="vector", vector_kind="text")
                layer_id = getattr(ref, "id", None)
                if isinstance(layer_id, str):
                    return layer_id, layer_id
            except Exception:
                logger.debug("add_layer failed for Text Tool", exc_info=True)
        return None, None

    @staticmethod
    def _layer_is_text_layer(layer: Any) -> bool:
        name = str(getattr(layer, "name", "")).strip().lower()
        if name.startswith("text layer"):
            return True
        objects = list(getattr(layer, "vector_objects", []) or [])
        return any(is_text_vector_object(obj) for obj in objects)

    # ------------------------------------------------------------------
    # Canvas transaction helpers — group commands into a single undo entry
    # ------------------------------------------------------------------

    def _begin_edit_transaction(self) -> None:
        """Start a canvas-level transaction for the current editing session."""
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        begin = getattr(canvas, "begin_transaction", None)
        if callable(begin):
            try:
                begin("Text Edit")
            except Exception:
                logger.debug("begin_transaction failed", exc_info=True)

    def _end_edit_transaction(self) -> None:
        """End the canvas-level editing transaction (commits the undo group)."""
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        end = getattr(canvas, "end_transaction", None)
        if callable(end):
            try:
                end()
            except Exception:
                logger.debug("end_transaction failed", exc_info=True)

    _PROP_DEBOUNCE_MS = TextToolSettingsUI.PROP_DEBOUNCE_MS

    def _ensure_prop_transaction(self) -> None:
        """Start or extend a debounced property-change transaction."""
        self._settings_ui.ensure_prop_transaction()

    def _flush_prop_transaction(self) -> None:
        """End the debounced property-change transaction."""
        self._settings_ui.flush_prop_transaction()

    def _submit_command(self, cmd: Any) -> None:
        if not self._edit_lifecycle.can_edit_layer(getattr(cmd, "layer_id", None)):
            return
        if not self.host_api:
            self._pending_commands.append(cmd)
            return
        canvas = getattr(self.host_api, "canvas", None)
        submit = getattr(canvas, "submit", None) if canvas else None
        if callable(submit):
            try:
                if submit(cmd, merge_key="text_tool"):
                    return
            except Exception:
                logger.debug("TextTool submit failed", exc_info=True)
        self._pending_commands.append(cmd)

    def _show_status(self, message: str) -> None:
        if not self.host_api:
            return
        ui = getattr(self.host_api, "ui", None)
        show_message = getattr(ui, "show_message", None) if ui else None
        if callable(show_message):
            show_message(message, 3500)

    def _sync_scaled_typography_settings(
        self,
        *,
        font_size_pt: float,
        tracking: float,
    ) -> None:
        if not self.host_api:
            return
        save_text_tool_setting(self.host_api.settings, "font_size_pt", float(font_size_pt))
        save_text_tool_setting(self.host_api.settings, "tracking", float(tracking))

        tool_manager = getattr(self.host_api, "_tool_manager", None) or getattr(
            self.host_api,
            "tool_manager",
            None,
        )
        target_tool_ids = self._iter_registered_text_tool_ids(tool_manager)
        sync = getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        if callable(sync):
            for tool_id in target_tool_ids:
                with suppress(Exception):
                    sync(tool_id, "font_size_pt", float(font_size_pt))
                with suppress(Exception):
                    sync(tool_id, "tracking", float(tracking))
            return

        events = getattr(self.host_api, "events", None)
        publish = getattr(events, "publish", None) if events else None
        emit = getattr(events, "emit", None) if events else None
        updates = (
            ("font_size_pt", float(font_size_pt)),
            ("tracking", float(tracking)),
        )
        if callable(publish):
            for tool_id in target_tool_ids:
                for key, value in updates:
                    with suppress(Exception):
                        publish(
                            "tool.settings_changed",
                            {
                                "tool_id": tool_id,
                                "key": key,
                                "value": value,
                                "sync_only": True,
                                "source": "text_tool_transform",
                            },
                        )
            return
        if callable(emit):
            for tool_id in target_tool_ids:
                for key, value in updates:
                    with suppress(Exception):
                        emit(
                            "tool.settings_changed",
                            {
                                "tool_id": tool_id,
                                "key": key,
                                "value": value,
                                "sync_only": True,
                                "source": "text_tool_transform",
                            },
                        )

    # ------------------------------------------------------------------
    # Snap helpers — delegates to sdk.snap
    # ------------------------------------------------------------------

    def _snap_position(
        self, x: float, y: float, w: float = 0.0, h: float = 0.0
    ) -> tuple[float, float]:
        from ..sdk.snap import snap_position  # noqa: PLC0415

        return snap_position(self.host_api, x, y, w, h)

    def _is_snap_enabled(self) -> bool:
        from ..sdk.snap import is_snap_enabled  # noqa: PLC0415

        return is_snap_enabled(self.host_api)

    def _show_context_menu(self, payload: dict[str, Any] | None = None) -> None:
        """Show a context menu for the selected text object (non-inline state)."""
        from PySide6.QtWidgets import QMenu  # noqa: PLC0415

        canvas_widget = self._canvas_widget()
        if canvas_widget is None:
            return
        hit = self._selected_text_hit()
        menu = QMenu(canvas_widget)
        if hit is not None:
            menu.addAction(_ts("Edit"), lambda: self._edit_existing_text(hit))
            menu.addAction(
                _ts("Delete"),
                lambda: self._handle_delete_key(),
            )
            menu.addSeparator()
        pos = None
        if isinstance(payload, Mapping) and "widget_pos" in payload:
            wp = payload["widget_pos"]
            if isinstance(wp, (list, tuple)) and len(wp) >= 2:
                from PySide6.QtCore import QPoint  # noqa: PLC0415

                pos = canvas_widget.mapToGlobal(QPoint(int(wp[0]), int(wp[1])))
        if pos is None:
            pos = QCursor.pos()
        menu.exec(pos)


def _build_text_ctx(parent: Any, on_changed: Any, *, is_vertical: bool = False) -> Any:
    """Factory for TextContextContent (lazy import to avoid circular deps)."""
    from loomstroke.ui.context_toolbar.text_toolbar import TextContextContent  # noqa: PLC0415

    return TextContextContent(parent, on_changed, is_vertical=is_vertical)


class TextToolPlugin(PluginBase):
    metadata = PluginMetadata(
        id="text_tool_plugin",
        name=_ts("Text Tool"),
        version="0.1.0",
        description=_ts("Canvas text object placement and editing tool"),
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.TOOL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self.tool = TextTool(
            tool_id=TEXT_TOOL_ID,
            display_name="Text",
            order=34,
            shortcut="T",
            shortcut_description="Activate Text Tool",
            forced_vertical=False,
            icon_svg=_TEXT_TOOL_ICON_SVG,
        )
        self.vertical_tool = TextTool(
            tool_id=TEXT_TOOL_VERTICAL_ID,
            display_name="Vertical Text",
            order=35,
            shortcut=None,
            shortcut_description=None,
            forced_vertical=True,
            icon_svg=_TEXT_TOOL_VERTICAL_ICON_SVG,
        )

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self.tool.activate(host_api)
        self.vertical_tool.activate(host_api)

    def deactivate(self) -> None:
        self.vertical_tool.deactivate()
        self.tool.deactivate()
        super().deactivate()

    def get_tools(self) -> list[TextTool]:
        return [self.tool, self.vertical_tool]


def create_plugin() -> TextToolPlugin:
    return TextToolPlugin()
