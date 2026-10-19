from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from .cubic_hit_test import CubicHitTest
from .edit_anchor_selection import _AnchorSelection
from .edit_selection import _Selection
from .vector_width_editor import VectorWidthEditor

if TYPE_CHECKING:
    from PySide6.QtCore import QRect

from ...core.native_cmyk.vector_paints import without_paint
from ...host_api import StrokePoint as HostStrokePoint
from ..common.vector_hit_test_geometry import VectorHitTestGeometry
from ..sdk import PropertySpec, PropertyType, ToolDescriptor, ToolMetadata
from ..sdk.cursors import apply_hover_cursor, cursor_for_handle
from ..sdk.tool import ContextToolbarSpec
from ..sdk.transform import compute_pivot, compute_scale_factors
from .common import (
    VECTOR_IDS_META_KEY,
    Anchor,
    VectorCreateCommand,
    VectorObject,
    VectorPath,
    VectorReplaceCommand,
    VectorStyle,
    copy_vector_object,
    ensure_vector_element_ids,
    make_vector_object,
    normalize_rgb,
)
from .parametric_shapes import (
    apply_parametric_handle_drag,
    is_editable_parametric_shape,
    parametric_handles,
)

logger = logging.getLogger(__name__)

_add_anchor_cursor = None
_delete_anchor_cursor = None
_anchor_hover_cursor = None
_handle_hover_cursor = None
_move_cursor = None


def _get_add_anchor_cursor():
    """Return a cached '+' cursor for adding anchors."""
    global _add_anchor_cursor  # noqa: PLW0603
    if _add_anchor_cursor is not None:
        return _add_anchor_cursor
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtGui import QCursor, QPainter, QPen, QPixmap  # noqa: PLC0415

    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(Qt.GlobalColor.white, 3.0))
    cx, cy, arm = size // 2, size // 2, 5
    p.drawLine(cx - arm, cy, cx + arm, cy)
    p.drawLine(cx, cy - arm, cx, cy + arm)
    p.setPen(QPen(Qt.GlobalColor.black, 1.5))
    p.drawLine(cx - arm, cy, cx + arm, cy)
    p.drawLine(cx, cy - arm, cx, cy + arm)
    p.end()
    _add_anchor_cursor = QCursor(pixmap, cx, cy)
    return _add_anchor_cursor


def _get_delete_anchor_cursor():
    """Return a cached '-' cursor for deleting anchors."""
    global _delete_anchor_cursor  # noqa: PLW0603
    if _delete_anchor_cursor is not None:
        return _delete_anchor_cursor
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtGui import QCursor, QPainter, QPen, QPixmap  # noqa: PLC0415

    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(Qt.GlobalColor.white, 3.0))
    cx, cy, arm = size // 2, size // 2, 5
    p.drawLine(cx - arm, cy, cx + arm, cy)
    p.setPen(QPen(Qt.GlobalColor.black, 1.5))
    p.drawLine(cx - arm, cy, cx + arm, cy)
    p.end()
    _delete_anchor_cursor = QCursor(pixmap, cx, cy)
    return _delete_anchor_cursor


def _get_anchor_hover_cursor():
    """Return a cached circle-with-dot cursor for anchor hover."""
    global _anchor_hover_cursor  # noqa: PLW0603
    if _anchor_hover_cursor is not None:
        return _anchor_hover_cursor
    from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: PLC0415
    from PySide6.QtGui import QBrush, QCursor, QPainter, QPen, QPixmap  # noqa: PLC0415

    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy, r = size / 2.0, size / 2.0, 5.0
    # Outer circle — white outline then black
    p.setPen(QPen(Qt.GlobalColor.white, 3.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    p.setPen(QPen(Qt.GlobalColor.black, 1.5))
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    # Center dot
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(Qt.GlobalColor.black))
    p.drawEllipse(QPointF(cx, cy), 1.5, 1.5)
    p.end()
    _anchor_hover_cursor = QCursor(pixmap, int(cx), int(cy))
    return _anchor_hover_cursor


def _get_handle_hover_cursor():
    """Return a cached filled-circle cursor for handle hover."""
    global _handle_hover_cursor  # noqa: PLW0603
    if _handle_hover_cursor is not None:
        return _handle_hover_cursor
    from PySide6.QtCore import QRectF, Qt  # noqa: PLC0415
    from PySide6.QtGui import QBrush, QCursor, QPainter, QPen, QPixmap  # noqa: PLC0415

    size = 24
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy, r = size / 2.0, size / 2.0, 4.0
    p.setPen(QPen(Qt.GlobalColor.white, 2.0))
    p.setBrush(QBrush(Qt.GlobalColor.black))
    p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
    p.end()
    _handle_hover_cursor = QCursor(pixmap, int(cx), int(cy))
    return _handle_hover_cursor


def _get_move_cursor():
    """Return a cached move cursor for fill-drag."""
    global _move_cursor  # noqa: PLW0603
    if _move_cursor is not None:
        return _move_cursor
    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtGui import QCursor  # noqa: PLC0415

    _move_cursor = QCursor(Qt.CursorShape.SizeAllCursor)
    return _move_cursor


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("VectorPathEditTool", text, use_fallback=True)


class VectorPathEditTool:
    """Anchor/handle edit tool for vector paths."""

    def __init__(self) -> None:
        self.host_api = None
        self._width_editor = VectorWidthEditor(self)
        self._selected: _Selection | None = None
        self._hover_cursor_set: bool = False
        self._editing_object = None
        self._pending_commands: list[VectorReplaceCommand] = []
        self._dragging = False
        self._transform_mode: str | None = None  # "move" | "scale" | "rotate"
        self._transform_handle: str | None = None
        self._transform_bbox: tuple[float, float, float, float] | None = None
        self._transform_pivot: tuple[float, float] | None = None
        self._transform_press_vec: tuple[float, float] | None = None
        self._transform_press_point: tuple[float, float] | None = None
        self._transform_start_object = None
        self._transform_preview_object = None
        self._transform_duplicate = False
        self._transform_duplicate_layer_id: str | None = None
        self._transform_duplicate_insert_index: int | None = None
        self._move_snap_enabled = True  # legacy; now read from settings
        self._move_grid_size = 10.0  # legacy; now read from settings
        self._transform_shift_locked = False
        self._transform_snap_override = False
        self._selected_object_cache = None
        self._selected_anchors: list[_AnchorSelection] = []
        self._anchor_box_start: tuple[float, float] | None = None
        self._anchor_box_auto_return_tool: str | None = None
        self._shape_handle_drag_id: str | None = None
        self._auto_return_tool_id: str | None = None
        self._auto_return_origin_id: str | None = None
        self._alt_pressed: bool = False
        self._last_hover_pt: HostStrokePoint | None = None
        self._style_settings = {
            "stroke_color": (0, 0, 0),
            "stroke_width": 2.0,
            "fill_enabled": False,
            "fill_color": None,
        }

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="vector_path_edit_tool",
            display_name=_ts("Path Edit"),
            category="vector",
            cursor="crosshair",
            icon_svg="icons/vector_path_edit.svg",
            order=33,
        )

    def tool_metadata(self) -> ToolMetadata:
        d = self.descriptor()
        return ToolMetadata(
            tool_id=d.id,
            display_name=d.display_name,
            category=d.category or "vector",
            group_id="vector.tools",
            group_label=_ts("Vector"),
            properties=self._property_specs(),
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="pointer_up",
            property_keys=[
                "edit_width_on_canvas",
                "stroke_color",
                "stroke_width",
                "fill_enabled",
                "fill_color",
            ],
            custom_widgets=[
                *self._path_correction_actions().widgets(),
                *self._width_actions().widgets(),
            ],
            anchor_mode="custom",
            mode="settings",
        )

    def _path_correction_actions(self):
        # CT-15: Simplify / Smooth / Straighten / Offset live in a delegate.
        actions = getattr(self, "_correction_actions", None)
        if actions is None:
            from .path_correction_actions import PathCorrectionActions  # noqa: PLC0415

            actions = PathCorrectionActions(self)
            self._correction_actions = actions
        return actions

    def _width_actions(self):
        actions = getattr(self, "_vector_width_actions", None)
        if actions is None:
            from .vector_width_actions import VectorWidthActions  # noqa: PLC0415

            actions = VectorWidthActions(self)
            self._vector_width_actions = actions
        return actions

    def get_context_anchor_rect(self) -> QRect | None:
        """Return the bounding box of the selected vector object.

        ``_compute_bbox`` returns ``(min_x, min_y, max_x, max_y)`` (tight
        bezier extrema bbox).  Convert to ``QRect(x, y, w, h)``; the previous
        mis-unpacking treated ``(max_x, max_y)`` as ``(w, h)`` which pushed
        the toolbar to the bottom-right of the visible object.
        """
        from PySide6.QtCore import QRect as _QRect  # noqa: PLC0415

        obj = self._current_selection_object()
        if obj is None:
            return None
        bbox = self._compute_bbox(obj)
        if bbox is None:
            return None
        min_x, min_y, max_x, max_y = bbox
        w = int(max_x - min_x)
        h = int(max_y - min_y)
        if w <= 0 or h <= 0:
            return None
        return _QRect(int(min_x), int(min_y), w, h)

    def activate(self, host) -> None:
        self.host_api = host

    def enable(self) -> None:
        self._reset()
        self._hydrate_selection_from_canvas()

    def disable(self) -> None:
        self._clear_vector_selection_on_canvas()
        self._reset()
        self._alt_pressed = False
        self._last_hover_pt = None
        if self._hover_cursor_set:
            self._hover_cursor_set = apply_hover_cursor(None, is_set=self._hover_cursor_set)

    def deactivate(self) -> None:
        self.disable()
        self.host_api = None

    def on_pointer_down(  # noqa: PLR0911, PLR0912
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del meta
        if self._width_editor.enabled:
            self._width_editor.down(pt, alt=alt)
            return
        self._dragging = False
        prev_vec_id = self._selected.vec_id if self._selected else None
        self._hydrate_selection_from_canvas()
        self._shape_handle_drag_id = None

        # Editing actions (drag, insert, delete, transform) are only
        # allowed when the target object was already selected before
        # this click.  For auto-switch from another tool the object is
        # hydrated during enable(), so prev_vec_id is already set.
        editing_ok = (
            prev_vec_id is not None
            and self._selected is not None
            and self._selected.vec_id == prev_vec_id
        )

        if editing_ok:
            # Shift+transform handle → uniform scale (anchor ops より優先)
            if shift and self._maybe_begin_transform(pt, alt=alt, shift=shift, ctrl=ctrl):
                return
            # Shift+クリックでアンカー追加/削除
            if shift and self._try_ctrl_anchor_ops(pt):
                return
            # Alt+click on anchor of selected object → delete anchor
            if alt and self._selected:
                px, py = float(pt.pos[0]), float(pt.pos[1])
                hit = self._hover_hit_selected_anchor(px, py)
                if hit is not None:
                    self._delete_anchor_on_selected(hit[0], hit[1])
                    return
            if self._hit_test_parametric_handle(pt):
                self._dragging = True
                return

        prefer_anchor = alt or shift
        prefer_handle = ctrl
        if self._hit_test_anchor(pt, prefer_anchor=prefer_anchor, prefer_handle=prefer_handle):
            # Start drag only if the anchor belongs to the already-selected object
            if editing_ok and self._selected and self._selected.vec_id == prev_vec_id:
                self._dragging = True
            return

        if editing_ok and not alt and not shift and not ctrl and self._selected:
            px, py = float(pt.pos[0]), float(pt.pos[1])
            hit = self._hover_hit_selected_segment(px, py)
            if hit is not None:
                self._insert_anchor_on_selected(hit[0], hit[1], hit[2])
                self._dragging = True
                return
            # Drag on fill of selected object → select that subpath and move transform.
            fill_hit = self._hit_selected_fill_path(px, py)
            if fill_hit is not None:
                self._select_path_on_selected_object(fill_hit[0], fill_hit[1])
                self._begin_transform("center", "move", pt, alt=alt)
                if self._transform_mode:
                    self._dragging = True
                return

        if self._hit_test_path_segment(pt):
            return
        if self._hit_test_fill(pt):
            # Immediately enable move-drag for newly selected object
            self._begin_transform("center", "move", pt, alt=alt)
            if self._transform_mode:
                self._dragging = True
            return
        self._flush_pending_commands()
        if editing_ok and self._maybe_begin_transform(pt, alt=alt, shift=shift, ctrl=ctrl):
            return
        if editing_ok and self._selected and not alt and not shift and not ctrl:
            self._anchor_box_start = (float(pt.pos[0]), float(pt.pos[1]))
            self._anchor_box_auto_return_tool = (
                self._auto_return_tool_id
                if self._auto_return_tool_id and self._auto_return_origin_id
                else None
            )
            return
        # Nothing was hit — deselect and return to origin tool if applicable
        auto_return_tool = (
            self._auto_return_tool_id
            if self._auto_return_tool_id and self._auto_return_origin_id
            else None
        )
        self._clear_vector_selection_on_canvas()
        self._reset(keep_style_settings=True)
        if auto_return_tool:
            self._activate_tool(auto_return_tool)
        return

    def on_pointer_move(self, pts: Iterable[HostStrokePoint]) -> None:
        last = self._last_point(pts)
        if last is None:
            return
        if self._width_editor.enabled:
            self._width_editor.move(last)
            return
        if self._anchor_box_start is not None:
            self._last_hover_pt = last
            self._update_anchor_box_preview(last)
            return
        if self._transform_mode:
            self._update_transform_drag(last, preview_only=True)
            return
        if self._shape_handle_drag_id:
            self._move_parametric_handle(last, preview_only=True)
            return
        if not self._dragging or not self._selected:
            return
        self._move_selected_anchor(last, preview_only=True)

    @staticmethod
    def _last_point(pts: Iterable[HostStrokePoint]) -> HostStrokePoint | None:
        last = None
        for point in pts:
            last = point
        return last

    def on_pointer_up(self, pt: HostStrokePoint) -> None:
        if self._width_editor.enabled:
            self._width_editor.up(pt)
            return
        if self._transform_mode:
            self._update_transform_drag(pt, preview_only=False)
            self._commit_transform()
            self._transform_mode = None
            self._dragging = False
            return
        if self._shape_handle_drag_id:
            self._dragging = False
            self._move_parametric_handle(pt, preview_only=False)
            self._shape_handle_drag_id = None
            self._commit_editing_object()
            return
        if self._anchor_box_start is not None:
            self._finish_anchor_box_select(pt)
            return
        if not self._dragging or not self._selected:
            return
        self._dragging = False
        self._move_selected_anchor(pt, preview_only=False)
        self._commit_editing_object()

    def on_pointer_hover(self, pt: HostStrokePoint) -> None:
        if self._width_editor.enabled:
            self._width_editor.hover()
            return
        self._last_hover_pt = pt
        if not self._selected:
            self._hydrate_selection_from_canvas()
        cursor = None
        if self._selected:
            px, py = float(pt.pos[0]), float(pt.pos[1])
            anchor_hit = self._hover_hit_selected_anchor(px, py)
            handle_hit = self._hover_hit_selected_handle(px, py)
            on_anchor = anchor_hit is not None and (
                self._alt_pressed or handle_hit is None or anchor_hit[2] <= handle_hit[3]
            )
            on_handle = not on_anchor and handle_hit is not None
            if on_anchor and self._alt_pressed:
                cursor = _get_delete_anchor_cursor()
            elif on_anchor:
                cursor = _get_anchor_hover_cursor()
            elif on_handle:
                cursor = _get_handle_hover_cursor()
            elif not self._alt_pressed and self._hover_hit_selected_segment(px, py) is not None:
                cursor = _get_add_anchor_cursor()
            elif self._hover_hit_selected_fill(px, py):
                cursor = _get_move_cursor()
        if cursor is None:
            handle_result = self._hit_test_transform_handle(pt)
            if handle_result is not None:
                cursor = cursor_for_handle(*handle_result)
        self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:
        if self._width_editor.enabled:
            if pressed and key.lower() in ("esc", "escape"):
                self._width_editor.cancel()
                return True
            return False
        return self._on_path_key(key, pressed)

    def _on_path_key(self, key: str, pressed: bool) -> bool:
        key_lower = key.lower()
        if key_lower in ("alt", "alt_l", "alt_r"):
            self._alt_pressed = pressed
            if self._last_hover_pt is not None:
                self.on_pointer_hover(self._last_hover_pt)
            return False
        if not pressed:
            return False
        if key_lower in ("esc", "escape"):
            self._clear_canvas_preview()
            self._reset()
            return True
        if key_lower in ("enter", "return"):
            return self._toggle_selected_path_closed()
        if key_lower in ("c", "b", "s", "m"):
            mode_by_key = {
                "c": "corner",
                "b": "break",
                "s": "smooth",
                "m": "symmetric",
            }
            return self._convert_selected_anchor_handle_mode(mode_by_key[key_lower])
        return False

    def on_wheel(self, delta: float, pt: HostStrokePoint) -> bool:
        del delta, pt
        return False

    def build_commands(self) -> list[VectorReplaceCommand]:
        pending = list(self._pending_commands)
        self._pending_commands.clear()
        return pending

    def _property_specs(self) -> list[PropertySpec]:
        return [
            PropertySpec(
                key="edit_width_on_canvas",
                display_name=_ts("Edit Width on Canvas"),
                type=PropertyType.BOOL,
                default_value=self._width_editor.enabled,
            ),
            PropertySpec(
                key="stroke_color",
                display_name=_ts("Stroke Color"),
                type=PropertyType.COLOR,
                default_value=self._style_settings.get("stroke_color"),
                is_common=True,
            ),
            PropertySpec(
                key="stroke_width",
                display_name=_ts("Stroke Width"),
                type=PropertyType.FLOAT,
                min_value=0.1,
                max_value=128.0,
                default_value=self._style_settings.get("stroke_width", 2.0),
                step=0.5,
                is_common=True,
            ),
            PropertySpec(
                key="fill_enabled",
                display_name=_ts("Fill Enabled"),
                type=PropertyType.BOOL,
                default_value=self._style_settings.get("fill_enabled"),
                is_common=True,
            ),
            PropertySpec(
                key="fill_color",
                display_name=_ts("Fill Color"),
                type=PropertyType.COLOR,
                default_value=self._style_settings.get("fill_color"),
                is_common=True,
            ),
        ]

    def get_settings(self) -> dict[str, object]:
        return {**self._style_settings, "edit_width_on_canvas": self._width_editor.enabled}

    def set_settings(self, settings: dict[str, object]) -> None:
        settings = dict(settings or {})
        if "edit_width_on_canvas" in settings:
            self._width_editor.set_enabled(bool(settings.pop("edit_width_on_canvas")))
        self._hydrate_selection_from_canvas()
        merged: dict[str, object] = dict(self._style_settings)
        merged.update(settings or {})
        stroke = normalize_rgb(merged.get("stroke_color")) or (0, 0, 0)
        merged["stroke_color"] = stroke
        merged["fill_color"] = normalize_rgb(merged.get("fill_color"))
        if bool(merged.get("fill_enabled")) and merged.get("fill_color") is None:
            merged["fill_color"] = (0, 0, 0)
        if merged.get("fill_color") is None:
            merged["fill_enabled"] = False
        if merged == self._style_settings:
            return
        clear_roles = tuple(
            role
            for role in ("stroke", "fill")
            if merged.get(f"{role}_color") != self._style_settings.get(f"{role}_color")
        )
        self._style_settings = merged
        self._apply_style_settings_to_selection(clear_roles=clear_roles)

    def set_setting(self, key: str, value: object) -> None:
        if key == "edit_width_on_canvas":
            self._width_editor.set_enabled(bool(value))
            return
        self._set_style_setting(key, value)

    def _set_style_setting(self, key: str, value: object) -> None:
        if key not in self._style_settings:
            return
        self._hydrate_selection_from_canvas()
        before = dict(self._style_settings)
        if key == "stroke_color":
            normalized = normalize_rgb(value)
            if normalized is not None:
                self._style_settings["stroke_color"] = normalized
        elif key == "fill_color":
            normalized = normalize_rgb(value)
            self._style_settings["fill_color"] = normalized
            if normalized is None:
                self._style_settings["fill_enabled"] = False
            else:
                self._style_settings["fill_enabled"] = True
        elif key == "fill_enabled":
            self._style_settings["fill_enabled"] = bool(value)
            if (
                self._style_settings["fill_enabled"]
                and self._style_settings.get("fill_color") is None
            ):
                self._style_settings["fill_color"] = (0, 0, 0)
        elif key == "stroke_width":
            try:
                self._style_settings["stroke_width"] = max(0.1, min(128.0, float(value)))
            except (TypeError, ValueError):
                return
        else:
            self._style_settings[key] = value
        if self._style_settings == before:
            return
        self._apply_style_settings_to_selection(
            clear_roles=(key.removesuffix("_color"),) if key.endswith("_color") else ()
        )

    def _apply_style_settings_to_selection(self, *, clear_roles: tuple[str, ...] = ()) -> None:
        if not self._selected:
            self._hydrate_selection_from_canvas()
        if not self._selected:
            return
        obj = self._current_selection_object()
        if obj is None:
            return
        style = getattr(obj, "style", None)
        stroke_width = float(self._style_settings.get("stroke_width") or 2.0)
        style_updated = VectorStyle(
            stroke_color=tuple(self._style_settings.get("stroke_color") or (0, 0, 0)),
            stroke_width=stroke_width,
            fill_enabled=bool(
                self._style_settings.get("fill_enabled")
                and self._style_settings.get("fill_color") is not None
            ),
            fill_color=self._style_settings.get("fill_color"),
            join=getattr(style, "join", "miter") if style else "miter",
            cap=getattr(style, "cap", "butt") if style else "butt",
            dash=getattr(style, "dash", ()),
            miter_limit=getattr(style, "miter_limit", 4.0) if style else 4.0,
        )
        meta = dict(getattr(obj, "meta", {}) or {})
        for role in clear_roles:
            meta = without_paint(meta, role)
        new_obj = type(obj)(
            id=getattr(obj, "id", None),
            paths=list(getattr(obj, "paths", []) or []),
            style=style_updated,
            meta=meta,
            preview=getattr(obj, "preview", False),
        )
        cmd = VectorReplaceCommand(self._selected.vec_id, new_obj, layer_id=self._selected.layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None) if self.host_api else False
        except Exception:
            success = False
        if not success:
            self._pending_commands.append(cmd)
        self._editing_object = copy_vector_object(new_obj)
        self._update_canvas_selection(
            self._selected.vec_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            self._selected.layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
            mode=None,
        )

    def _update_style_settings_from_object(self, obj: object) -> None:
        style = getattr(obj, "style", None)
        if not style:
            return
        stroke = normalize_rgb(getattr(style, "stroke_color", None)) or (0, 0, 0)
        fill_enabled = bool(getattr(style, "fill_enabled", False))
        fill_color = normalize_rgb(getattr(style, "fill_color", None))
        if fill_enabled and fill_color is None:
            fill_color = (0, 0, 0)
        self._style_settings["stroke_color"] = stroke
        self._style_settings["stroke_width"] = float(getattr(style, "stroke_width", 2.0))
        self._style_settings["fill_color"] = fill_color
        self._style_settings["fill_enabled"] = bool(fill_enabled and fill_color is not None)

    def _sync_style_settings_to_tool_properties(self) -> None:
        host = self.host_api
        if not host:
            return
        tool_id = self.descriptor().id
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        sync_tool_setting = (
            getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        )
        if callable(sync_tool_setting):
            for key in ("stroke_color", "stroke_width", "fill_enabled", "fill_color"):
                try:
                    sync_tool_setting(tool_id, key, self._style_settings.get(key))
                except Exception:
                    logger.debug("VectorPathEditTool sync_tool_setting failed", exc_info=True)
            return
        set_tool_setting = getattr(tool_manager, "set_tool_setting", None) if tool_manager else None
        if callable(set_tool_setting):
            for key in ("stroke_color", "stroke_width", "fill_enabled", "fill_color"):
                try:
                    set_tool_setting(tool_id, key, self._style_settings.get(key))
                except Exception:
                    logger.debug("VectorPathEditTool set_tool_setting failed", exc_info=True)
            return
        events = getattr(host, "events", None)
        publish = getattr(events, "publish", None) if events else None
        if not callable(publish):
            return
        for key in ("stroke_color", "fill_enabled", "fill_color"):
            try:
                publish(
                    "tool.settings_changed",
                    {"tool_id": tool_id, "key": key, "value": self._style_settings.get(key)},
                )
            except Exception:
                logger.debug(
                    "VectorPathEditTool tool.settings_changed publish failed",
                    exc_info=True,
                )

    def _maybe_auto_return_to_origin_move_tool(self) -> bool:
        if not self._selected or not self._auto_return_tool_id or not self._auto_return_origin_id:
            return False
        if self._selected.vec_id == self._auto_return_origin_id:
            return False
        tool_id = self._auto_return_tool_id
        activated = self._activate_tool(tool_id)
        if activated:
            self._strip_auto_return_metadata_from_canvas_selection()
            self._auto_return_tool_id = None
            self._auto_return_origin_id = None
        return activated

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

    def _strip_auto_return_metadata_from_canvas_selection(self) -> None:
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return
        if "auto_return_tool" not in selection and "auto_return_origin_id" not in selection:
            return
        stripped = dict(selection)
        stripped.pop("auto_return_tool", None)
        stripped.pop("auto_return_origin_id", None)
        try:
            canvas._vector_selection = stripped  # type: ignore[attr-defined]  # duck-typed canvas attr
        except Exception:
            return

    # --- Selection / editing helpers -----------------------------------
    def _vector_store(self) -> list[tuple[str | None, object]]:
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas:
            return []
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                logger.debug("get_vector_objects failed")
        store = getattr(canvas, "_vector_objects", None)
        if store is None:
            store = []
            try:
                canvas._vector_objects = store  # type: ignore[attr-defined]  # duck-typed canvas attr
            except Exception:
                logger.debug("Vector store init failed")
                return []
        return [(None, obj) for obj in store]

    def _hit_test_parametric_handle(self, pt: HostStrokePoint) -> bool:
        if not self._selected:
            return False
        obj = self._current_selection_object()
        if obj is None or not is_editable_parametric_shape(obj):
            return False
        handles = parametric_handles(obj)
        if not handles:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        threshold_sq = (
            VectorHitTestGeometry(self.host_api).canvas_radius(VectorHitTestGeometry.SHAPE_RADIUS)
            ** 2
        )
        best_handle_id: str | None = None
        best_dist2: float = float("inf")
        for handle in handles:
            dx = float(handle.pos[0]) - px
            dy = float(handle.pos[1]) - py
            dist2 = dx * dx + dy * dy
            if dist2 <= threshold_sq and dist2 < best_dist2:
                best_dist2 = dist2
                best_handle_id = handle.handle_id
        if best_handle_id is None:
            return False
        self._editing_object = copy_vector_object(obj)
        self._shape_handle_drag_id = best_handle_id
        sel = self._selected
        self._update_canvas_selection(
            sel.vec_id,
            sel.path_idx,
            sel.anchor_idx,
            sel.layer_id,
            preview=True,
            transform_bounds=self._compute_bbox(self._editing_object),
            mode="shape",
        )
        return True

    def _move_parametric_handle(self, pt: HostStrokePoint, *, preview_only: bool) -> None:
        if not self._editing_object or not self._selected or not self._shape_handle_drag_id:
            return
        updated = apply_parametric_handle_drag(
            self._editing_object,
            self._shape_handle_drag_id,
            (float(pt.pos[0]), float(pt.pos[1])),
        )
        if updated is None:
            return
        self._editing_object = updated
        sel = self._selected
        self._update_canvas_selection(
            sel.vec_id,
            sel.path_idx,
            sel.anchor_idx,
            sel.layer_id,
            preview=preview_only,
            transform_bounds=self._compute_bbox(self._editing_object),
            mode="shape",
        )
        if preview_only and self.host_api and getattr(self.host_api, "canvas", None):
            try:
                canvas = self.host_api.canvas
                canvas._vector_preview = self._editing_object  # type: ignore[attr-defined]  # duck-typed canvas attr
                canvas._vector_preview_layer_id = sel.layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(canvas, "notify_vector_preview_changed"):
                    canvas.notify_vector_preview_changed()
                elif hasattr(canvas, "_recompose"):
                    canvas._recompose()
            except Exception:
                logger.debug("Parametric handle preview update failed", exc_info=True)

    def _hit_test_anchor(
        self, pt: HostStrokePoint, *, prefer_anchor: bool = False, prefer_handle: bool = False
    ) -> bool:
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        best_anchor: tuple[str, int, int, float] | None = None  # (id, path, anchor, dist2)
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius() ** 2
        best_handle: tuple[str, int, int, float, str] | None = (
            None  # (id, path, anchor, dist2, kind)
        )

        for _layer_id, vec in store:
            vec_obj = vec[1] if isinstance(vec, tuple) else vec
            if is_editable_parametric_shape(vec_obj):
                continue
            vec_id = getattr(vec_obj, "id", None)
            paths = getattr(vec_obj, "paths", None) or []
            for pi, path in enumerate(paths):
                anchors = getattr(path, "anchors", None) or []
                for ai, anchor in enumerate(anchors):
                    # Anchor candidate
                    dx = anchor.pos[0] - px
                    dy = anchor.pos[1] - py
                    dist2 = dx * dx + dy * dy
                    if dist2 <= threshold_sq and (best_anchor is None or dist2 < best_anchor[3]):
                        best_anchor = (vec_id, pi, ai, dist2)

                    # Handle candidates
                    for handle_kind, vec_handle in (("in", anchor.h_in), ("out", anchor.h_out)):
                        if not vec_handle:
                            continue
                        hx = anchor.pos[0] + vec_handle[0]
                        hy = anchor.pos[1] + vec_handle[1]
                        dxh = hx - px
                        dyh = hy - py
                        dist2h = dxh * dxh + dyh * dyh
                        if dist2h <= threshold_sq and (
                            best_handle is None or dist2h < best_handle[3]
                        ):
                            best_handle = (vec_id, pi, ai, dist2h, handle_kind)

        # Resolve selection per preference
        def _select(
            target: tuple[str, int, int], handle: str | None, layer_hint: str | None
        ) -> bool:
            layer_id: str | None = None
            target_ref = _AnchorSelection(target[1], target[2])
            preserve_anchor_refs = (
                handle is None
                and self._selected is not None
                and self._selected.vec_id == target[0]
                and target_ref in self._selected_anchors
            )
            anchor_refs = list(self._selected_anchors) if preserve_anchor_refs else [target_ref]
            for candidate_layer_id, candidate in store:
                obj = candidate[1] if isinstance(candidate, tuple) else candidate
                if getattr(obj, "id", None) == target[0]:
                    layer_id = candidate_layer_id
                    self._editing_object = copy_vector_object(obj)
                    self._update_style_settings_from_object(obj)
                    self._sync_style_settings_to_tool_properties()
                    break
            if self._editing_object:
                self._editing_object.paths[target[1]] = self._ensure_handles(
                    self._editing_object.paths[target[1]], target[2]
                )
            self._selected = _Selection(target[0], target[1], target[2], layer_id, handle)
            self._selected_anchors = anchor_refs
            self._update_canvas_selection(
                target[0],
                target[1],
                target[2],
                layer_id,
                preview=True,
                transform_bounds=self._compute_bbox(self._editing_object),
            )
            return True

        # Ctrl: handle strictly preferred if available
        if prefer_handle and best_handle:
            return _select((best_handle[0], best_handle[1], best_handle[2]), best_handle[4], None)

        # Prefer the closest control; anchors win ties and explicit anchor operations.
        if best_anchor and (
            prefer_anchor or best_handle is None or best_anchor[3] <= best_handle[3]
        ):
            return _select((best_anchor[0], best_anchor[1], best_anchor[2]), None, None)
        if best_handle:
            return _select((best_handle[0], best_handle[1], best_handle[2]), best_handle[4], None)
        return False

    def _hit_test_path_segment(  # noqa: PLR0912
        self, pt: HostStrokePoint, *, threshold: float = 3.0
    ) -> bool:
        """Select a vector object by clicking near a path segment (not only anchors/handles)."""
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius(threshold) ** 2
        best: tuple[str, str | None, int, int, float] | None = None
        # (id, layer_id, path, anchor, dist2)

        for layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            vec_id = getattr(obj, "id", None)
            if not vec_id:
                continue
            paths = getattr(obj, "paths", None) or []
            for pi, path in enumerate(paths):
                anchors = getattr(path, "anchors", None) or []
                if len(anchors) < 2:
                    continue
                count = len(anchors)
                seg_count = count if getattr(path, "closed", False) else (count - 1)
                for i in range(seg_count):
                    a = anchors[i]
                    b = anchors[(i + 1) % count]
                    dist2, t = self._bezier_segment_distance_sq(
                        px, py, a, b, max_distance=math.sqrt(threshold_sq)
                    )
                    if dist2 > threshold_sq:
                        continue
                    anchor_idx = i if t <= 0.5 else ((i + 1) % count)
                    if best is None or dist2 < best[4]:
                        best = (vec_id, layer_id, pi, anchor_idx, dist2)

        if best is None:
            return False
        vec_id, layer_id, path_idx, anchor_idx, _ = best
        return self._select_object_by_id(store, vec_id, layer_id, path_idx, anchor_idx)

    def _move_selected_anchor(self, pt: HostStrokePoint, *, preview_only: bool) -> None:
        if not self._editing_object or not self._selected:
            return
        sel = self._selected
        if sel.path_idx >= len(self._editing_object.paths):
            return
        path = self._editing_object.paths[sel.path_idx]
        if sel.anchor_idx >= len(path.anchors):
            return
        anchors = list(path.anchors)
        anchor = anchors[sel.anchor_idx]

        selected_refs = self._selected_anchor_refs_for_move(sel)
        if selected_refs:
            dx = float(pt.pos[0]) - float(anchor.pos[0])
            dy = float(pt.pos[1]) - float(anchor.pos[1])
            self._move_anchor_refs_by_delta(selected_refs, dx, dy)
        else:
            if sel.handle == "in" and anchor.h_in is not None:
                vec = (float(pt.pos[0]) - anchor.pos[0], float(pt.pos[1]) - anchor.pos[1])
                anchors[sel.anchor_idx] = Anchor(
                    pos=anchor.pos, kind="smooth", h_in=vec, h_out=anchor.h_out
                )
            elif sel.handle == "out" and anchor.h_out is not None:
                vec = (float(pt.pos[0]) - anchor.pos[0], float(pt.pos[1]) - anchor.pos[1])
                anchors[sel.anchor_idx] = Anchor(
                    pos=anchor.pos, kind="smooth", h_in=anchor.h_in, h_out=vec
                )
            elif not sel.handle:
                anchors[sel.anchor_idx] = Anchor(
                    pos=(float(pt.pos[0]), float(pt.pos[1])),
                    kind=anchor.kind,
                    h_in=anchor.h_in,
                    h_out=anchor.h_out,
                )
            self._editing_object.paths[sel.path_idx] = VectorPath(
                anchors=anchors,
                closed=path.closed,
            )

        if preview_only and self.host_api and getattr(self.host_api, "canvas", None):
            try:
                canvas = self.host_api.canvas
                # Invalidate QPainterPath cache — object is mutated in-place
                qpath_cache = getattr(canvas, "_qpath_cache", None)
                if isinstance(qpath_cache, dict):
                    qpath_cache.pop(id(self._editing_object), None)
                canvas._vector_preview = self._editing_object  # type: ignore[attr-defined]  # duck-typed canvas attr
                canvas._vector_preview_layer_id = sel.layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
            except Exception:
                logger.debug("VectorPathEditTool preview update failed", exc_info=True)
        # Single _recompose via _update_canvas_selection (preview is already set)
        self._update_canvas_selection(
            sel.vec_id,
            sel.path_idx,
            sel.anchor_idx,
            sel.layer_id,
            preview=preview_only,
            transform_bounds=self._compute_bbox(self._editing_object),
        )

    def _selected_anchor_refs_for_move(self, sel: _Selection) -> list[_AnchorSelection]:
        if sel.handle is not None or len(self._selected_anchors) <= 1:
            return []
        primary = _AnchorSelection(sel.path_idx, sel.anchor_idx)
        if primary not in self._selected_anchors:
            return []
        refs: list[_AnchorSelection] = []
        for ref in self._selected_anchors:
            if self._anchor_ref_is_valid(self._editing_object, ref):
                refs.append(ref)
        return refs if len(refs) > 1 else []

    def _move_anchor_refs_by_delta(
        self,
        refs: list[_AnchorSelection],
        dx: float,
        dy: float,
    ) -> None:
        paths = list(getattr(self._editing_object, "paths", []) or [])
        refs_by_path: dict[int, set[int]] = {}
        for ref in refs:
            refs_by_path.setdefault(ref.path_idx, set()).add(ref.anchor_idx)
        for path_idx, anchor_indices in refs_by_path.items():
            if path_idx < 0 or path_idx >= len(paths):
                continue
            path = paths[path_idx]
            anchors = list(getattr(path, "anchors", []) or [])
            for anchor_idx in sorted(anchor_indices):
                if anchor_idx < 0 or anchor_idx >= len(anchors):
                    continue
                anchor = anchors[anchor_idx]
                anchors[anchor_idx] = Anchor(
                    pos=(float(anchor.pos[0]) + dx, float(anchor.pos[1]) + dy),
                    kind=anchor.kind,
                    h_in=anchor.h_in,
                    h_out=anchor.h_out,
                )
            paths[path_idx] = VectorPath(anchors=anchors, closed=path.closed)
        self._editing_object.paths[:] = paths

    def _finish_anchor_box_select(self, pt: HostStrokePoint) -> None:
        start = self._anchor_box_start
        auto_return_tool = self._anchor_box_auto_return_tool
        self._anchor_box_start = None
        self._anchor_box_auto_return_tool = None
        self._clear_anchor_box_preview()
        if start is None or not self._selected:
            return
        end = (float(pt.pos[0]), float(pt.pos[1]))
        if self._distance_sq(start, end) < 9.0:
            self._clear_vector_selection_on_canvas()
            self._reset(keep_style_settings=True)
            if auto_return_tool:
                self._activate_tool(auto_return_tool)
            return
        refs = self._anchor_refs_in_rect(start, end)
        if not refs:
            return
        primary = refs[0]
        obj = self._current_selection_object()
        self._selected_anchors = refs
        self._selected = _Selection(
            self._selected.vec_id,
            primary.path_idx,
            primary.anchor_idx,
            self._selected.layer_id,
            None,
        )
        if obj is not None:
            self._editing_object = copy_vector_object(obj)
        self._update_canvas_selection(
            self._selected.vec_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            self._selected.layer_id,
            preview=True,
            transform_bounds=self._compute_bbox(self._editing_object or obj),
            mode="anchor",
        )

    def _anchor_refs_in_rect(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> list[_AnchorSelection]:
        obj = self._current_selection_object()
        if obj is None or is_editable_parametric_shape(obj):
            return []
        x0 = min(start[0], end[0])
        y0 = min(start[1], end[1])
        x1 = max(start[0], end[0])
        y1 = max(start[1], end[1])
        refs: list[_AnchorSelection] = []
        for path_idx, path in enumerate(getattr(obj, "paths", None) or []):
            for anchor_idx, anchor in enumerate(getattr(path, "anchors", None) or []):
                ax = float(anchor.pos[0])
                ay = float(anchor.pos[1])
                if x0 <= ax <= x1 and y0 <= ay <= y1:
                    refs.append(_AnchorSelection(path_idx, anchor_idx))
        return refs

    @staticmethod
    def _distance_sq(
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        return dx * dx + dy * dy

    def _update_anchor_box_preview(self, pt: HostStrokePoint) -> None:
        start = self._anchor_box_start
        if start is None:
            return
        canvas = getattr(self.host_api, "canvas", None)
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if not isinstance(selection, dict):
            return
        end = (float(pt.pos[0]), float(pt.pos[1]))
        selection["anchor_box_bounds"] = (
            min(start[0], end[0]),
            min(start[1], end[1]),
            max(start[0], end[0]),
            max(start[1], end[1]),
        )
        self._notify_vector_overlay_changed(canvas)

    def _clear_anchor_box_preview(self) -> None:
        canvas = getattr(self.host_api, "canvas", None)
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if not isinstance(selection, dict):
            return
        if selection.pop("anchor_box_bounds", None) is not None:
            self._notify_vector_overlay_changed(canvas)

    @staticmethod
    def _notify_vector_overlay_changed(canvas: object) -> None:
        try:
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            logger.debug("VectorPathEditTool vector overlay update failed", exc_info=True)

    def _commit_editing_object(self) -> None:
        if not self._editing_object or not self._selected or not self.host_api:
            return
        if not getattr(self.host_api, "canvas", None):
            return
        target_id, _, _, layer_id, _ = (
            self._selected.vec_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            self._selected.layer_id,
            self._selected.handle,
        )
        cmd = VectorReplaceCommand(target_id, self._editing_object, layer_id=layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)
        self._update_canvas_selection(
            target_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(self._editing_object),
        )
        self._clear_canvas_preview()
        self._reset(keep_auto_return=True)

    def _update_canvas_selection(
        self,
        vec_id: str,
        path_idx: int,
        anchor_idx: int,
        layer_id: str | None,
        *,
        preview: bool = False,
        transform_bounds: tuple[float, float, float, float] | None = None,
        mode: str | None = None,
    ) -> None:
        if transform_bounds is None:
            transform_bounds = self._compute_bbox(self._current_selection_object())
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                canvas = self.host_api.canvas
                obj_for_ids = self._current_selection_object()
                selection_payload = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                    "id": vec_id,
                    "path": path_idx,
                    "anchor": anchor_idx,
                    "layer_id": layer_id,
                    "preview": preview,
                    "transform_bounds": transform_bounds,
                    "mode": mode or ("transform" if transform_bounds else None),
                    "style": dict(self._style_settings),
                }
                selection_payload.update(
                    self._selection_element_id_payload(obj_for_ids, path_idx, anchor_idx)
                )
                anchor_payload = self._selected_anchor_payload(obj_for_ids)
                if len(anchor_payload) > 1:
                    selection_payload["anchors"] = anchor_payload
                if self._auto_return_tool_id and self._auto_return_origin_id:
                    selection_payload["auto_return_tool"] = self._auto_return_tool_id
                    selection_payload["auto_return_origin_id"] = self._auto_return_origin_id
                shape_handles = self._shape_handle_payload(obj_for_ids)
                if shape_handles:
                    selection_payload["shape_handles"] = shape_handles
                canvas._vector_selection = selection_payload  # type: ignore[attr-defined]  # duck-typed canvas attr
                if transform_bounds is None:
                    transform_bounds = self._compute_bbox(self._current_selection_object())
                if transform_bounds:
                    cached = (
                        self._editing_object
                        if self._editing_object
                        and getattr(self._editing_object, "id", None) == vec_id
                        else self._current_selection_object()
                    )
                    self._selected_object_cache = cached
                if hasattr(canvas, "notify_vector_preview_changed"):
                    canvas.notify_vector_preview_changed()
                elif hasattr(canvas, "_recompose"):
                    canvas._recompose()
            except Exception:
                logger.debug("VectorPathEditTool selection update failed", exc_info=True)

    def _selected_anchor_payload(self, obj: object | None) -> list[dict[str, object]]:
        refs = self._normalized_selected_anchors(obj)
        payload: list[dict[str, object]] = []
        for ref in refs:
            entry: dict[str, object] = {
                "path": ref.path_idx,
                "anchor": ref.anchor_idx,
            }
            entry.update(self._selection_element_id_payload(obj, ref.path_idx, ref.anchor_idx))
            payload.append(entry)
        return payload

    def _normalized_selected_anchors(self, obj: object | None) -> list[_AnchorSelection]:
        refs = list(self._selected_anchors)
        if self._selected is not None:
            primary = _AnchorSelection(self._selected.path_idx, self._selected.anchor_idx)
            refs = [primary, *[ref for ref in refs if ref != primary]]
        deduped: list[_AnchorSelection] = []
        for ref in refs:
            if ref in deduped or not self._anchor_ref_is_valid(obj, ref):
                continue
            deduped.append(ref)
        return deduped

    def _anchor_ref_is_valid(self, obj: object | None, ref: _AnchorSelection) -> bool:
        paths = list(getattr(obj, "paths", None) or [])
        if ref.path_idx < 0 or ref.path_idx >= len(paths):
            return False
        anchors = list(getattr(paths[ref.path_idx], "anchors", None) or [])
        return 0 <= ref.anchor_idx < len(anchors)

    def _selection_element_id_payload(
        self,
        obj: object | None,
        path_idx: int,
        anchor_idx: int,
    ) -> dict[str, str]:
        payload: dict[str, str] = {}
        path_meta = self._path_id_entry(obj, path_idx)
        if path_meta is None:
            return payload
        path_id = path_meta.get("id")
        if isinstance(path_id, str) and path_id:
            payload["path_id"] = path_id
        anchors_meta = path_meta.get("anchors")
        anchor_meta = self._indexed_mapping(anchors_meta, anchor_idx)
        if anchor_meta is not None:
            anchor_id = anchor_meta.get("id")
            if isinstance(anchor_id, str) and anchor_id:
                payload["anchor_id"] = anchor_id
        return payload

    def _resolve_selection_indices_from_ids(
        self,
        obj: object,
        path_idx: int,
        anchor_idx: int,
        *,
        path_id: str | None,
        anchor_id: str | None,
    ) -> tuple[int, int]:
        paths = list(getattr(obj, "paths", None) or [])
        if not paths:
            return (0, 0)

        resolved_path_idx = self._find_path_index_by_id(obj, path_id)
        if anchor_id:
            search_paths = (
                range(len(paths))
                if resolved_path_idx is None
                else range(resolved_path_idx, resolved_path_idx + 1)
            )
            for candidate_path_idx in search_paths:
                candidate_anchor_idx = self._find_anchor_index_by_id(
                    obj,
                    candidate_path_idx,
                    anchor_id,
                )
                if candidate_anchor_idx is not None:
                    return (candidate_path_idx, candidate_anchor_idx)

        if resolved_path_idx is not None:
            path_idx = resolved_path_idx
        path_idx = max(0, min(path_idx, len(paths) - 1))
        anchors = list(getattr(paths[path_idx], "anchors", None) or [])
        if not anchors:
            return (path_idx, 0)
        anchor_idx = max(0, min(anchor_idx, len(anchors) - 1))
        return (path_idx, anchor_idx)

    def _find_path_index_by_id(self, obj: object | None, path_id: str | None) -> int | None:
        if not path_id:
            return None
        ids_meta = self._vector_ids_meta(obj)
        paths_meta = ids_meta.get("paths") if ids_meta is not None else None
        if not isinstance(paths_meta, Sequence) or isinstance(paths_meta, (str, bytes)):
            return None
        for index, entry in enumerate(paths_meta):
            if isinstance(entry, Mapping) and entry.get("id") == path_id:
                return index
        return None

    def _find_anchor_index_by_id(
        self,
        obj: object | None,
        path_idx: int,
        anchor_id: str | None,
    ) -> int | None:
        if not anchor_id:
            return None
        path_meta = self._path_id_entry(obj, path_idx)
        if path_meta is None:
            return None
        anchors_meta = path_meta.get("anchors")
        if not isinstance(anchors_meta, Sequence) or isinstance(anchors_meta, (str, bytes)):
            return None
        for index, entry in enumerate(anchors_meta):
            if isinstance(entry, Mapping) and entry.get("id") == anchor_id:
                return index
        return None

    def _path_id_entry(self, obj: object | None, path_idx: int) -> Mapping[str, object] | None:
        ids_meta = self._vector_ids_meta(obj)
        paths_meta = ids_meta.get("paths") if ids_meta is not None else None
        return self._indexed_mapping(paths_meta, path_idx)

    def _vector_ids_meta(self, obj: object | None) -> Mapping[str, object] | None:
        meta = getattr(obj, "meta", None)
        if not isinstance(meta, Mapping):
            return None
        raw_ids = meta.get(VECTOR_IDS_META_KEY)
        return raw_ids if isinstance(raw_ids, Mapping) else None

    @staticmethod
    def _indexed_mapping(value: object, index: int) -> Mapping[str, object] | None:
        if index < 0 or not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return None
        if index >= len(value):
            return None
        entry = value[index]
        return entry if isinstance(entry, Mapping) else None

    @staticmethod
    def _selection_index(value: object, default: int = 0) -> int:
        try:
            return max(0, int(value or default))
        except (TypeError, ValueError):
            return default

    def _shape_handle_payload(self, obj: object | None) -> list[dict[str, object]]:
        if obj is None:
            return []
        handles = parametric_handles(obj)
        if not handles:
            return []
        payload: list[dict[str, object]] = []
        for handle in handles:
            entry: dict[str, object] = {
                "id": handle.handle_id,
                "x": float(handle.pos[0]),
                "y": float(handle.pos[1]),
                "kind": handle.kind,
                "active": handle.handle_id == self._shape_handle_drag_id,
            }
            if handle.color is not None:
                entry["color"] = list(handle.color)
            payload.append(entry)
        return payload

    def _clear_vector_selection_on_canvas(self) -> None:
        """Clear canvas vector selection (and preview) without resetting tool settings."""
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas:
            return
        try:
            if hasattr(canvas, "_vector_preview"):
                canvas._vector_preview = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                canvas._vector_preview_layer_id = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            logger.debug("VectorPathEditTool clear canvas selection failed", exc_info=True)

    def _is_outside_selected_object_bbox(self, pt: HostStrokePoint) -> bool:
        bbox = None
        canvas = getattr(self.host_api, "canvas", None)
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if isinstance(selection, dict):
            tb = selection.get("transform_bounds")
            if isinstance(tb, (list, tuple)) and len(tb) == 4:
                try:
                    bbox = (float(tb[0]), float(tb[1]), float(tb[2]), float(tb[3]))
                except (TypeError, ValueError):
                    bbox = None
        if bbox is None:
            bbox = self._compute_bbox(self._current_selection_object())
        if bbox is None:
            return True
        min_x, min_y, max_x, max_y = bbox
        px, py = float(pt.pos[0]), float(pt.pos[1])
        return not (min_x <= px <= max_x and min_y <= py <= max_y)

    def _clear_canvas_preview(self) -> None:
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                canvas = self.host_api.canvas
                if hasattr(canvas, "_vector_preview"):
                    canvas._vector_preview = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                    canvas._vector_preview_layer_id = None  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(canvas, "notify_vector_preview_changed"):
                    canvas.notify_vector_preview_changed()
                elif hasattr(canvas, "_recompose"):
                    canvas._recompose()
            except Exception:
                logger.debug("VectorPathEditTool preview clear failed", exc_info=True)

    def _ensure_handles(self, path: VectorPath, anchor_idx: int) -> VectorPath:
        anchors = list(path.anchors)
        if anchor_idx >= len(anchors):
            return path
        anchor = anchors[anchor_idx]
        if anchor.h_in and anchor.h_out:
            return path
        if anchor_idx > 0:
            prev_anchor = anchors[anchor_idx - 1]
        elif path.closed and len(anchors) > 1:
            prev_anchor = anchors[-1]
        else:
            prev_anchor = None
        if anchor_idx + 1 < len(anchors):
            next_anchor = anchors[anchor_idx + 1]
        elif path.closed and len(anchors) > 1:
            next_anchor = anchors[0]
        else:
            next_anchor = None
        vec_x = 12.0
        vec_y = 0.0
        if prev_anchor and next_anchor:
            dx = next_anchor.pos[0] - prev_anchor.pos[0]
            dy = next_anchor.pos[1] - prev_anchor.pos[1]
            vec_x = dx * 0.3
            vec_y = dy * 0.3
        anchors[anchor_idx] = Anchor(
            pos=anchor.pos,
            kind="smooth",
            h_in=(-vec_x, -vec_y),
            h_out=(vec_x, vec_y),
        )
        return VectorPath(anchors=anchors, closed=path.closed)

    def _reset(
        self,
        *,
        keep_style_settings: bool = False,
        keep_auto_return: bool = False,
    ) -> None:
        style_settings = dict(self._style_settings) if keep_style_settings else None
        self._width_editor.set_enabled(False)
        auto_return_tool_id = self._auto_return_tool_id if keep_auto_return else None
        auto_return_origin_id = self._auto_return_origin_id if keep_auto_return else None
        self._clear_anchor_box_preview()
        self._selected = None
        self._selected_anchors = []
        self._anchor_box_start = None
        self._anchor_box_auto_return_tool = None
        self._editing_object = None
        self._flush_pending_commands()
        self._pending_commands.clear()
        self._dragging = False
        self._transform_mode = None
        self._transform_handle = None
        self._transform_bbox = None
        self._transform_pivot = None
        self._transform_press_vec = None
        self._transform_press_point = None
        self._transform_start_object = None
        self._transform_preview_object = None
        self._transform_duplicate = False
        self._transform_duplicate_layer_id = None
        self._transform_duplicate_insert_index = None
        self._transform_shift_locked = False
        self._transform_snap_override = False
        self._shape_handle_drag_id = None
        self._selected_object_cache = None
        self._auto_return_tool_id = auto_return_tool_id
        self._auto_return_origin_id = auto_return_origin_id
        if style_settings is not None:
            self._style_settings = style_settings
        else:
            self._style_settings = {
                "stroke_color": (0, 0, 0),
                "fill_enabled": False,
                "fill_color": None,
            }

    # --- Hover hit-tests for selected object -----------------------------
    def _hover_hit_selected_segment(
        self, px: float, py: float, threshold: float = 3.0
    ) -> tuple[int, int, float, float] | None:
        """Return (path_idx, seg_idx, t, dist2) if near a segment of the selected object."""
        if not self._selected:
            return None
        obj = self._current_selection_object()
        if obj is None or is_editable_parametric_shape(obj):
            return None
        paths = getattr(obj, "paths", None) or []
        best: tuple[int, int, float, float] | None = None
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius(threshold) ** 2
        for pi, path in enumerate(paths):
            anchors = getattr(path, "anchors", None) or []
            if len(anchors) < 2:
                continue
            count = len(anchors)
            seg_count = count if getattr(path, "closed", False) else count - 1
            for i in range(seg_count):
                a = anchors[i]
                b = anchors[(i + 1) % count]
                dist2, t = self._bezier_segment_distance_sq(
                    px, py, a, b, max_distance=math.sqrt(threshold_sq)
                )
                if dist2 <= threshold_sq and (best is None or dist2 < best[3]):
                    best = (pi, i, t, dist2)
        return best

    def _hover_hit_selected_anchor(
        self, px: float, py: float, threshold: float = 3.0
    ) -> tuple[int, int, float] | None:
        """Return (path_idx, anchor_idx, dist2) if near an anchor of the selected object."""
        if not self._selected:
            return None
        obj = self._current_selection_object()
        if obj is None or is_editable_parametric_shape(obj):
            return None
        paths = getattr(obj, "paths", None) or []
        best: tuple[int, int, float] | None = None
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius(threshold) ** 2
        for pi, path in enumerate(paths):
            anchors = getattr(path, "anchors", None) or []
            for ai, anchor in enumerate(anchors):
                dx = anchor.pos[0] - px
                dy = anchor.pos[1] - py
                dist2 = dx * dx + dy * dy
                if dist2 <= threshold_sq and (best is None or dist2 < best[2]):
                    best = (pi, ai, dist2)
        return best

    def _hover_hit_selected_handle(
        self, px: float, py: float, threshold: float = 3.0
    ) -> tuple[int, int, str, float] | None:
        """Return (path_idx, anchor_idx, kind, dist2) if near a handle of the selected object."""
        if not self._selected:
            return None
        obj = self._current_selection_object()
        if obj is None or is_editable_parametric_shape(obj):
            return None
        paths = getattr(obj, "paths", None) or []
        best: tuple[int, int, str, float] | None = None
        threshold_sq = VectorHitTestGeometry(self.host_api).canvas_radius(threshold) ** 2
        for pi, path in enumerate(paths):
            anchors = getattr(path, "anchors", None) or []
            for ai, anchor in enumerate(anchors):
                for kind, h in (("in", anchor.h_in), ("out", anchor.h_out)):
                    if not h:
                        continue
                    hx = anchor.pos[0] + h[0]
                    hy = anchor.pos[1] + h[1]
                    dx = hx - px
                    dy = hy - py
                    dist2 = dx * dx + dy * dy
                    if dist2 <= threshold_sq and (best is None or dist2 < best[3]):
                        best = (pi, ai, kind, dist2)
        return best

    def _hover_hit_selected_fill(self, px: float, py: float) -> bool:
        """Return True if (px, py) is inside a closed path of the selected object."""
        return self._hit_selected_fill_path(px, py) is not None

    def _hit_selected_fill_path(self, px: float, py: float) -> tuple[int, int] | None:
        """Return (path_idx, anchor_idx) for fill hit on the selected object."""
        if not self._selected:
            return None
        obj = self._current_selection_object()
        if obj is None:
            return None
        try:
            from PySide6.QtCore import QPointF  # noqa: PLC0415
        except Exception:
            return None
        paths = getattr(obj, "paths", None) or []
        best: tuple[int, int, float] | None = None
        for path_idx, path in enumerate(paths):
            if not getattr(path, "closed", False):
                continue
            anchors = getattr(path, "anchors", None) or []
            if len(anchors) < 3:
                continue
            qpath = self._build_qpath_for_closed(anchors, len(anchors))
            if qpath.contains(QPointF(px, py)):
                rect = qpath.boundingRect()
                area = rect.width() * rect.height()
                if best is None or area < best[2]:
                    best = (path_idx, 0, area)
        if best is None:
            return None
        return (best[0], best[1])

    def _select_path_on_selected_object(self, path_idx: int, anchor_idx: int) -> bool:
        """Update local selected path/anchor while keeping the same object selected."""
        if not self._selected:
            return False
        obj = self._current_selection_object()
        if obj is None:
            return False
        paths = list(getattr(obj, "paths", None) or [])
        if not (0 <= path_idx < len(paths)):
            return False
        anchors = list(getattr(paths[path_idx], "anchors", None) or [])
        if not anchors:
            return False
        anchor_idx = max(0, min(anchor_idx, len(anchors) - 1))
        self._selected = _Selection(
            self._selected.vec_id,
            path_idx,
            anchor_idx,
            self._selected.layer_id,
            None,
        )
        self._selected_anchors = [_AnchorSelection(path_idx, anchor_idx)]
        self._editing_object = copy_vector_object(obj)
        if self._editing_object is not None and not is_editable_parametric_shape(obj):
            self._editing_object.paths[path_idx] = self._ensure_handles(
                self._editing_object.paths[path_idx],
                anchor_idx,
            )
        return True

    def _delete_anchor_on_selected(self, path_idx: int, anchor_idx: int) -> None:
        """Delete an anchor from the selected object and commit."""
        if not self._selected or not self.host_api:
            return
        obj = self._current_selection_object()
        if obj is None:
            return
        vec_id = self._selected.vec_id
        layer_id = self._selected.layer_id
        path = obj.paths[path_idx]
        anchors = list(path.anchors)
        min_anchor_count = 3 if path.closed else 2
        if len(anchors) <= min_anchor_count:
            return
        del anchors[anchor_idx]
        new_paths = list(obj.paths)
        new_paths[path_idx] = VectorPath(anchors=anchors, closed=path.closed)
        new_obj = self._with_new_paths(obj, new_paths, preserve_id=True)
        new_obj = self._with_anchor_id_topology_change(
            new_obj,
            path_idx,
            anchor_idx,
            inserted=False,
        )
        cmd = VectorReplaceCommand(vec_id, new_obj, layer_id=layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)
        new_anchor_idx = min(anchor_idx, len(anchors) - 1)
        self._selected = _Selection(vec_id, path_idx, new_anchor_idx, layer_id, None)
        self._editing_object = copy_vector_object(new_obj)
        self._update_canvas_selection(
            vec_id,
            path_idx,
            new_anchor_idx,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
        )
        self._clear_canvas_preview()

    def _toggle_selected_path_closed(self) -> bool:
        """Toggle the selected path between open and closed via the command path."""
        if self._dragging or self._transform_mode:
            return False
        if not self._selected:
            self._hydrate_selection_from_canvas()
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if not self._selected or canvas is None:
            return False

        obj = self._current_selection_object()
        paths = list(getattr(obj, "paths", None) or [])
        if obj is None or is_editable_parametric_shape(obj) or not paths:
            return False
        path_idx = self._selected.path_idx
        if path_idx < 0 or path_idx >= len(paths):
            return False

        path = paths[path_idx]
        anchors = list(getattr(path, "anchors", None) or [])
        target_closed = not bool(getattr(path, "closed", False))
        if (target_closed and len(anchors) < 3) or (not target_closed and len(anchors) < 2):
            return False

        paths[path_idx] = VectorPath(anchors=anchors, closed=target_closed)
        new_obj = self._with_new_paths(obj, paths, preserve_id=True)
        vec_id = self._selected.vec_id
        layer_id = self._selected.layer_id
        cmd = VectorReplaceCommand(vec_id, new_obj, layer_id=layer_id)
        try:
            success = canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)

        anchor_idx = min(self._selected.anchor_idx, len(anchors) - 1)
        self._selected = _Selection(vec_id, path_idx, anchor_idx, layer_id, None)
        self._editing_object = copy_vector_object(new_obj)
        self._selected_object_cache = new_obj
        self._update_canvas_selection(
            vec_id,
            path_idx,
            anchor_idx,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
        )
        self._clear_canvas_preview()
        return True

    def _convert_selected_anchor_handle_mode(self, mode: str) -> bool:
        target = self._selected_anchor_handle_target()
        if target is None:
            return False
        canvas, obj, path_idx, anchor_idx, path, anchors = target
        converted = self._anchor_with_handle_mode(path, anchor_idx, mode)
        if converted is None or converted == anchors[anchor_idx]:
            return False
        anchors[anchor_idx] = converted
        new_paths = list(obj.paths)
        new_paths[path_idx] = VectorPath(anchors=anchors, closed=path.closed)
        new_obj = self._with_new_paths(obj, new_paths, preserve_id=True)
        cmd = VectorReplaceCommand(self._selected.vec_id, new_obj, layer_id=self._selected.layer_id)
        try:
            success = canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)

        self._editing_object = copy_vector_object(new_obj)
        self._selected_object_cache = new_obj
        self._selected = _Selection(
            self._selected.vec_id,
            path_idx,
            anchor_idx,
            self._selected.layer_id,
            None,
        )
        self._update_canvas_selection(
            self._selected.vec_id,
            path_idx,
            anchor_idx,
            self._selected.layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
        )
        self._clear_canvas_preview()
        return True

    def _selected_anchor_handle_target(
        self,
    ) -> tuple[object, object, int, int, VectorPath, list[Anchor]] | None:
        if self._dragging or self._transform_mode:
            return None
        if not self._selected:
            self._hydrate_selection_from_canvas()
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        obj = self._current_selection_object()
        if not self._selected or canvas is None or obj is None or is_editable_parametric_shape(obj):
            return None
        path_idx = self._selected.path_idx
        anchor_idx = self._selected.anchor_idx
        paths = list(getattr(obj, "paths", None) or [])
        if not (0 <= path_idx < len(paths)):
            return None
        path = paths[path_idx]
        anchors = list(path.anchors)
        if not (0 <= anchor_idx < len(anchors)):
            return None
        return (canvas, obj, path_idx, anchor_idx, path, anchors)

    def _anchor_with_handle_mode(
        self,
        path: VectorPath,
        anchor_idx: int,
        mode: str,
    ) -> Anchor | None:
        anchor = path.anchors[anchor_idx]
        if mode == "corner":
            return Anchor(
                pos=anchor.pos,
                kind="corner",
                h_in=(0.0, 0.0),
                h_out=(0.0, 0.0),
            )

        working_path = path
        if not self._nonzero_handle(anchor.h_in) and not self._nonzero_handle(anchor.h_out):
            working_path = self._ensure_handles(path, anchor_idx)
            anchor = working_path.anchors[anchor_idx]

        if mode == "break":
            return Anchor(
                pos=anchor.pos,
                kind="corner",
                h_in=anchor.h_in,
                h_out=anchor.h_out,
            )
        if mode in {"smooth", "symmetric"}:
            return self._smooth_anchor_from_handles(anchor, symmetric=mode == "symmetric")
        return None

    def _smooth_anchor_from_handles(self, anchor: Anchor, *, symmetric: bool) -> Anchor:
        in_vec = anchor.h_in if self._nonzero_handle(anchor.h_in) else None
        out_vec = anchor.h_out if self._nonzero_handle(anchor.h_out) else None
        tangent = self._handle_tangent(in_vec, out_vec)
        length_in = self._handle_length(in_vec) or self._handle_length(out_vec) or 12.0
        length_out = self._handle_length(out_vec) or self._handle_length(in_vec) or 12.0
        if symmetric:
            length = (length_in + length_out) * 0.5
            length_in = length
            length_out = length
        return Anchor(
            pos=anchor.pos,
            kind="smooth",
            h_in=(-tangent[0] * length_in, -tangent[1] * length_in),
            h_out=(tangent[0] * length_out, tangent[1] * length_out),
        )

    def _handle_tangent(
        self,
        in_vec: tuple[float, float] | None,
        out_vec: tuple[float, float] | None,
    ) -> tuple[float, float]:
        if in_vec is not None and out_vec is not None:
            tangent = (out_vec[0] - in_vec[0], out_vec[1] - in_vec[1])
        elif out_vec is not None:
            tangent = out_vec
        elif in_vec is not None:
            tangent = (-in_vec[0], -in_vec[1])
        else:
            tangent = (1.0, 0.0)
        length = math.hypot(tangent[0], tangent[1])
        if length <= 1e-9:
            return (1.0, 0.0)
        return (tangent[0] / length, tangent[1] / length)

    @staticmethod
    def _handle_length(handle: tuple[float, float] | None) -> float:
        if handle is None:
            return 0.0
        return math.hypot(handle[0], handle[1])

    def _insert_anchor_on_selected(self, path_idx: int, seg_idx: int, t: float) -> None:
        """Insert an anchor on a segment of the selected object and commit."""
        if not self._selected or not self.host_api:
            return
        obj = self._current_selection_object()
        if obj is None:
            return
        vec_id = self._selected.vec_id
        layer_id = self._selected.layer_id
        path = obj.paths[path_idx]
        anchors = list(path.anchors)
        a = anchors[seg_idx]
        next_idx = (seg_idx + 1) % len(anchors)
        b = anchors[next_idx]
        left_anchor, new_anchor, right_anchor = self._split_segment_anchors(a, b, t)
        insert_at = seg_idx + 1
        anchors[seg_idx] = left_anchor
        anchors[next_idx] = right_anchor
        anchors.insert(insert_at, new_anchor)
        new_paths = list(obj.paths)
        new_paths[path_idx] = VectorPath(anchors=anchors, closed=path.closed)
        new_obj = self._with_new_paths(obj, new_paths, preserve_id=True)
        new_obj = self._with_anchor_id_topology_change(
            new_obj,
            path_idx,
            insert_at,
            inserted=True,
        )
        cmd = VectorReplaceCommand(vec_id, new_obj, layer_id=layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)
        self._selected = _Selection(vec_id, path_idx, insert_at, layer_id, None)
        self._editing_object = copy_vector_object(new_obj)
        self._update_canvas_selection(
            vec_id,
            path_idx,
            insert_at,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
        )
        self._clear_canvas_preview()

    # --- Transform helpers ----------------------------------------------
    def _try_ctrl_anchor_ops(self, pt: HostStrokePoint) -> bool:
        """Ctrl+click: add anchor on segment or delete anchor under cursor."""
        if not self.host_api:
            return False
        current = self._current_selection_object()
        if current is not None and is_editable_parametric_shape(current):
            return False
        return self._delete_anchor_at_point(pt) or self._insert_anchor_on_segment(pt)

    def _delete_anchor_at_point(self, pt: HostStrokePoint, threshold: float = 3.0) -> bool:
        px, py = float(pt.pos[0]), float(pt.pos[1])
        threshold = VectorHitTestGeometry(self.host_api).canvas_radius(threshold)
        best: tuple[str, str | None, int, int, float] | None = (
            None  # (vec_id, layer_id, path_idx, anchor_idx, dist2)
        )
        store = self._vector_store()
        for layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if is_editable_parametric_shape(obj):
                continue
            paths = getattr(obj, "paths", None) or []
            for pi, path in enumerate(paths):
                anchors = getattr(path, "anchors", None) or []
                for ai, anchor in enumerate(anchors):
                    dx = anchor.pos[0] - px
                    dy = anchor.pos[1] - py
                    dist2 = dx * dx + dy * dy
                    if dist2 <= threshold * threshold and (best is None or dist2 < best[4]):
                        best = (getattr(obj, "id", None), layer_id, pi, ai, dist2)
        if not best:
            return False
        vec_id, layer_id, pi, ai, _ = best
        target_obj = None
        for _lid, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if getattr(obj, "id", None) == vec_id:
                target_obj = obj
                break
        if target_obj is None:
            return False
        path = target_obj.paths[pi]
        anchors = list(path.anchors)
        min_anchor_count = 3 if path.closed else 2
        if len(anchors) <= min_anchor_count:
            return False
        del anchors[ai]
        new_paths = list(target_obj.paths)
        new_paths[pi] = VectorPath(anchors=anchors, closed=path.closed)
        new_obj = self._with_new_paths(target_obj, new_paths, preserve_id=True)
        new_obj = self._with_anchor_id_topology_change(new_obj, pi, ai, inserted=False)
        cmd = VectorReplaceCommand(vec_id, new_obj, layer_id=layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)
        new_anchor_idx = min(ai, len(anchors) - 1)
        self._selected = _Selection(vec_id, pi, new_anchor_idx, layer_id, None)
        self._editing_object = copy_vector_object(new_obj)
        self._update_canvas_selection(
            vec_id,
            pi,
            new_anchor_idx,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
            mode=None,
        )
        self._clear_canvas_preview()
        return True

    def _insert_anchor_on_segment(self, pt: HostStrokePoint, threshold: float = 3.0) -> bool:
        px, py, threshold = (
            float(pt.pos[0]),
            float(pt.pos[1]),
            VectorHitTestGeometry(self.host_api).canvas_radius(threshold),
        )
        best: tuple[str, str | None, int, int, float, float] | None = (
            None  # (vec_id, layer_id, path_idx, insert_after_idx, t, dist2)
        )
        store = self._vector_store()
        for layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if is_editable_parametric_shape(obj):
                continue
            paths = getattr(obj, "paths", None) or []
            for pi, path in enumerate(paths):
                anchors = getattr(path, "anchors", None) or []
                if len(anchors) < 2:
                    continue
                count = len(anchors)
                seg_count = count if getattr(path, "closed", False) else count - 1
                for i in range(seg_count):
                    a = anchors[i]
                    b = anchors[(i + 1) % count]
                    dist2, t = self._bezier_segment_distance_sq(
                        px, py, a, b, max_distance=threshold
                    )
                    if dist2 <= threshold * threshold and (best is None or dist2 < best[5]):
                        best = (getattr(obj, "id", None), layer_id, pi, i, t, dist2)
        if not best:
            return False
        vec_id, layer_id, pi, seg_idx, t, _ = best
        target_obj = None
        for _lid, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if getattr(obj, "id", None) == vec_id:
                target_obj = obj
                break
        if target_obj is None:
            return False
        path = target_obj.paths[pi]
        anchors = list(path.anchors)
        a = anchors[seg_idx]
        next_idx = (seg_idx + 1) % len(anchors)
        b = anchors[next_idx]
        left_anchor, new_anchor, right_anchor = self._split_segment_anchors(a, b, t)
        insert_at = seg_idx + 1
        anchors[seg_idx] = left_anchor
        anchors[next_idx] = right_anchor
        anchors.insert(insert_at, new_anchor)
        new_paths = list(target_obj.paths)
        new_paths[pi] = VectorPath(anchors=anchors, closed=path.closed)
        new_obj = self._with_new_paths(target_obj, new_paths, preserve_id=True)
        new_obj = self._with_anchor_id_topology_change(new_obj, pi, insert_at, inserted=True)
        cmd = VectorReplaceCommand(vec_id, new_obj, layer_id=layer_id)
        try:
            success = self.host_api.canvas.submit(cmd, merge_key=None)
            if not success:
                self._pending_commands.append(cmd)
        except Exception:
            self._pending_commands.append(cmd)
        self._selected = _Selection(vec_id, pi, insert_at, layer_id, None)
        self._editing_object = copy_vector_object(new_obj)
        self._update_canvas_selection(
            vec_id,
            pi,
            insert_at,
            layer_id,
            preview=False,
            transform_bounds=self._compute_bbox(new_obj),
            mode=None,
        )
        self._clear_canvas_preview()
        return True

    def _point_segment_distance_sq(
        self, px: float, py: float, a: tuple[float, float], b: tuple[float, float]
    ) -> tuple[float, float]:
        ax, ay = a
        bx, by = b
        abx = bx - ax
        aby = by - ay
        apx = px - ax
        apy = py - ay
        ab_len2 = abx * abx + aby * aby
        if ab_len2 <= 1e-9:
            return ((apx * apx + apy * apy), 0.0)
        t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab_len2))
        cx = ax + abx * t
        cy = ay + aby * t
        dx = px - cx
        dy = py - cy
        return (dx * dx + dy * dy, t)

    def _split_segment_anchors(
        self,
        a: Anchor,
        b: Anchor,
        t: float,
    ) -> tuple[Anchor, Anchor, Anchor]:
        t = max(0.0, min(1.0, float(t)))
        h_out = a.h_out
        h_in = b.h_in
        has_out = self._nonzero_handle(h_out)
        has_in = self._nonzero_handle(h_in)
        if not has_out and not has_in:
            new_pos = (
                a.pos[0] + (b.pos[0] - a.pos[0]) * t,
                a.pos[1] + (b.pos[1] - a.pos[1]) * t,
            )
            return (
                a,
                Anchor(pos=new_pos, kind="corner", h_in=(0.0, 0.0), h_out=(0.0, 0.0)),
                b,
            )

        p0 = a.pos
        p1 = self._absolute_handle(a.pos, h_out)
        p2 = self._absolute_handle(b.pos, h_in)
        p3 = b.pos
        q0 = self._lerp_point(p0, p1, t)
        q1 = self._lerp_point(p1, p2, t)
        q2 = self._lerp_point(p2, p3, t)
        r0 = self._lerp_point(q0, q1, t)
        r1 = self._lerp_point(q1, q2, t)
        split = self._lerp_point(r0, r1, t)

        left_anchor = Anchor(
            pos=a.pos,
            kind=a.kind,
            h_in=a.h_in,
            h_out=(q0[0] - p0[0], q0[1] - p0[1]),
        )
        split_anchor = Anchor(
            pos=split,
            kind="smooth",
            h_in=(r0[0] - split[0], r0[1] - split[1]),
            h_out=(r1[0] - split[0], r1[1] - split[1]),
        )
        right_anchor = Anchor(
            pos=b.pos,
            kind=b.kind,
            h_in=(q2[0] - p3[0], q2[1] - p3[1]),
            h_out=b.h_out,
        )
        return (left_anchor, split_anchor, right_anchor)

    @staticmethod
    def _nonzero_handle(handle: tuple[float, float] | None) -> bool:
        return bool(handle and (abs(handle[0]) > 1e-3 or abs(handle[1]) > 1e-3))

    @staticmethod
    def _absolute_handle(
        pos: tuple[float, float],
        handle: tuple[float, float] | None,
    ) -> tuple[float, float]:
        return (pos[0] + (handle[0] if handle else 0.0), pos[1] + (handle[1] if handle else 0.0))

    @staticmethod
    def _lerp_point(
        a: tuple[float, float],
        b: tuple[float, float],
        t: float,
    ) -> tuple[float, float]:
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    @staticmethod
    def _eval_cubic(
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
        t: float,
    ) -> tuple[float, float]:
        """Evaluate cubic Bezier at parameter *t*."""
        u = 1.0 - t
        uu = u * u
        tt = t * t
        x = uu * u * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + tt * t * p3[0]
        y = uu * u * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + tt * t * p3[1]
        return (x, y)

    def _bezier_segment_distance_sq(
        self,
        px: float,
        py: float,
        a: Anchor,
        b: Anchor,
        *,
        samples: int = 16,
        max_distance: float | None = None,
    ) -> tuple[float, float]:
        return CubicHitTest.distance_squared(
            px, py, a, b, samples=samples, max_distance=max_distance
        )

    @staticmethod
    def _build_qpath_for_closed(anchors, count):  # noqa: PLR0912
        """Build a closed QPainterPath from *anchors* (used by fill hit-test)."""
        from PySide6.QtGui import QPainterPath  # noqa: PLC0415

        qpath = QPainterPath()
        qpath.moveTo(float(anchors[0].pos[0]), float(anchors[0].pos[1]))
        for i in range(count):
            a_anc = anchors[i]
            b_anc = anchors[(i + 1) % count]
            h_out = a_anc.h_out
            h_in = b_anc.h_in
            has_out = h_out and (abs(h_out[0]) > 1e-3 or abs(h_out[1]) > 1e-3)
            has_in_flag = h_in and (abs(h_in[0]) > 1e-3 or abs(h_in[1]) > 1e-3)
            if has_out or has_in_flag:
                c1x = a_anc.pos[0] + (h_out[0] if h_out else 0.0)
                c1y = a_anc.pos[1] + (h_out[1] if h_out else 0.0)
                c2x = b_anc.pos[0] + (h_in[0] if h_in else 0.0)
                c2y = b_anc.pos[1] + (h_in[1] if h_in else 0.0)
                qpath.cubicTo(c1x, c1y, c2x, c2y, float(b_anc.pos[0]), float(b_anc.pos[1]))
            else:
                qpath.lineTo(float(b_anc.pos[0]), float(b_anc.pos[1]))
        qpath.closeSubpath()
        return qpath

    def _hit_test_fill(self, pt: HostStrokePoint) -> bool:
        """Select an object by clicking inside a closed path's interior."""
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])

        try:
            from PySide6.QtCore import QPointF  # noqa: PLC0415
        except Exception:
            return False

        best: tuple[str, str | None, int, float] | None = None

        for layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            vec_id = getattr(obj, "id", None)
            if not vec_id:
                continue
            paths = getattr(obj, "paths", None) or []
            for pi, path in enumerate(paths):
                if not getattr(path, "closed", False):
                    continue
                anchors = getattr(path, "anchors", None) or []
                if len(anchors) < 3:
                    continue
                qpath = self._build_qpath_for_closed(anchors, len(anchors))
                if not qpath.contains(QPointF(px, py)):
                    continue
                rect = qpath.boundingRect()
                area = rect.width() * rect.height()
                if best is None or area < best[3]:
                    best = (vec_id, layer_id, pi, area)

        if best is None:
            return False
        return self._select_object_by_id(store, best[0], best[1], best[2], 0)

    def _select_object_by_id(
        self,
        store: list,
        vec_id: str,
        layer_id: str | None,
        path_idx: int,
        anchor_idx: int,
    ) -> bool:
        """Shared selection helper used by segment / fill hit-tests."""
        target_obj = None
        for candidate_layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if getattr(obj, "id", None) == vec_id:
                target_obj = obj
                layer_id = candidate_layer_id
                break
        if target_obj is None:
            return False

        self._editing_object = copy_vector_object(target_obj)
        self._update_style_settings_from_object(target_obj)
        self._sync_style_settings_to_tool_properties()
        if (
            self._editing_object
            and path_idx < len(self._editing_object.paths)
            and not is_editable_parametric_shape(target_obj)
        ):
            self._editing_object.paths[path_idx] = self._ensure_handles(
                self._editing_object.paths[path_idx], anchor_idx
            )
        self._selected = _Selection(vec_id, path_idx, anchor_idx, layer_id, None)
        self._selected_anchors = [_AnchorSelection(path_idx, anchor_idx)]
        self._update_canvas_selection(
            vec_id,
            path_idx,
            anchor_idx,
            layer_id,
            preview=True,
            transform_bounds=self._compute_bbox(self._editing_object),
        )
        return True

    def _maybe_begin_transform(
        self, pt: HostStrokePoint, *, alt: bool, shift: bool, ctrl: bool
    ) -> bool:
        if not self._selected:
            self._hydrate_selection_from_canvas()
        if not self._selected:
            return False
        hit = self._hit_test_transform_handle(pt)
        if not hit:
            return False
        handle_kind, mode = hit
        self._transform_shift_locked = shift
        self._transform_snap_override = ctrl
        self._begin_transform(handle_kind, mode, pt, alt=alt)
        if not self._transform_mode:
            return False
        self._dragging = True
        return True

    def _flush_pending_commands(self) -> None:
        """Try to submit any pending commands accumulated from failed attempts."""
        if (
            not self._pending_commands
            or not self.host_api
            or not getattr(self.host_api, "canvas", None)
        ):
            return
        still_pending: list[VectorReplaceCommand] = []
        for cmd in list(self._pending_commands):
            try:
                success = self.host_api.canvas.submit(cmd, merge_key=None)
            except Exception:
                success = False
            if not success:
                still_pending.append(cmd)
        self._pending_commands = still_pending

    def _hit_test_transform_handle(  # noqa: PLR0911
        self, pt: HostStrokePoint
    ) -> tuple[str, str] | None:
        if not self._selected:
            return None
        selection = getattr(getattr(self.host_api, "canvas", None), "_vector_selection", None)
        bbox = None
        if selection and selection.get("transform_bounds"):
            tb = selection["transform_bounds"]
            if isinstance(tb, (list, tuple)) and len(tb) == 4:
                bbox = (float(tb[0]), float(tb[1]), float(tb[2]), float(tb[3]))
        if bbox is None:
            obj = self._current_selection_object()
            bbox = self._compute_bbox(obj)
            if bbox is None:
                return None
        return VectorHitTestGeometry(self.host_api).transform_handle(
            float(pt.pos[0]), float(pt.pos[1]), bbox
        )

    def _begin_transform(
        self, handle_kind: str, mode: str, press_pt: HostStrokePoint, *, alt: bool
    ) -> None:
        obj = self._current_selection_object()
        if obj is None:
            return
        bbox = self._compute_bbox(obj)
        if bbox is None:
            return
        self._transform_mode = mode
        self._transform_handle = handle_kind
        self._transform_bbox = bbox
        self._transform_start_object = copy_vector_object(obj)
        self._transform_preview_object = copy_vector_object(obj)
        self._transform_duplicate = bool(alt and mode == "move")
        self._transform_duplicate_layer_id = None
        self._transform_duplicate_insert_index = None
        px, py = float(press_pt.pos[0]), float(press_pt.pos[1])
        self._transform_press_point = (px, py)
        pivot = compute_pivot(handle_kind, bbox)
        self._transform_pivot = pivot
        self._transform_press_vec = (px - pivot[0], py - pivot[1])
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                self.host_api.canvas._vector_preview = self._transform_preview_object  # type: ignore[attr-defined]  # duck-typed canvas attr
                self.host_api.canvas._vector_preview_layer_id = self._selected.layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                    self.host_api.canvas.notify_vector_preview_changed()
                elif hasattr(self.host_api.canvas, "_recompose"):
                    self.host_api.canvas._recompose()
            except Exception:
                logger.debug("Transform begin preview set failed", exc_info=True)
        self._update_canvas_selection(
            self._selected.vec_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            self._selected.layer_id,
            preview=True,
            transform_bounds=bbox,
            mode="transform",
        )
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                    self.host_api.canvas.notify_vector_preview_changed()
                elif hasattr(self.host_api.canvas, "_recompose"):
                    self.host_api.canvas._recompose()
            except Exception:
                logger.debug("Transform begin recompose failed", exc_info=True)
        # Hide context toolbar during transform
        if self.host_api:
            self.host_api.events.emit("tool.transform_start", {"tool_id": self.descriptor().id})

    def _update_transform_drag(  # noqa: PLR0912
        self,
        current_pt: HostStrokePoint,
        *,
        preview_only: bool,
    ) -> None:
        if not self._transform_mode or not self._transform_start_object or not self._selected:
            return
        pivot = self._transform_pivot
        bbox = self._transform_bbox
        press_vec = self._transform_press_vec
        press_point = self._transform_press_point
        if pivot is None or bbox is None or press_point is None:
            return
        cx, cy = float(current_pt.pos[0]), float(current_pt.pos[1])
        mode = self._transform_mode
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        shift_now = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        preview_obj = copy_vector_object(self._transform_start_object)
        if mode == "move":
            delta = (cx - press_point[0], cy - press_point[1])
            if shift_now:
                delta = (delta[0], 0.0) if abs(delta[0]) >= abs(delta[1]) else (0.0, delta[1])
            delta = self._apply_move_snap(delta, ctrl_override=self._transform_snap_override)
            preview_obj = self._apply_transform_translation(preview_obj, delta)
        elif mode == "scale":
            if press_vec is None:
                return
            sx, sy = compute_scale_factors(
                handle=self._transform_handle or "",
                pivot=pivot,
                press_vec=press_vec,
                current=(cx, cy),
                bbox=bbox,
                uniform=shift_now,
            )
            preview_obj = self._apply_transform_scaling(preview_obj, pivot, sx, sy)
        elif mode == "rotate":
            angle = math.atan2(cy - pivot[1], cx - pivot[0]) - math.atan2(
                press_point[1] - pivot[1], press_point[0] - pivot[0]
            )
            if shift_now:
                snap = math.radians(15.0)
                angle = round(angle / snap) * snap
            preview_obj = self._apply_transform_rotation(preview_obj, pivot, angle)
        else:
            logger.debug("Unknown transform mode: %s", mode)
        self._transform_preview_object = preview_obj
        bounds = self._compute_bbox(preview_obj)
        self._update_canvas_selection(
            self._selected.vec_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            self._selected.layer_id,
            preview=True,
            transform_bounds=bounds,
            mode="transform",
        )
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                self.host_api.canvas._vector_preview = preview_obj  # type: ignore[attr-defined]  # duck-typed canvas attr
                self.host_api.canvas._vector_preview_layer_id = self._selected.layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
                if hasattr(self.host_api.canvas, "notify_vector_preview_changed"):
                    self.host_api.canvas.notify_vector_preview_changed()
                elif hasattr(self.host_api.canvas, "_recompose"):
                    self.host_api.canvas._recompose()
            except Exception:
                logger.debug("Transform preview update failed", exc_info=True)

    def _commit_transform(self) -> None:
        if not self._transform_preview_object or not self._selected or not self.host_api:
            self._clear_canvas_preview()
            return
        target_layer = self._selected.layer_id
        target_id = self._selected.vec_id
        if self._transform_duplicate:
            new_obj = copy_vector_object(self._transform_preview_object)
            new_obj = make_vector_object(new_obj.paths, new_obj.style, meta=dict(new_obj.meta))
            # Update reference_bbox proportionally for duplicate
            self._update_parametric_ref_bbox(new_obj)
            layer_id = self._ensure_vector_layer_above(target_layer)
            cmd = VectorCreateCommand(
                new_obj,
                layer_id=layer_id,
                merge_key=None,
                created_layer_id=layer_id,
            )
            try:
                self.host_api.canvas.submit(cmd, merge_key=None)
                logger.debug(
                    "Transform commit duplicate submit: layer=%s id=%s", layer_id, new_obj.id
                )
            except Exception:
                logger.debug("Transform commit duplicate submit failed; queued", exc_info=True)
                self._pending_commands.append(cmd)
            self._update_canvas_selection(
                new_obj.id,
                0,
                0,
                layer_id,
                preview=False,
                transform_bounds=self._compute_bbox(new_obj),
                mode="transform",
            )
            self._clear_canvas_preview()
            self._reset(keep_auto_return=True)
            return
        # Update reference_bbox proportionally based on overall bbox change
        commit_obj = self._transform_preview_object
        self._update_parametric_ref_bbox(commit_obj)
        cmd = VectorReplaceCommand(target_id, commit_obj, layer_id=target_layer)
        try:
            self.host_api.canvas.submit(cmd, merge_key=None)
            logger.debug("Transform commit replace: id=%s layer=%s", target_id, target_layer)
        except Exception:
            logger.debug("Transform commit replace failed; queued", exc_info=True)
            self._pending_commands.append(cmd)
        self._update_canvas_selection(
            target_id,
            self._selected.path_idx,
            self._selected.anchor_idx,
            target_layer,
            preview=False,
            transform_bounds=self._compute_bbox(self._transform_preview_object),
            mode="transform",
        )
        self._clear_canvas_preview()
        self._reset(keep_auto_return=True)

    def _update_parametric_ref_bbox(self, commit_obj: object) -> None:
        """Scale reference_bbox proportionally based on overall bbox change."""
        if not self._transform_start_object:
            return
        meta = getattr(commit_obj, "meta", None)
        if not isinstance(meta, dict):
            return
        pm = meta.get("shape_manager_parametric")
        if not isinstance(pm, dict) or "reference_bbox" not in pm:
            return
        # Read old values first — meta dict is shallow-copied and may be shared
        old_rb = list(pm["reference_bbox"])
        old_full = self._compute_bbox(self._transform_start_object)
        new_full = self._compute_bbox(commit_obj)
        if not old_full or not new_full:
            return
        ow = old_full[2] - old_full[0]
        oh = old_full[3] - old_full[1]
        if ow < 1e-6 or oh < 1e-6:
            # Degenerate: just translate
            dx = new_full[0] - old_full[0]
            dy = new_full[1] - old_full[1]
            pm["reference_bbox"] = [
                old_rb[0] + dx,
                old_rb[1] + dy,
                old_rb[2] + dx,
                old_rb[3] + dy,
            ]
            return
        sx = (new_full[2] - new_full[0]) / ow
        sy = (new_full[3] - new_full[1]) / oh
        pm["reference_bbox"] = [
            new_full[0] + (old_rb[0] - old_full[0]) * sx,
            new_full[1] + (old_rb[1] - old_full[1]) * sy,
            new_full[0] + (old_rb[2] - old_full[0]) * sx,
            new_full[1] + (old_rb[3] - old_full[1]) * sy,
        ]

    def _apply_transform_translation(self, obj, delta: tuple[float, float]) -> object:
        dx, dy = delta
        new_paths: list[VectorPath] = []
        for path in obj.paths:
            new_anchors: list[Anchor] = []
            for anchor in path.anchors:
                pos = (anchor.pos[0] + dx, anchor.pos[1] + dy)
                h_in = None
                h_out = None
                if anchor.h_in:
                    h_in = (anchor.h_in[0], anchor.h_in[1])
                if anchor.h_out:
                    h_out = (anchor.h_out[0], anchor.h_out[1])
                new_anchors.append(Anchor(pos=pos, kind=anchor.kind, h_in=h_in, h_out=h_out))
            new_paths.append(VectorPath(anchors=new_anchors, closed=path.closed))
        return self._with_new_paths(obj, new_paths, preserve_id=True)

    def _apply_transform_scaling(
        self, obj, pivot: tuple[float, float], sx: float, sy: float
    ) -> object:
        px, py = pivot
        new_paths: list[VectorPath] = []
        for path in obj.paths:
            new_anchors: list[Anchor] = []
            for anchor in path.anchors:
                pos_abs = anchor.pos
                pos_new = (
                    px + (pos_abs[0] - px) * sx,
                    py + (pos_abs[1] - py) * sy,
                )
                h_in = None
                h_out = None
                if anchor.h_in:
                    in_abs = (pos_abs[0] + anchor.h_in[0], pos_abs[1] + anchor.h_in[1])
                    in_new = (px + (in_abs[0] - px) * sx, py + (in_abs[1] - py) * sy)
                    h_in = (in_new[0] - pos_new[0], in_new[1] - pos_new[1])
                if anchor.h_out:
                    out_abs = (pos_abs[0] + anchor.h_out[0], pos_abs[1] + anchor.h_out[1])
                    out_new = (px + (out_abs[0] - px) * sx, py + (out_abs[1] - py) * sy)
                    h_out = (out_new[0] - pos_new[0], out_new[1] - pos_new[1])
                new_anchors.append(Anchor(pos=pos_new, kind=anchor.kind, h_in=h_in, h_out=h_out))
            new_paths.append(VectorPath(anchors=new_anchors, closed=path.closed))
        return self._with_new_paths(obj, new_paths, preserve_id=True)

    def _apply_transform_rotation(self, obj, pivot: tuple[float, float], angle: float) -> object:
        sin_a = math.sin(angle)
        cos_a = math.cos(angle)
        px, py = pivot

        def _rotate_point(x: float, y: float) -> tuple[float, float]:
            rx = x - px
            ry = y - py
            return (
                px + rx * cos_a - ry * sin_a,
                py + rx * sin_a + ry * cos_a,
            )

        new_paths: list[VectorPath] = []
        for path in obj.paths:
            new_anchors: list[Anchor] = []
            for anchor in path.anchors:
                pos_new = _rotate_point(anchor.pos[0], anchor.pos[1])
                h_in = None
                h_out = None
                if anchor.h_in:
                    in_abs = (anchor.pos[0] + anchor.h_in[0], anchor.pos[1] + anchor.h_in[1])
                    in_new = _rotate_point(in_abs[0], in_abs[1])
                    h_in = (in_new[0] - pos_new[0], in_new[1] - pos_new[1])
                if anchor.h_out:
                    out_abs = (anchor.pos[0] + anchor.h_out[0], anchor.pos[1] + anchor.h_out[1])
                    out_new = _rotate_point(out_abs[0], out_abs[1])
                    h_out = (out_new[0] - pos_new[0], out_new[1] - pos_new[1])
                new_anchors.append(Anchor(pos=pos_new, kind=anchor.kind, h_in=h_in, h_out=h_out))
            new_paths.append(VectorPath(anchors=new_anchors, closed=path.closed))
        return self._with_new_paths(obj, new_paths, preserve_id=True)

    def _with_new_paths(self, obj, paths: list[VectorPath], *, preserve_id: bool) -> object:
        if preserve_id:
            return type(obj)(
                id=getattr(obj, "id", None),
                paths=paths,
                style=obj.style,
                meta=dict(getattr(obj, "meta", {}) or {}),
                preview=getattr(obj, "preview", False),
            )
        return make_vector_object(
            paths=paths,
            style=obj.style,
            meta=dict(getattr(obj, "meta", {}) or {}),
        )

    def _with_anchor_id_topology_change(
        self,
        obj: object,
        path_idx: int,
        anchor_idx: int,
        *,
        inserted: bool,
    ) -> object:
        if not isinstance(obj, VectorObject):
            return obj

        meta = dict(obj.meta)
        raw_ids = meta.get(VECTOR_IDS_META_KEY)
        if isinstance(raw_ids, Mapping):
            ids_meta = dict(raw_ids)
            raw_paths = ids_meta.get("paths")
            if isinstance(raw_paths, Sequence) and not isinstance(raw_paths, (str, bytes)):
                paths_meta = list(raw_paths)
                if 0 <= path_idx < len(paths_meta) and isinstance(paths_meta[path_idx], Mapping):
                    path_meta = dict(paths_meta[path_idx])
                    raw_anchors = path_meta.get("anchors")
                    if isinstance(raw_anchors, Sequence) and not isinstance(
                        raw_anchors, (str, bytes)
                    ):
                        anchors_meta = [
                            dict(entry) if isinstance(entry, Mapping) else {}
                            for entry in raw_anchors
                        ]
                        if inserted:
                            insert_at = max(0, min(anchor_idx, len(anchors_meta)))
                            anchors_meta.insert(insert_at, {})
                        elif 0 <= anchor_idx < len(anchors_meta):
                            del anchors_meta[anchor_idx]
                        path_meta["anchors"] = anchors_meta
                        paths_meta[path_idx] = path_meta
                        ids_meta["paths"] = paths_meta
                        meta[VECTOR_IDS_META_KEY] = ids_meta

        if meta != obj.meta:
            obj = VectorObject(
                id=obj.id,
                paths=obj.paths,
                style=obj.style,
                meta=meta,
                preview=obj.preview,
            )
        return ensure_vector_element_ids(obj)

    def _compute_bbox(self, obj) -> tuple[float, float, float, float] | None:
        if obj is None or not getattr(obj, "paths", None):
            return None
        from ..shape_manager.ops.bbox import compute_bbox  # noqa: PLC0415

        return compute_bbox(obj.paths)

    def _apply_move_snap(
        self, delta: tuple[float, float], *, ctrl_override: bool
    ) -> tuple[float, float]:
        if ctrl_override:
            return delta
        from ..sdk.snap import snap_position  # noqa: PLC0415

        bbox = self._transform_bbox
        bx = bbox[0] if bbox else 0.0
        by = bbox[1] if bbox else 0.0
        bw = (bbox[2] - bbox[0]) if bbox else 0.0
        bh = (bbox[3] - bbox[1]) if bbox else 0.0
        sx, sy = snap_position(self.host_api, bx + delta[0], by + delta[1], bw, bh)
        return (sx - bx, sy - by)

    def _current_selection_object(self):  # noqa: PLR0911
        if not self._selected:
            return None
        # Prefer preview/editing object if present
        if self._transform_preview_object:
            return self._transform_preview_object
        if (
            self._editing_object
            and getattr(self._editing_object, "id", None) == self._selected.vec_id
        ):
            return self._editing_object
        if self._selected_object_cache and getattr(
            self._selected_object_cache, "id", None
        ) == getattr(self._selected, "vec_id", None):
            return self._selected_object_cache
        store = self._vector_store()
        for _layer_id, vec in store:
            obj = vec[1] if isinstance(vec, tuple) else vec
            if getattr(obj, "id", None) == self._selected.vec_id:
                return obj
        canvas = getattr(self.host_api, "canvas", None)
        preview_obj = getattr(canvas, "_vector_preview", None)
        if preview_obj and getattr(preview_obj, "id", None) == self._selected.vec_id:
            return preview_obj
        return None

    def _hydrate_selection_from_canvas(self) -> None:
        """Build local selection state from canvas selection if available."""
        canvas = getattr(self.host_api, "canvas", None)
        selection = getattr(canvas, "_vector_selection", None)
        if not selection or not isinstance(selection, dict):
            if self._selected or self._editing_object or self._transform_mode:
                self._reset(keep_style_settings=True)
            self._auto_return_tool_id = None
            self._auto_return_origin_id = None
            return
        auto_return_tool = selection.get("auto_return_tool")
        auto_return_origin = selection.get("auto_return_origin_id")
        self._auto_return_tool_id = (
            auto_return_tool if isinstance(auto_return_tool, str) and auto_return_tool else None
        )
        self._auto_return_origin_id = (
            auto_return_origin
            if isinstance(auto_return_origin, str) and auto_return_origin
            else None
        )
        vec_id = selection.get("id")
        path_idx = self._selection_index(selection.get("path"))
        anchor_idx = self._selection_index(selection.get("anchor"))
        path_id = selection.get("path_id")
        anchor_id = selection.get("anchor_id")
        path_id = path_id if isinstance(path_id, str) and path_id else None
        anchor_id = anchor_id if isinstance(anchor_id, str) and anchor_id else None
        layer_id = selection.get("layer_id")
        if not vec_id:
            return
        self._selected = _Selection(vec_id, path_idx, anchor_idx, layer_id, None)
        obj = self._current_selection_object()
        if obj:
            path_idx, anchor_idx = self._resolve_selection_indices_from_ids(
                obj,
                path_idx,
                anchor_idx,
                path_id=path_id,
                anchor_id=anchor_id,
            )
            self._selected = _Selection(vec_id, path_idx, anchor_idx, layer_id, None)
            self._selected_anchors = self._resolve_anchor_refs_from_payload(
                obj,
                selection.get("anchors"),
                primary=_AnchorSelection(path_idx, anchor_idx),
            )
            self._editing_object = copy_vector_object(obj)
            self._update_style_settings_from_object(obj)
            self._sync_style_settings_to_tool_properties()
            self._update_canvas_selection(
                vec_id,
                path_idx,
                anchor_idx,
                layer_id,
                preview=True,
                transform_bounds=self._compute_bbox(self._editing_object),
            )

    def _resolve_anchor_refs_from_payload(
        self,
        obj: object,
        value: object,
        *,
        primary: _AnchorSelection,
    ) -> list[_AnchorSelection]:
        refs = [primary]
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return refs
        for entry in value:
            if not isinstance(entry, Mapping):
                continue
            path_id = entry.get("path_id")
            anchor_id = entry.get("anchor_id")
            path_idx = self._selection_index(entry.get("path"), primary.path_idx)
            anchor_idx = self._selection_index(entry.get("anchor"), primary.anchor_idx)
            path_idx, anchor_idx = self._resolve_selection_indices_from_ids(
                obj,
                path_idx,
                anchor_idx,
                path_id=path_id if isinstance(path_id, str) else None,
                anchor_id=anchor_id if isinstance(anchor_id, str) else None,
            )
            ref = _AnchorSelection(path_idx, anchor_idx)
            if ref not in refs and self._anchor_ref_is_valid(obj, ref):
                refs.append(ref)
        return refs

    def _ensure_vector_layer_above(self, layer_id: str | None) -> str | None:
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        target_index = None
        layers = getattr(canvas, "_layers", None)
        if layers and layer_id:
            for idx, layer in enumerate(layers):
                if getattr(layer, "id", None) == layer_id:
                    target_index = idx + 1
                    break
        ref = None
        add_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer()
            except Exception:
                logger.debug("add_vector_layer failed during duplicate move", exc_info=True)
        new_layer_id = getattr(ref, "id", None) if ref else None
        if target_index is not None and hasattr(canvas, "move_layer") and new_layer_id:
            try:
                canvas.move_layer(
                    len(canvas._layers) - 1, min(target_index, len(canvas._layers) - 1)
                )
            except Exception:
                logger.debug("move_layer during duplicate move failed", exc_info=True)
        return new_layer_id


from .edit_plugin import VectorPathEditPlugin as VectorPathEditPlugin  # noqa: E402, PLC0414

PLUGIN_METADATA = VectorPathEditPlugin().metadata


def create_plugin() -> VectorPathEditPlugin:
    return VectorPathEditPlugin()
