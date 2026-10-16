"""
Basic rectangle selection tool plugin.

Implements selection mask updates via UpdateSelectionMaskCommand.
Supports boolean operations, feathering, and pixel-perfect snapping.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from ...core.commands import (
    Command,
    CutSelectionCommand,
    ModifySelectionMaskCommand,
    PasteSelectionCommand,
    SelectionOp,
    TransformSelectionCommand,
    UpdateSelectionMaskCommand,
    _affine_bounds,
    _canvas_dpi,
    _clip_bounds_to_canvas,
    _is_qimage_layer,
    _place_mask_on_canvas,
    _qimage_from_bgra,
    _resolve_target_layer,
    _set_selection_full_bounds,
    _supports_layer_expansion,
    _transform_payload_raster,
)
from ...core.events import Events
from ...core.geometry import Affine2D
from ...core.selection_clipboard import SelectionPayload
from ...host_api import HostAPI, StrokePoint
from ...plugins.sdk.base import PluginMetadata, PluginType
from ...plugins.sdk.handles import (
    HANDLE_CORNER_SIZE,
    HANDLE_DETECT_PAD,
    HANDLE_EDGE_SIZE,
    handle_anchor_for_rect,
    handle_position_for_rect,
    handle_positions_for_rect,
)
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    ContextWidgetSpec,
    PropertySpec,
    PropertyType,
    ToolActionGroup,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ...plugins.sdk.transform import (  # noqa: E402
    compute_pivot,
    compute_scale_factors,
    hit_test_handle_aabb,
)
from ...plugins.shape_manager.ops.selection_ops import rotate_object, scale_object
from ...plugins.vector_tools.common import (
    Anchor,
    VectorCreateCommand,
    VectorObject,
    VectorPath,
    VectorReplaceCommand,
    VectorStyle,
    copy_vector_object,
    ensure_vector_element_ids,
    make_vector_object,
)
from .free_selection_controller import FreeSelectionController
from .rect_raster_controller import (
    RasterSelectionController,
)
from .rect_raster_controller import RasterTransformDragState as _TransformDragState
from .rect_vector_controller import VectorSelectionController
from .selection_settings import SelectionSettings as SelectionSettings  # noqa: PLC0414
from .selection_transform_overlay import SelectionTransformOverlay

logger = logging.getLogger(__name__)

MIN_VECTOR_SCALE_SPAN = 4.0


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicSelectionRectTool", text, use_fallback=True)


class BasicSelectionRectTool:
    """Rectangle selection tool that updates selection mask via commands."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self.settings = SelectionSettings()
        self._raster_controller = RasterSelectionController(self)
        self._vector_controller = VectorSelectionController(self)
        self._free_selection_controller = FreeSelectionController(self)
        self._selection_overlay = SelectionTransformOverlay(self)
        self._start: StrokePoint | None = None
        self._current: StrokePoint | None = None

        # Snapshot of modifiers at the moment of press (determines Boolean Op)
        self._initial_modifiers: dict[str, bool] = {"shift": False, "alt": False}

        # Current modifiers during drag (determines Geometry: Square/Center)
        self._current_modifiers: dict[str, bool] = {"shift": False, "alt": False}

        self._pending_commands: list[Command] = []
        self._hover_cursor_set: bool = False
        self._ctrl_down = False
        self._press_pos: tuple[float, float] | None = None

    @property
    def _base_mask(self) -> Any | None:
        return self._raster_controller.state.base_mask

    @_base_mask.setter
    def _base_mask(self, value: Any | None) -> None:
        self._raster_controller.state.base_mask = value

    @property
    def _base_bounds(self) -> Any | None:
        return self._raster_controller.state.base_bounds

    @_base_bounds.setter
    def _base_bounds(self, value: Any | None) -> None:
        self._raster_controller.state.base_bounds = value

    @property
    def _base_full_bounds(self) -> Any | None:
        return self._raster_controller.state.base_full_bounds

    @_base_full_bounds.setter
    def _base_full_bounds(self, value: Any | None) -> None:
        self._raster_controller.state.base_full_bounds = value

    @property
    def _preview_enabled(self) -> bool:
        return self._raster_controller.state.preview_enabled

    @_preview_enabled.setter
    def _preview_enabled(self, value: bool) -> None:
        self._raster_controller.state.preview_enabled = bool(value)

    @property
    def _drag_mode(self) -> str:
        return self._raster_controller.state.drag_mode

    @_drag_mode.setter
    def _drag_mode(self, value: str) -> None:
        self._raster_controller.state.drag_mode = value

    @property
    def _drag_offset(self) -> tuple[float, float]:
        return self._raster_controller.state.drag_offset

    @_drag_offset.setter
    def _drag_offset(self, value: tuple[float, float]) -> None:
        self._raster_controller.state.drag_offset = value

    @property
    def _transform_active(self) -> bool:
        return self._raster_controller.transform.active

    @_transform_active.setter
    def _transform_active(self, value: bool) -> None:
        self._raster_controller.transform.active = bool(value)

    @property
    def _transform_payload(self) -> SelectionPayload | None:
        return self._raster_controller.transform.payload

    @_transform_payload.setter
    def _transform_payload(self, value: SelectionPayload | None) -> None:
        self._raster_controller.transform.payload = value

    @property
    def _transform_matrix(self) -> Affine2D | None:
        return self._raster_controller.transform.matrix

    @_transform_matrix.setter
    def _transform_matrix(self, value: Affine2D | None) -> None:
        self._raster_controller.transform.matrix = value

    @property
    def _transform_pivot_local(self) -> tuple[float, float]:
        return self._raster_controller.transform.pivot_local

    @_transform_pivot_local.setter
    def _transform_pivot_local(self, value: tuple[float, float]) -> None:
        self._raster_controller.transform.pivot_local = value

    @property
    def _transform_drag(self) -> _TransformDragState | None:
        return self._raster_controller.transform.drag

    @_transform_drag.setter
    def _transform_drag(self, value: _TransformDragState | None) -> None:
        self._raster_controller.transform.drag = value

    @property
    def _transform_mode_override(self) -> str | None:
        return self._raster_controller.transform.mode_override

    @_transform_mode_override.setter
    def _transform_mode_override(self, value: str | None) -> None:
        self._raster_controller.transform.mode_override = value

    @property
    def _transform_duplicate_latched(self) -> bool:
        return self._raster_controller.transform.duplicate_latched

    @_transform_duplicate_latched.setter
    def _transform_duplicate_latched(self, value: bool) -> None:
        self._raster_controller.transform.duplicate_latched = bool(value)

    @property
    def _transform_mask_mode(self) -> str:
        return self._raster_controller.transform.mask_mode

    @_transform_mask_mode.setter
    def _transform_mask_mode(self, value: str) -> None:
        self._raster_controller.transform.mask_mode = value

    @property
    def _transform_cut_command(self) -> CutSelectionCommand | None:
        return self._raster_controller.transform.cut_command

    @_transform_cut_command.setter
    def _transform_cut_command(self, value: CutSelectionCommand | None) -> None:
        self._raster_controller.transform.cut_command = value

    @property
    def _transform_base_mask(self) -> Any | None:
        return self._raster_controller.transform.base_mask

    @_transform_base_mask.setter
    def _transform_base_mask(self, value: Any | None) -> None:
        self._raster_controller.transform.base_mask = value

    @property
    def _transform_base_bounds(self) -> Any | None:
        return self._raster_controller.transform.base_bounds

    @_transform_base_bounds.setter
    def _transform_base_bounds(self, value: Any | None) -> None:
        self._raster_controller.transform.base_bounds = value

    @property
    def _transform_base_full_bounds(self) -> Any | None:
        return self._raster_controller.transform.base_full_bounds

    @_transform_base_full_bounds.setter
    def _transform_base_full_bounds(self, value: Any | None) -> None:
        self._raster_controller.transform.base_full_bounds = value

    @property
    def _vector_drag_mode(self) -> str:
        return self._vector_controller.state.drag_mode

    @_vector_drag_mode.setter
    def _vector_drag_mode(self, value: str) -> None:
        self._vector_controller.state.drag_mode = value

    @property
    def _vector_transform_mode_override(self) -> str | None:
        return self._vector_controller.state.transform_mode_override

    @_vector_transform_mode_override.setter
    def _vector_transform_mode_override(self, value: str | None) -> None:
        self._vector_controller.state.transform_mode_override = value

    @property
    def _vector_drag_items(self) -> list[dict[str, Any]]:
        return self._vector_controller.state.drag_items

    @_vector_drag_items.setter
    def _vector_drag_items(self, value: list[dict[str, Any]]) -> None:
        self._vector_controller.state.drag_items = value

    @property
    def _vector_drag_originals(self) -> list[tuple[str, str | None, VectorObject]]:
        return self._vector_controller.state.drag_originals

    @_vector_drag_originals.setter
    def _vector_drag_originals(self, value: list[tuple[str, str | None, VectorObject]]) -> None:
        self._vector_controller.state.drag_originals = value

    @property
    def _vector_drag_bounds(self) -> tuple[float, float, float, float] | None:
        return self._vector_controller.state.drag_bounds

    @_vector_drag_bounds.setter
    def _vector_drag_bounds(self, value: tuple[float, float, float, float] | None) -> None:
        self._vector_controller.state.drag_bounds = value

    @property
    def _vector_drag_start(self) -> tuple[float, float] | None:
        return self._vector_controller.state.drag_start

    @_vector_drag_start.setter
    def _vector_drag_start(self, value: tuple[float, float] | None) -> None:
        self._vector_controller.state.drag_start = value

    @property
    def _vector_drag_handle(self) -> str | None:
        return self._vector_controller.state.drag_handle

    @_vector_drag_handle.setter
    def _vector_drag_handle(self, value: str | None) -> None:
        self._vector_controller.state.drag_handle = value

    @property
    def _vector_drag_pivot(self) -> tuple[float, float] | None:
        return self._vector_controller.state.drag_pivot

    @_vector_drag_pivot.setter
    def _vector_drag_pivot(self, value: tuple[float, float] | None) -> None:
        self._vector_controller.state.drag_pivot = value

    @property
    def _vector_drag_press_vec(self) -> tuple[float, float] | None:
        return self._vector_controller.state.drag_press_vec

    @_vector_drag_press_vec.setter
    def _vector_drag_press_vec(self, value: tuple[float, float] | None) -> None:
        self._vector_controller.state.drag_press_vec = value

    @property
    def _vector_last_delta(self) -> tuple[float, float]:
        return self._vector_controller.state.last_delta

    @_vector_last_delta.setter
    def _vector_last_delta(self, value: tuple[float, float]) -> None:
        self._vector_controller.state.last_delta = value

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="loomstroke.basic.selection.rect",
            display_name=_ts("Selection (Rect)"),
            category="selection",
            cursor="crosshair",
            order=30,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "selection",
            tags=["selection", "rect"],
            group_id="selection.tools",
            group_label=_ts("Selection"),
            group_order=30,
            properties=[
                PropertySpec(
                    key="selection_mode",
                    display_name=_ts("Mode"),
                    type=PropertyType.ENUM,
                    enum_items=["Rectangle", "Vector", "Ellipse", "Lasso", "Polygon"],
                    default_value=self.settings.selection_mode,
                ),
                PropertySpec(
                    key="operation",
                    display_name=_ts("Operation"),
                    type=PropertyType.ENUM,
                    enum_items=["Replace", "Add", "Subtract", "Intersect"],
                    default_value=self.settings.operation,
                ),
                PropertySpec(
                    key="pixel_perfect",
                    display_name=_ts("Pixel Perfect"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.pixel_perfect,
                ),
                PropertySpec(
                    key="feather_enabled",
                    display_name=_ts("Feather"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.feather_enabled,
                ),
                PropertySpec(
                    key="feather_radius",
                    display_name=_ts("Feather Radius"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=128,
                    default_value=self.settings.feather_radius,
                ),
                PropertySpec(
                    key="mask_radius",
                    display_name=_ts("Mask Radius"),
                    type=PropertyType.INT,
                    min_value=0,
                    max_value=128,
                    default_value=self.settings.mask_radius,
                ),
                PropertySpec(
                    key="transform_x",
                    display_name=_ts("Transform X"),
                    type=PropertyType.FLOAT,
                    min_value=-10000.0,
                    max_value=10000.0,
                    step=1.0,
                    default_value=self.settings.transform_x,
                ),
                PropertySpec(
                    key="transform_y",
                    display_name=_ts("Transform Y"),
                    type=PropertyType.FLOAT,
                    min_value=-10000.0,
                    max_value=10000.0,
                    step=1.0,
                    default_value=self.settings.transform_y,
                ),
                PropertySpec(
                    key="transform_scale_x",
                    display_name=_ts("Scale X"),
                    type=PropertyType.FLOAT,
                    min_value=0.01,
                    max_value=100.0,
                    step=0.01,
                    default_value=self.settings.transform_scale_x,
                ),
                PropertySpec(
                    key="transform_scale_y",
                    display_name=_ts("Scale Y"),
                    type=PropertyType.FLOAT,
                    min_value=0.01,
                    max_value=100.0,
                    step=0.01,
                    default_value=self.settings.transform_scale_y,
                ),
                PropertySpec(
                    key="transform_rotation",
                    display_name=_ts("Rotation"),
                    type=PropertyType.FLOAT,
                    min_value=-180.0,
                    max_value=180.0,
                    step=1.0,
                    default_value=self.settings.transform_rotation,
                ),
            ],
            action_groups=[
                ToolActionGroup(
                    id="clipboard",
                    label=_ts("Clipboard"),
                    actions=[
                        ToolActionSpec(
                            key="selection.copy",
                            label=_ts("Copy"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.cut",
                            label=_ts("Cut"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.paste",
                            label=_ts("Paste"),
                            kind="button",
                            enabled_when="clipboard_not_empty",
                        ),
                    ],
                ),
                ToolActionGroup(
                    id="mask_ops",
                    label=_ts("Mask Ops"),
                    actions=[
                        ToolActionSpec(
                            key="selection.invert",
                            label=_ts("Invert"),
                            kind="button",
                        ),
                        ToolActionSpec(
                            key="selection.expand",
                            label=_ts("Expand"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.contract",
                            label=_ts("Contract"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.feather",
                            label=_ts("Feather"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.convert_to_vector_shape",
                            label=_ts("Convert"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                    ],
                ),
                ToolActionGroup(
                    id="transform",
                    label=_ts("Transform"),
                    actions=[
                        ToolActionSpec(
                            key="selection.transform.move",
                            label=_ts("Move"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.scale",
                            label=_ts("Scale"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.rotate",
                            label=_ts("Rotate"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.flip_h",
                            label=_ts("Flip H"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.flip_v",
                            label=_ts("Flip V"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.reset",
                            label=_ts("Reset"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.apply",
                            label=_ts("Apply"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                        ToolActionSpec(
                            key="selection.transform.cancel",
                            label=_ts("Cancel"),
                            kind="button",
                            enabled_when="has_selection",
                        ),
                    ],
                ),
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="selection_created",
            property_keys=["operation", "feather_enabled", "feather_radius"],
            custom_widgets=[
                ContextWidgetSpec(key="selection.transform.move", kind="button", label=_ts("Move")),
                ContextWidgetSpec(
                    key="selection.transform.scale",
                    kind="button",
                    label=_ts("Scale"),
                ),
                ContextWidgetSpec(
                    key="selection.transform.rotate",
                    kind="button",
                    label=_ts("Rotate"),
                ),
                ContextWidgetSpec(
                    key="selection.transform.flip_h",
                    kind="button",
                    label=_ts("Flip H"),
                ),
                ContextWidgetSpec(
                    key="selection.transform.flip_v",
                    kind="button",
                    label=_ts("Flip V"),
                ),
                ContextWidgetSpec(
                    key="selection.transform.apply",
                    kind="button",
                    label=_ts("Apply"),
                ),
                ContextWidgetSpec(
                    key="selection.transform.cancel",
                    kind="button",
                    label=_ts("Cancel"),
                ),
            ],
            anchor_mode="selection_bbox",
            mode="settings",
        )

    def activate(self, host: HostAPI) -> None:
        self.host_api = host

    def enable(self) -> None:
        self._selection_overlay.enabled = True
        self._reset_state()
        self._selection_overlay.refresh()

    def disable(self) -> None:
        self._selection_overlay.clear()
        self._cancel_transform_session()
        self._restore_base_selection(recompose=True)
        self._reset_state()
        self._pending_commands.clear()
        if self._hover_cursor_set:
            from ...plugins.sdk.cursors import apply_hover_cursor  # noqa: PLC0415

            self._hover_cursor_set = apply_hover_cursor(None, is_set=self._hover_cursor_set)

    def deactivate(self) -> None:
        self._selection_overlay.clear()
        self._cancel_transform_session()
        self._restore_base_selection(recompose=True)
        self._reset_state()
        self.host_api = None

    def on_pointer_down(  # noqa: PLR0912
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        if not self.host_api:
            return
        if self._is_vector_mode():
            self._vector_controller.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)
            return
        if self._free_selection_controller.enabled:
            self._free_selection_controller.down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)
            return
        self._raster_controller.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        points = list(pts)
        if self._is_vector_mode():
            self._vector_controller.on_pointer_move(points)
            return
        if self._free_selection_controller.enabled:
            self._free_selection_controller.move(points)
            return
        self._raster_controller.on_pointer_move(points)

    def on_pointer_up(self, pt: StrokePoint) -> None:  # noqa: PLR0912
        if self._is_vector_mode():
            self._vector_controller.on_pointer_up(pt)
            return
        if self._free_selection_controller.enabled:
            self._free_selection_controller.up(pt)
            return
        self._raster_controller.on_pointer_up(pt)

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        self._selection_overlay.refresh()
        if self._free_selection_controller.enabled:
            self._free_selection_controller.move([pt])
        from ...plugins.sdk.cursors import apply_hover_cursor, cursor_for_handle  # noqa: PLC0415

        cursor = None
        if self._is_vector_mode():
            bounds = self._vector_selection_bounds_from_canvas()
            handle_result = self._hit_test_vector_transform_handle(pt, bounds)
        else:
            handle_result = self._hit_test_transform_handle(pt)
        if handle_result is not None:
            cursor = cursor_for_handle(*handle_result)
        self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:  # noqa: PLR0911, PLR0912
        if self._free_selection_controller.key(key, pressed):
            return True
        key_lower = key.lower()

        if key_lower in {"shift", "alt"}:
            self._current_modifiers[key_lower] = pressed
            if self._start and self._current:
                self.on_pointer_move([self._current])

        if key_lower == "ctrl":
            self._ctrl_down = pressed
            return False

        if not pressed:
            return False
        if key_lower in {"enter", "return"} and self._transform_active:
            cmd = self._build_transform_commit_command()
            if cmd:
                self._pending_commands.append(cmd)
            return True
        if key_lower in {"esc", "escape"}:
            if self._is_vector_mode():
                if self._vector_drag_mode in {"move", "scale", "rotate"}:
                    self._cancel_vector_transform()
                else:
                    self._clear_vector_selection()
                self._finish_pointer()
                return True
            if self._transform_active:
                self._cancel_transform_session()
            else:
                self._queue_clear()
                self._finish_pointer()
            return True
        if key_lower == "t" and self._ctrl_down:
            self._begin_transform_mode("move")
            return True
        if key_lower == "d" and self._ctrl_down:
            self._queue_clear()
            return True
        return False

    def on_action(  # noqa: PLR0911, PLR0912
        self, action_key: str, payload: dict[str, Any] | None = None
    ) -> list[Command]:
        self._free_selection_controller.reset()
        del payload
        if action_key == "selection.copy":
            self._copy_selection_to_clipboard()
            return []
        if action_key == "selection.cut":
            return [CutSelectionCommand(None)]
        if action_key == "selection.paste":
            payload_obj = self._clipboard_payload()
            if payload_obj is None:
                return []
            layer_id = self._create_paste_layer()
            position = self._default_paste_position()
            return [PasteSelectionCommand(payload_obj, position=position, layer_id=layer_id)]
        if action_key == "selection.select_all":
            self._cancel_transform_session()
            cmd = self._select_all_command()
            return [cmd] if cmd else []
        if action_key == "selection.deselect":
            self._cancel_transform_session()
            self._clear_vector_selection()
            cmd = self._clear_selection_command()
            return [cmd]
        if action_key == "selection.vector_select_all":
            self._select_all_vectors()
            return []
        if action_key == "selection.convert_to_vector_shape":
            self._convert_selection_to_vector_shape()
            return []
        if action_key == "selection.invert":
            return [ModifySelectionMaskCommand("invert")]
        if action_key == "selection.expand":
            return [ModifySelectionMaskCommand("expand", self.settings.mask_radius)]
        if action_key == "selection.contract":
            return [ModifySelectionMaskCommand("contract", self.settings.mask_radius)]
        if action_key == "selection.feather":
            return [ModifySelectionMaskCommand("feather", self.settings.feather_radius)]
        if action_key == "selection.transform.move":
            if self._begin_vector_toolbar_mode("move"):
                return []
            self._begin_transform_mode("move")
            return []
        if action_key == "selection.transform.scale":
            if self._begin_vector_toolbar_mode("scale"):
                return []
            self._begin_transform_mode("scale")
            return []
        if action_key == "selection.transform.rotate":
            if self._begin_vector_toolbar_mode("rotate"):
                return []
            self._begin_transform_mode("rotate")
            return []
        if action_key == "selection.transform.flip_h":
            if self._apply_vector_selection_flip(-1.0, 1.0):
                return []
            self._apply_transform_flip(-1.0, 1.0)
            return []
        if action_key == "selection.transform.flip_v":
            if self._apply_vector_selection_flip(1.0, -1.0):
                return []
            self._apply_transform_flip(1.0, -1.0)
            return []
        if action_key == "selection.transform.reset":
            if self._cancel_vector_toolbar_transform():
                return []
            self._reset_transform()
            return []
        if action_key == "selection.transform.apply":
            if self._apply_vector_toolbar_transform():
                return []
            cmd = self._build_transform_commit_command()
            return [cmd] if cmd else []
        if action_key == "selection.transform.cancel":
            if self._cancel_vector_toolbar_transform():
                return []
            self._cancel_transform_session()
            return []
        return []

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        del delta, pt
        return False

    def _compute_rect_geometry(
        self, start: StrokePoint, end: StrokePoint, square: bool, center: bool
    ) -> tuple[int, int, int, int] | None:
        rect, _ = self._compute_rect_geometry_with_full(start, end, square, center)
        return rect

    def _compute_rect_geometry_with_full(  # noqa: PLR0915
        self,
        start: StrokePoint,
        end: StrokePoint,
        square: bool,
        center: bool,
    ) -> tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]:
        width, height = self._canvas_size()
        if width <= 0 or height <= 0:
            return None, None

        x0_f, y0_f = float(start.pos[0]), float(start.pos[1])
        x1_f, y1_f = float(end.pos[0]), float(end.pos[1])

        if center:
            dx = abs(x1_f - x0_f)
            dy = abs(y1_f - y0_f)
            if square:
                side = max(dx, dy)
                dx = dy = side
            left = x0_f - dx
            top = y0_f - dy
            right = x0_f + dx
            bottom = y0_f + dy
        else:
            left = min(x0_f, x1_f)
            top = min(y0_f, y1_f)
            right = max(x0_f, x1_f)
            bottom = max(y0_f, y1_f)

            if square:
                w_curr = abs(x1_f - x0_f)
                h_curr = abs(y1_f - y0_f)
                side = max(w_curr, h_curr)
                sign_x = 1 if x1_f >= x0_f else -1
                sign_y = 1 if y1_f >= y0_f else -1
                left = x0_f if sign_x > 0 else x0_f - side
                top = y0_f if sign_y > 0 else y0_f - side
                right = left + side
                bottom = top + side

        if self.settings.pixel_perfect:
            fx0 = round(left)
            fy0 = round(top)
            fx1 = round(right)
            fy1 = round(bottom)
        else:
            fx0 = math.floor(left)
            fy0 = math.floor(top)
            fx1 = math.ceil(right)
            fy1 = math.ceil(bottom)

        fw = fx1 - fx0
        fh = fy1 - fy0
        full_rect = None
        if fw > 0 and fh > 0:
            full_rect = (fx0, fy0, fw, fh)

        # Pixel perfect snapping or standard floor/ceil with canvas clamp.
        if self.settings.pixel_perfect:
            ix0 = max(0, fx0)
            iy0 = max(0, fy0)
            ix1 = min(width, fx1)
            iy1 = min(height, fy1)
        else:
            ix0 = max(0, fx0)
            iy0 = max(0, fy0)
            ix1 = min(width, fx1)
            iy1 = min(height, fy1)

        w_final = ix1 - ix0
        h_final = iy1 - iy0

        if w_final <= 0 or h_final <= 0:
            return None, full_rect

        return (ix0, iy0, w_final, h_final), full_rect

    def build_commands(self) -> list[Command]:
        self._selection_overlay.refresh()
        commands = list(self._pending_commands)
        self._pending_commands.clear()
        return commands

    def get_settings(self) -> SelectionSettings:
        return self.settings

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        self._free_selection_controller.reset()
        if isinstance(settings, dict):
            for k, v in settings.items():
                if hasattr(self.settings, k):
                    val = v
                    if k in {"pixel_perfect", "feather_enabled"}:
                        val = bool(v)
                    elif k in {"feather_radius", "mask_radius"}:
                        val = int(v)
                    elif k.startswith("transform_"):
                        val = float(v)
                    setattr(self.settings, k, val)
            return
        if isinstance(settings, SelectionSettings):
            self.settings = settings

    def set_setting(self, key: str, value: Any) -> None:
        if key == "selection_mode" and value != self.settings.selection_mode:
            self._free_selection_controller.reset()
        if hasattr(self.settings, key):
            if key.startswith("transform_"):
                self._apply_transform_setting(key, value)
                return
            val = value
            if key in ("pixel_perfect", "feather_enabled"):
                val = bool(value)
            elif key in ("feather_radius", "mask_radius"):
                val = int(value)
            setattr(self.settings, key, val)

    def create_options_widget(self) -> Any:
        return None

    def _queue_clear(self) -> None:
        self._pending_commands.append(UpdateSelectionMaskCommand(None, SelectionOp.REPLACE))

    def _clear_selection_command(self) -> UpdateSelectionMaskCommand:
        return UpdateSelectionMaskCommand(None, SelectionOp.REPLACE)

    def _select_all_command(self) -> UpdateSelectionMaskCommand | None:
        width, height = self._canvas_size()
        if width <= 0 or height <= 0:
            return None
        rect = (0, 0, width, height)
        return UpdateSelectionMaskCommand(rect, SelectionOp.REPLACE, full_bounds=rect)

    def _finish_pointer(self) -> None:
        if self.host_api:
            self.host_api.input.release_capture(self.descriptor().id)
        self._reset_state()

    def _reset_state(self) -> None:
        self._free_selection_controller.reset()
        self._start = None
        self._current = None
        self._press_pos = None
        self._raster_controller.reset_pointer_state()
        self._vector_controller.reset_pointer_state()

    def _capture_base_selection(self) -> None:
        if not self.host_api:
            self._base_mask = None
            self._base_bounds = None
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._base_mask = None
            self._base_bounds = None
            self._base_full_bounds = None
            return
        getter = getattr(canvas, "get_selection_mask", None)
        if callable(getter):
            self._base_mask = getter()
        else:
            self._base_mask = getattr(canvas, "_selection_mask", None)
        bounds_getter = getattr(canvas, "get_selection_bounds", None)
        if callable(bounds_getter):
            self._base_bounds = self._normalize_bounds(bounds_getter())
        else:
            self._base_bounds = self._normalize_bounds(getattr(canvas, "_selection_bounds", None))
        self._base_full_bounds = self._normalize_bounds(
            getattr(canvas, "_selection_bounds_full", None)
        )

    def _restore_base_selection(self, *, recompose: bool = False) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        mask = self._base_mask
        bounds = self._base_bounds

        # Guard against None state restoration if we never captured
        if mask is None and bounds is None:
            return

        setter = getattr(canvas, "set_selection_mask", None)
        if callable(setter):
            setter(mask, bounds)
            if hasattr(canvas, "_selection_bounds_full"):
                canvas._selection_bounds_full = self._base_full_bounds
            return

        # Fallback for direct attribute access (legacy)
        if hasattr(canvas, "_selection_mask"):
            canvas._selection_mask = mask
        if hasattr(canvas, "_selection_bounds"):
            canvas._selection_bounds = bounds
        if hasattr(canvas, "_selection_bounds_full"):
            canvas._selection_bounds_full = self._base_full_bounds
        if recompose:
            recomposer = getattr(canvas, "_recompose", None)
            if callable(recomposer):
                recomposer()

    def _clear_raster_selection(self, *, recompose: bool = False) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        setter = getattr(canvas, "set_selection_mask", None)
        if callable(setter):
            setter(None, None)
        else:
            if hasattr(canvas, "_selection_mask"):
                canvas._selection_mask = None
            if hasattr(canvas, "_selection_bounds"):
                canvas._selection_bounds = None
        if hasattr(canvas, "_selection_bounds_full"):
            canvas._selection_bounds_full = None
        if hasattr(canvas, "_selection_mask_full"):
            canvas._selection_mask_full = None
        if recompose:
            recomposer = getattr(canvas, "_recompose", None)
            if callable(recomposer):
                recomposer()

    def _op_from_modifiers(self, mods: dict[str, bool]) -> SelectionOp:
        shift = mods.get("shift", False)
        alt = mods.get("alt", False)
        if shift and alt:
            return SelectionOp.INTERSECT
        if shift:
            return SelectionOp.ADD
        if alt:
            return SelectionOp.SUBTRACT
        return self._op_from_setting()

    def _op_from_setting(self) -> SelectionOp:
        value = (self.settings.operation or "").strip().lower()
        for op in SelectionOp:
            if op.value == value:
                return op
        return SelectionOp.REPLACE

    def _clipboard_payload(self) -> SelectionPayload | None:
        if not self.host_api:
            return None
        clipboard = getattr(self.host_api, "selection_clipboard", None)
        if clipboard is None:
            return None
        getter = getattr(clipboard, "get", None)
        if callable(getter):
            return getter()
        return None

    def _copy_selection_to_clipboard(self) -> bool:
        payload = self._extract_selection_payload()
        if payload is None or not self.host_api:
            return False
        clipboard = getattr(self.host_api, "selection_clipboard", None)
        if clipboard is None:
            return False
        setter = getattr(clipboard, "set", None)
        if not callable(setter):
            return False
        setter(payload)
        return True

    def _extract_selection_payload(
        self,
    ) -> SelectionPayload | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return None

        raw_mask, raw_bounds = UpdateSelectionMaskCommand._read_selection(canvas)
        mask_np = UpdateSelectionMaskCommand._mask_to_numpy(raw_mask, width, height)
        bounds = self._normalize_bounds(raw_bounds) or UpdateSelectionMaskCommand._mask_bounds(
            mask_np
        )
        if bounds is None:
            return None
        x, y, w, h = bounds
        if w <= 0 or h <= 0:
            return None
        mask_patch = mask_np[y : y + h, x : x + w]
        if mask_patch.size == 0 or not np.any(mask_patch):
            return None

        layer = _resolve_target_layer(canvas, None)
        if layer is None:
            return None
        if getattr(layer, "layer_type", "raster") == "vector":
            return None

        full_bounds = self._normalize_bounds(getattr(canvas, "_selection_bounds_full", None))
        full_mask = getattr(canvas, "_selection_mask_full", None)
        full_mask_np: np.ndarray | None = None
        if full_mask is not None and full_bounds is not None:
            if isinstance(full_mask, np.ndarray):
                full_mask_np = full_mask.astype(np.uint8, copy=False)
            else:
                try:
                    from PySide6.QtGui import QImage  # noqa: PLC0415
                except Exception:
                    QImage = None
                if QImage is not None and isinstance(full_mask, QImage):
                    full_mask_np = UpdateSelectionMaskCommand._mask_to_numpy(
                        full_mask, full_mask.width(), full_mask.height()
                    )
            if full_mask_np is not None and (
                full_mask_np.shape[0] != int(full_bounds[3])
                or full_mask_np.shape[1] != int(full_bounds[2])
            ):
                full_mask_np = None

        use_full = False
        if (
            full_mask_np is not None
            and full_bounds is not None
            and _supports_layer_expansion(canvas, layer)
        ):
            fx, fy, fw, fh = full_bounds
            if fw > 0 and fh > 0 and np.any(full_mask_np) and _is_qimage_layer(layer):
                image_ref = getattr(layer, "image", None)
                if image_ref is not None and not image_ref.isNull():
                    offset_x = int(getattr(layer, "offset_x", 0))
                    offset_y = int(getattr(layer, "offset_y", 0))
                    if (
                        (fx - offset_x) >= 0
                        and (fy - offset_y) >= 0
                        and (fx - offset_x + fw) <= image_ref.width()
                        and (fy - offset_y + fh) <= image_ref.height()
                    ):
                        use_full = True
        if use_full:
            bounds = full_bounds
            x, y, w, h = bounds
            mask_patch = full_mask_np

        if _is_qimage_layer(layer):
            image = getattr(layer, "image", None)
            if image is None or image.isNull():
                return None
            offset_x = int(getattr(layer, "offset_x", 0))
            offset_y = int(getattr(layer, "offset_y", 0))
            payload_image = image.copy(int(x - offset_x), int(y - offset_y), int(w), int(h))
            payload_mask = UpdateSelectionMaskCommand._numpy_to_qimage(mask_patch, w, h)
            if payload_mask is None:
                return None
            return SelectionPayload(
                image=payload_image,
                mask=payload_mask,
                bounds=bounds,
                source_layer_id=getattr(layer, "id", None),
                dpi=_canvas_dpi(canvas),
            )

        buffer = getattr(layer, "buffer", None)
        if not isinstance(buffer, np.ndarray) or buffer.ndim != 3:
            return None
        region = buffer[y : y + h, x : x + w]
        if region.size == 0:
            return None
        payload_image = np.clip(region, 0.0, 255.0).astype(np.uint8)
        return SelectionPayload(
            image=payload_image,
            mask=mask_patch.copy(),
            bounds=bounds,
            source_layer_id=getattr(layer, "id", None),
            dpi=_canvas_dpi(canvas),
        )

    def _select_all_vectors(self) -> bool:
        items: list[dict[str, Any]] = []
        for layer_id, obj in self._vector_store():
            vec_id = getattr(obj, "id", None)
            if not isinstance(vec_id, str):
                continue
            items.append({"id": vec_id, "layer_id": layer_id})
        bounds = self._vector_selection_bounds_for_items(items)
        self._set_vector_selection(items, bounds)
        return bool(items)

    def _convert_selection_to_vector_shape(self) -> bool:
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None or not self._selection_exists():
            return False
        bounds = self._selection_bounds_for_vector_shape(canvas)
        if bounds is None:
            return False
        x, y, w, h = bounds
        if w <= 0 or h <= 0:
            return False
        layer_id = self._create_selection_shape_layer_id(canvas)
        if layer_id is None:
            return False

        shape = self._selection_bounds_to_vector_shape(float(x), float(y), float(w), float(h))
        cmd = VectorCreateCommand(shape, layer_id=layer_id, created_layer_id=layer_id)
        submit = getattr(canvas, "submit", None)
        success = False
        if callable(submit):
            try:
                success = bool(submit(cmd, merge_key=None))
            except Exception:
                logger.debug("selection to vector shape submit failed", exc_info=True)
        if not success:
            self._pending_commands.append(cmd)
            return False

        shape_bounds = (float(x), float(y), float(x + w), float(y + h))
        self._set_vector_selection([{"id": shape.id, "layer_id": layer_id}], shape_bounds)
        return True

    def _selection_bounds_for_vector_shape(
        self, canvas: object
    ) -> tuple[int, int, int, int] | None:
        full_bounds = self._normalize_bounds(getattr(canvas, "_selection_bounds_full", None))
        if full_bounds is not None and full_bounds[2] > 0 and full_bounds[3] > 0:
            return full_bounds
        bounds_getter = getattr(canvas, "get_selection_bounds", None)
        if callable(bounds_getter):
            try:
                return self._normalize_bounds(bounds_getter())
            except Exception:
                logger.debug("selection bounds getter failed", exc_info=True)
                return None
        return self._normalize_bounds(getattr(canvas, "_selection_bounds", None))

    def _create_selection_shape_layer_id(self, canvas: object) -> str | None:
        add_vector_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_vector_layer):
            try:
                ref = add_vector_layer(vector_kind="shape")
                layer_id = getattr(ref, "id", None)
                return str(layer_id) if layer_id else None
            except TypeError:
                logger.debug("add_vector_layer does not accept vector_kind", exc_info=True)
            except Exception:
                logger.debug("add_vector_layer failed", exc_info=True)

        add_layer = getattr(canvas, "add_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer("Shape Layer", layer_type="vector", vector_kind="shape")
                layer_id = getattr(ref, "id", None)
                return str(layer_id) if layer_id else None
            except TypeError:
                logger.debug("add_layer does not accept vector layer args", exc_info=True)
            except Exception:
                logger.debug("add_layer failed", exc_info=True)
        return None

    @staticmethod
    def _selection_bounds_to_vector_shape(
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> VectorObject:
        path = VectorPath(
            anchors=[
                Anchor(pos=(x, y)),
                Anchor(pos=(x + width, y)),
                Anchor(pos=(x + width, y + height)),
                Anchor(pos=(x, y + height)),
            ],
            closed=True,
        )
        return ensure_vector_element_ids(make_vector_object([path], VectorStyle()))

    @staticmethod
    def _normalize_bounds(bounds: Any | None) -> tuple[int, int, int, int] | None:
        if bounds is None:
            return None
        if hasattr(bounds, "x") and hasattr(bounds, "y"):
            return (int(bounds.x()), int(bounds.y()), int(bounds.width()), int(bounds.height()))
        if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
            return (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
        return None

    def _default_paste_position(self) -> tuple[int, int] | None:
        width, height = self._canvas_size()
        if width <= 0 or height <= 0:
            return None
        if self.settings.pixel_perfect:
            return (round(width / 2) + 8, round(height / 2) + 8)
        return (int(width / 2) + 8, int(height / 2) + 8)

    def _create_paste_layer(self) -> str | None:
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        add_layer = getattr(canvas, "add_layer", None)
        if not callable(add_layer):
            return None
        try:
            new_layer = add_layer("Pasted Layer")
        except Exception:
            return None
        layer_id = getattr(new_layer, "id", None)
        return str(layer_id) if layer_id else None

    def _selection_exists(self) -> bool:  # noqa: PLR0911
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        getter = getattr(canvas, "get_selection", None)
        if callable(getter):
            try:
                selection = getter()
            except Exception:
                selection = None
            if selection is None:
                return False
            bounds = getattr(selection, "bounds", None)
            return bool(bounds)
        bounds_getter = getattr(canvas, "get_selection_bounds", None)
        if callable(bounds_getter):
            try:
                bounds = bounds_getter()
            except Exception:
                bounds = None
        else:
            bounds = getattr(canvas, "_selection_bounds", None)
        if bounds is None:
            return False
        if hasattr(bounds, "isEmpty"):
            return not bounds.isEmpty()
        if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
            return bounds[2] > 0 and bounds[3] > 0
        return False

    def _is_outside_selection(self, pt: StrokePoint) -> bool:
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        mask_contains = self._selection_mask_contains(pt)
        if mask_contains is not None:
            return not mask_contains
        bounds = None
        getter = getattr(canvas, "get_selection_bounds", None)
        if callable(getter):
            try:
                bounds = getter()
            except Exception:
                bounds = None
        if bounds is None:
            bounds = getattr(canvas, "_selection_bounds", None)
        if bounds is None:
            return False
        if hasattr(bounds, "x") and hasattr(bounds, "y"):
            x0 = float(bounds.x())
            y0 = float(bounds.y())
            x1 = x0 + float(bounds.width())
            y1 = y0 + float(bounds.height())
        elif isinstance(bounds, (tuple, list)) and len(bounds) == 4:
            x0 = float(bounds[0])
            y0 = float(bounds[1])
            x1 = x0 + float(bounds[2])
            y1 = y0 + float(bounds[3])
        else:
            return False
        px = float(pt.pos[0])
        py = float(pt.pos[1])
        return not (x0 <= px <= x1 and y0 <= py <= y1)

    def _selection_mask_contains(self, pt: StrokePoint) -> bool | None:  # noqa: PLR0911, PLR0912
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        getter = getattr(canvas, "get_selection_mask", None)
        if callable(getter):
            try:
                mask = getter()
            except Exception:
                mask = None
        else:
            mask = getattr(canvas, "_selection_mask", None)
        if mask is None:
            return None
        x = math.floor(float(pt.pos[0]))
        y = math.floor(float(pt.pos[1]))
        if isinstance(mask, np.ndarray):
            if mask.ndim < 2:
                return None
            h, w = mask.shape[:2]
            if x < 0 or y < 0 or x >= w or y >= h:
                return False
            try:
                return int(mask[y, x]) > 0
            except Exception:
                return None
        if hasattr(mask, "width") and hasattr(mask, "height"):
            try:
                width = int(mask.width())
                height = int(mask.height())
            except Exception:
                return None
            if x < 0 or y < 0 or x >= width or y >= height:
                return False
            try:
                pixel_color = mask.pixelColor(x, y)
                return int(pixel_color.alpha()) > 0
            except Exception:
                try:
                    return int(mask.pixel(x, y)) != 0
                except Exception:
                    return None
        return None

    def _movement_exceeded_threshold(self, pt: StrokePoint) -> bool:
        if self._press_pos is None:
            return False
        dx = float(pt.pos[0]) - self._press_pos[0]
        dy = float(pt.pos[1]) - self._press_pos[1]
        return (dx * dx + dy * dy) >= 9.0

    def _canvas_size(self) -> tuple[int, int]:
        if not self.host_api:
            return 0, 0
        canvas = getattr(self.host_api, "canvas", None)
        width = getattr(canvas, "_width", None)
        height = getattr(canvas, "_height", None)
        if isinstance(width, int) and isinstance(height, int):
            return width, height
        config = getattr(canvas, "config", None)
        if config is not None and hasattr(config, "width") and hasattr(config, "height"):
            return int(config.width), int(config.height)
        size = getattr(canvas, "size", None)
        if callable(size):
            try:
                w, h = size()
                return int(w), int(h)
            except Exception:
                return 0, 0
        return 0, 0

    # --- Vector selection helpers -----------------------------------------

    def _is_vector_mode(self) -> bool:
        mode = (self.settings.selection_mode or "").strip().lower()
        return mode.startswith("vector")

    def _on_vector_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        self._vector_controller.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)

    def _on_vector_pointer_move(self, points: list[StrokePoint]) -> None:
        self._vector_controller.on_pointer_move(points)

    def _on_vector_pointer_up(self, pt: StrokePoint) -> None:
        self._vector_controller.on_pointer_up(pt)

    def _vector_store(self) -> list[tuple[str | None, Any]]:
        if not self.host_api:
            return []
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return []
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                return list(getter())
            except Exception:
                return []
        store = getattr(canvas, "_vector_objects", None)
        if isinstance(store, list):
            return [(None, obj) for obj in store]
        return []

    def _vector_selection_items(self) -> list[dict[str, Any]]:
        if not self.host_api:
            return []
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return []
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return []
        items = selection.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        vec_id = selection.get("id")
        if isinstance(vec_id, str):
            return [{"id": vec_id, "layer_id": selection.get("layer_id")}]
        return []

    def _vector_selection_bounds_from_canvas(self) -> tuple[float, float, float, float] | None:
        items = self._vector_selection_items()
        if not items:
            return None
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        selection = getattr(canvas, "_vector_selection", None)
        if isinstance(selection, dict):
            bounds = selection.get("transform_bounds")
            if isinstance(bounds, (tuple, list)) and len(bounds) == 4:
                try:
                    return (
                        float(bounds[0]),
                        float(bounds[1]),
                        float(bounds[2]),
                        float(bounds[3]),
                    )
                except (TypeError, ValueError):
                    logger.debug("transform_bounds parse failed", exc_info=True)
        return self._vector_selection_bounds_for_items(items)

    def _vector_selection_bounds_for_items(
        self, items: list[dict[str, Any]]
    ) -> tuple[float, float, float, float] | None:
        if not items:
            return None
        store = self._vector_store()
        if not store:
            return None
        bounds: tuple[float, float, float, float] | None = None
        lookup: dict[str, tuple[str | None, Any]] = {}
        for layer_id, obj in store:
            vec_id = getattr(obj, "id", None)
            if isinstance(vec_id, str):
                lookup[vec_id] = (layer_id, obj)
        for item in items:
            vec_id = item.get("id")
            if not isinstance(vec_id, str):
                continue
            entry = lookup.get(vec_id)
            if entry is None:
                continue
            obj = entry[1]
            bbox = self._vector_bbox(obj)
            if bbox is None:
                continue
            if bounds is None:
                bounds = bbox
            else:
                bounds = (
                    min(bounds[0], bbox[0]),
                    min(bounds[1], bbox[1]),
                    max(bounds[2], bbox[2]),
                    max(bounds[3], bbox[3]),
                )
        return bounds

    def _vector_bbox(self, obj: Any) -> tuple[float, float, float, float] | None:
        paths = getattr(obj, "paths", None) or []
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for path in paths:
            anchors = getattr(path, "anchors", None) or []
            for anchor in anchors:
                try:
                    ax = float(anchor.pos[0])
                    ay = float(anchor.pos[1])
                except Exception:
                    continue
                min_x = min(min_x, ax)
                min_y = min(min_y, ay)
                max_x = max(max_x, ax)
                max_y = max(max_y, ay)
                h_in = getattr(anchor, "h_in", None)
                h_out = getattr(anchor, "h_out", None)
                if h_in:
                    hx = ax + float(h_in[0])
                    hy = ay + float(h_in[1])
                    min_x = min(min_x, hx)
                    min_y = min(min_y, hy)
                    max_x = max(max_x, hx)
                    max_y = max(max_y, hy)
                if h_out:
                    hx = ax + float(h_out[0])
                    hy = ay + float(h_out[1])
                    min_x = min(min_x, hx)
                    min_y = min(min_y, hy)
                    max_x = max(max_x, hx)
                    max_y = max(max_y, hy)
        if min_x == float("inf"):
            return None
        return (min_x, min_y, max_x, max_y)

    def _vector_items_from_rect(self, rect: tuple[int, int, int, int]) -> list[dict[str, Any]]:
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return []
        x0 = float(x)
        y0 = float(y)
        x1 = x0 + float(w)
        y1 = y0 + float(h)
        items: list[dict[str, Any]] = []
        for layer_id, obj in self._vector_store():
            vec_id = getattr(obj, "id", None)
            if not isinstance(vec_id, str):
                continue
            bbox = self._vector_bbox(obj)
            if bbox is None:
                continue
            bx0, by0, bx1, by1 = bbox
            if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
                continue
            items.append({"id": vec_id, "layer_id": layer_id})
        return items

    def _vector_item_key(self, item: dict[str, Any]) -> tuple[str, str | None]:
        return (str(item.get("id")), item.get("layer_id"))

    def _vector_apply_op(
        self,
        existing: list[dict[str, Any]],
        picked: list[dict[str, Any]],
        op: SelectionOp,
    ) -> list[dict[str, Any]]:
        if op == SelectionOp.REPLACE:
            return picked
        existing_map = {self._vector_item_key(item): item for item in existing}
        picked_map = {self._vector_item_key(item): item for item in picked}
        if op == SelectionOp.ADD:
            merged = dict(existing_map)
            merged.update(picked_map)
            return list(merged.values())
        if op == SelectionOp.SUBTRACT:
            return [item for key, item in existing_map.items() if key not in picked_map]
        if op == SelectionOp.INTERSECT:
            return [item for key, item in existing_map.items() if key in picked_map]
        return picked

    def _set_vector_selection(
        self,
        items: list[dict[str, Any]],
        bounds: tuple[float, float, float, float] | None,
    ) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        if not items:
            if hasattr(canvas, "_vector_selection"):
                canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            self._notify_vector_overlay_changed(canvas)
            self._publish_vector_selection_changed(False, None)
            return
        overlay_layer_id = self._topmost_vector_selection_layer_id(items)
        canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
            "items": items,
            "mode": "multi",
            "transform_bounds": bounds,
            "overlay_layer_id": overlay_layer_id,
            "source": "selection_tool",
        }
        self._notify_vector_overlay_changed(canvas)
        self._publish_vector_selection_changed(True, bounds)

    def _set_vector_selection_preview_rect(self, rect: tuple[int, int, int, int] | None) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        selection = getattr(canvas, "_vector_selection", None)
        payload = dict(selection) if isinstance(selection, dict) else {}
        if rect is None:
            if "anchor_box_bounds" in payload:
                payload.pop("anchor_box_bounds", None)
                has_target = any(key in payload for key in ("id", "ids", "items"))
                canvas._vector_selection = payload if has_target else None  # type: ignore[attr-defined]
        else:
            x, y, w, h = rect
            payload["anchor_box_bounds"] = (
                float(x),
                float(y),
                float(x + w),
                float(y + h),
            )
            payload.setdefault("source", "selection_tool_preview")
            canvas._vector_selection = payload  # type: ignore[attr-defined]
        self._notify_vector_overlay_changed(canvas)

    def _notify_vector_overlay_changed(self, canvas: Any) -> None:
        notifier = getattr(canvas, "notify_vector_preview_changed", None)
        if callable(notifier):
            notifier()
            return
        recomposer = getattr(canvas, "_recompose", None)
        if callable(recomposer):
            recomposer()

    def _topmost_vector_selection_layer_id(self, items: list[dict[str, Any]]) -> str | None:
        selected = {self._vector_item_key(item) for item in items}
        overlay_layer_id: str | None = None
        for layer_id, obj in self._vector_store():
            vec_id = getattr(obj, "id", None)
            if not isinstance(vec_id, str):
                continue
            if (vec_id, layer_id) in selected:
                overlay_layer_id = layer_id
        return overlay_layer_id

    def _publish_vector_selection_changed(
        self,
        has_selection: bool,
        bounds: tuple[float, float, float, float] | None,
    ) -> None:
        if not self.host_api:
            return
        events = getattr(self.host_api, "events", None)
        if events is None:
            return
        payload: dict[str, Any] = {"has_selection": bool(has_selection), "kind": "vector"}
        if has_selection and bounds is not None:
            payload["bounds"] = (
                float(bounds[0]),
                float(bounds[1]),
                float(bounds[2] - bounds[0]),
                float(bounds[3] - bounds[1]),
            )
        emit = getattr(events, "emit", None)
        if callable(emit):
            emit(Events.SELECTION_CHANGED, payload)
            return
        publish = getattr(events, "publish", None)
        if callable(publish):
            publish(Events.SELECTION_CHANGED, payload)

    def _clear_vector_selection(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        if hasattr(canvas, "_vector_selection"):
            canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
        self._notify_vector_overlay_changed(canvas)

    def _begin_vector_toolbar_mode(self, mode: str) -> bool:
        if mode not in {"move", "scale", "rotate"}:
            return False
        if not self._vector_selection_items():
            return False
        if self._vector_selection_bounds_from_canvas() is None:
            return False
        self._vector_transform_mode_override = mode
        return True

    def _apply_vector_toolbar_transform(self) -> bool:
        if self._vector_drag_mode not in {"move", "scale", "rotate"}:
            if self._vector_transform_mode_override is not None:
                self._vector_transform_mode_override = None
                return True
            return False
        if self._current is None:
            return False
        self._finish_vector_transform(self._current)
        return True

    def _cancel_vector_toolbar_transform(self) -> bool:
        if self._vector_drag_mode in {"move", "scale", "rotate"}:
            self._cancel_vector_transform()
            return True
        if self._vector_transform_mode_override is not None:
            self._vector_transform_mode_override = None
            return True
        return False

    def _begin_vector_move(self, pt: StrokePoint) -> bool:
        return self._begin_vector_transform(pt, "move", "move-area", None)

    def _begin_vector_transform(
        self,
        pt: StrokePoint,
        mode: str,
        handle: str,
        bounds: tuple[float, float, float, float] | None,
    ) -> bool:
        items = self._vector_selection_items()
        if not items or not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        store = self._vector_store()
        if not store:
            return False
        lookup: dict[str, tuple[str | None, Any]] = {}
        for layer_id, obj in store:
            vec_id = getattr(obj, "id", None)
            if isinstance(vec_id, str):
                lookup[vec_id] = (layer_id, obj)
        originals: list[tuple[str, str | None, VectorObject]] = []
        valid_items: list[dict[str, Any]] = []
        for item in items:
            vec_id = item.get("id")
            if not isinstance(vec_id, str):
                continue
            entry = lookup.get(vec_id)
            if entry is None:
                continue
            layer_id, obj = entry
            try:
                original = copy_vector_object(obj)
            except Exception:
                continue
            originals.append((vec_id, layer_id, original))
            valid_items.append(item)
        if not originals:
            return False
        self._vector_drag_mode = mode
        self._vector_drag_items = valid_items
        self._vector_drag_originals = originals
        self._vector_drag_bounds = bounds or self._vector_selection_bounds_for_items(valid_items)
        self._vector_drag_start = (float(pt.pos[0]), float(pt.pos[1]))
        self._vector_drag_handle = handle
        self._vector_drag_pivot = None
        self._vector_drag_press_vec = None
        if self._vector_drag_bounds is not None and mode in {"scale", "rotate"}:
            if mode == "scale":
                pivot = compute_pivot(handle, self._vector_drag_bounds)
            else:
                x0, y0, x1, y1 = self._vector_drag_bounds
                pivot = ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
            self._vector_drag_pivot = pivot
            self._vector_drag_press_vec = (float(pt.pos[0]) - pivot[0], float(pt.pos[1]) - pivot[1])
        self._vector_last_delta = (0.0, 0.0)
        return True

    def _hit_test_vector_transform_handle(
        self,
        pt: StrokePoint,
        bounds: tuple[float, float, float, float] | None,
    ) -> tuple[str, str] | None:
        if bounds is None:
            return None
        x0, y0, x1, y1 = bounds
        px = float(pt.pos[0])
        py = float(pt.pos[1])
        half_edge = max(1.0, HANDLE_EDGE_SIZE * 0.5 - 1e-6)
        return hit_test_handle_aabb(
            px,
            py,
            (x0, y0, x1, y1),
            corner_half_extent=HANDLE_CORNER_SIZE * 0.5,
            edge_radius=half_edge,
            rotation_radius=half_edge + HANDLE_DETECT_PAD,
            corner_inclusive=False,
        )

    def _update_vector_transform_preview(self, pt: StrokePoint) -> None:  # noqa: PLR0912
        if not self._vector_drag_start or not self._vector_drag_originals:
            return
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        key = self._vector_transform_key(pt)
        if key == self._vector_last_delta:
            return
        self._vector_last_delta = key
        transformed: list[tuple[str, str | None, VectorObject]] = []
        for vec_id, layer_id, original in self._vector_drag_originals:
            transformed.append((vec_id, layer_id, self._transform_vector_object(original, pt)))
        replacer = getattr(canvas, "replace_vector_object", None)
        if callable(replacer):
            for vec_id, layer_id, obj in transformed:
                try:
                    replacer(vec_id, obj, layer_id=layer_id, recompose=False)
                except Exception:
                    continue
        else:
            store = getattr(canvas, "_vector_objects", None)
            if isinstance(store, list):
                id_to_index = {getattr(obj, "id", None): idx for idx, obj in enumerate(store)}
                for vec_id, _layer_id, obj in transformed:
                    idx = id_to_index.get(vec_id)
                    if idx is None:
                        continue
                    store[idx] = obj
        bounds = self._vector_bounds_for_objects([obj for _vec_id, _layer_id, obj in transformed])
        if bounds is not None:
            selection = getattr(canvas, "_vector_selection", None)
            if isinstance(selection, dict) and selection.get("items"):
                selection["transform_bounds"] = bounds
        self._notify_vector_overlay_changed(canvas)

    def _finish_vector_transform(self, pt: StrokePoint) -> None:
        if not self._vector_drag_start or not self._vector_drag_originals:
            self._finish_pointer()
            return
        if not self.host_api:
            self._finish_pointer()
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._finish_pointer()
            return
        key = self._vector_transform_key(pt)
        changed = self._vector_transform_changed(key)
        transformed = [
            (vec_id, layer_id, self._transform_vector_object(original, pt))
            for vec_id, layer_id, original in self._vector_drag_originals
        ]

        self._restore_vector_drag_originals()

        if changed:
            begin_tx = getattr(canvas, "begin_transaction", None)
            end_tx = getattr(canvas, "end_transaction", None)
            if callable(begin_tx) and callable(end_tx):
                begin_tx(self._vector_transaction_label())
            try:
                for vec_id, layer_id, obj in transformed:
                    cmd = VectorReplaceCommand(vec_id, obj, layer_id=layer_id)
                    try:
                        canvas.submit(cmd, merge_key=None)
                    except Exception:
                        self._pending_commands.append(cmd)
            finally:
                if callable(end_tx):
                    end_tx()

        bounds_objects = [obj for _vec_id, _layer_id, obj in transformed]
        bounds = self._vector_bounds_for_objects(bounds_objects)
        self._set_vector_selection(self._vector_drag_items, bounds)
        self._finish_pointer()

    def _apply_vector_selection_flip(self, fx: float, fy: float) -> bool:
        items = self._vector_selection_items()
        bounds = self._vector_selection_bounds_for_items(items)
        if not items or bounds is None or not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        pivot = ((bounds[0] + bounds[2]) * 0.5, (bounds[1] + bounds[3]) * 0.5)
        store = self._vector_store()
        updates: list[tuple[str, str | None, VectorObject]] = []
        for item in items:
            vec_id = item.get("id")
            if not isinstance(vec_id, str):
                continue
            layer_hint = item.get("layer_id")
            layer_hint = layer_hint if isinstance(layer_hint, str) else None
            for layer_id, obj in store:
                if layer_hint is not None and layer_id != layer_hint:
                    continue
                if str(getattr(obj, "id", "")) != vec_id:
                    continue
                flipped = scale_object(obj, pivot, fx, fy, keep_stroke_width=True)
                updates.append((vec_id, layer_id, flipped))
                break
        if not updates:
            return False
        label = "Flip Vector Selection Horizontal" if fx < 0.0 else "Flip Vector Selection Vertical"
        begin_tx = getattr(canvas, "begin_transaction", None)
        end_tx = getattr(canvas, "end_transaction", None)
        if callable(begin_tx) and callable(end_tx):
            begin_tx(label)
        try:
            submit = getattr(canvas, "submit", None)
            for vec_id, layer_id, obj in updates:
                cmd = VectorReplaceCommand(vec_id, obj, layer_id=layer_id)
                if callable(submit):
                    try:
                        submit(cmd, merge_key=None)
                    except Exception:
                        self._pending_commands.append(cmd)
                else:
                    self._pending_commands.append(cmd)
        finally:
            if callable(end_tx):
                end_tx()
        bounds = self._vector_bounds_for_objects([obj for _vec_id, _layer_id, obj in updates])
        self._set_vector_selection(items, bounds)
        return True

    def _cancel_vector_transform(self) -> None:
        self._restore_vector_drag_originals()
        self._finish_pointer()

    def _vector_transform_key(self, pt: StrokePoint) -> tuple[float, float]:
        mode = self._vector_drag_mode
        if mode == "move":
            dx = float(pt.pos[0]) - self._vector_drag_start[0]
            dy = float(pt.pos[1]) - self._vector_drag_start[1]
            if self.settings.pixel_perfect:
                dx = float(round(dx))
                dy = float(round(dy))
            return (dx, dy)
        if mode == "scale":
            return self._vector_scale_factors(pt)
        if mode == "rotate":
            return (self._vector_rotation_angle(pt), 0.0)
        return (0.0, 0.0)

    def _vector_transform_changed(self, key: tuple[float, float]) -> bool:
        if self._vector_drag_mode == "move":
            return key != (0.0, 0.0)
        if self._vector_drag_mode == "scale":
            return not (math.isclose(key[0], 1.0) and math.isclose(key[1], 1.0))
        if self._vector_drag_mode == "rotate":
            return not math.isclose(key[0], 0.0)
        return False

    def _vector_transaction_label(self) -> str:
        if self._vector_drag_mode == "scale":
            return "Scale Vector Selection"
        if self._vector_drag_mode == "rotate":
            return "Rotate Vector Selection"
        return "Move Vector Selection"

    def _transform_vector_object(self, obj: VectorObject, pt: StrokePoint) -> VectorObject:
        if self._vector_drag_mode == "move":
            dx, dy = self._vector_transform_key(pt)
            return self._translate_vector_object(obj, dx, dy)
        if self._vector_drag_mode == "scale" and self._vector_drag_pivot is not None:
            sx, sy = self._vector_scale_factors(pt)
            return scale_object(obj, self._vector_drag_pivot, sx, sy, keep_stroke_width=True)
        if self._vector_drag_mode == "rotate" and self._vector_drag_pivot is not None:
            return rotate_object(obj, self._vector_drag_pivot, self._vector_rotation_angle(pt))
        return obj

    def _vector_scale_factors(self, pt: StrokePoint) -> tuple[float, float]:
        bounds = self._vector_drag_bounds
        pivot = self._vector_drag_pivot
        handle = self._vector_drag_handle
        if bounds is None or pivot is None or not handle:
            return (1.0, 1.0)
        sx, sy = compute_scale_factors(
            handle=handle,
            pivot=pivot,
            press_vec=self._vector_drag_press_vec,
            current=(float(pt.pos[0]), float(pt.pos[1])),
            bbox=bounds,
            uniform=bool(self._current_modifiers.get("shift")) and handle.startswith("corner"),
        )
        width = max(1.0, bounds[2] - bounds[0])
        height = max(1.0, bounds[3] - bounds[1])
        sx = max(sx, MIN_VECTOR_SCALE_SPAN / width)
        sy = max(sy, MIN_VECTOR_SCALE_SPAN / height)
        if self._current_modifiers.get("shift") and handle.startswith("corner"):
            uniform = max(sx, sy)
            return (uniform, uniform)
        return (sx, sy)

    def _vector_rotation_angle(self, pt: StrokePoint) -> float:
        pivot = self._vector_drag_pivot
        press_vec = self._vector_drag_press_vec
        if pivot is None or press_vec is None:
            return 0.0
        current = (float(pt.pos[0]) - pivot[0], float(pt.pos[1]) - pivot[1])
        if math.hypot(*press_vec) < 1e-6 or math.hypot(*current) < 1e-6:
            return 0.0
        return math.atan2(current[1], current[0]) - math.atan2(press_vec[1], press_vec[0])

    def _vector_bounds_for_objects(
        self, objects: list[VectorObject]
    ) -> tuple[float, float, float, float] | None:
        bounds: tuple[float, float, float, float] | None = None
        for obj in objects:
            bbox = self._vector_bbox(obj)
            if bbox is None:
                continue
            if bounds is None:
                bounds = bbox
            else:
                bounds = (
                    min(bounds[0], bbox[0]),
                    min(bounds[1], bbox[1]),
                    max(bounds[2], bbox[2]),
                    max(bounds[3], bbox[3]),
                )
        return bounds

    def _restore_vector_drag_originals(self) -> None:
        if not self._vector_drag_originals or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        replacer = getattr(canvas, "replace_vector_object", None)
        if callable(replacer):
            for vec_id, layer_id, original in self._vector_drag_originals:
                try:
                    replacer(vec_id, original, layer_id=layer_id, recompose=False)
                except Exception:
                    continue
        else:
            store = getattr(canvas, "_vector_objects", None)
            if isinstance(store, list):
                id_to_index = {getattr(obj, "id", None): idx for idx, obj in enumerate(store)}
                for vec_id, _layer_id, original in self._vector_drag_originals:
                    idx = id_to_index.get(vec_id)
                    if idx is None:
                        continue
                    store[idx] = original
        if self._vector_drag_bounds is not None:
            selection = getattr(canvas, "_vector_selection", None)
            if isinstance(selection, dict) and selection.get("items"):
                selection["transform_bounds"] = self._vector_drag_bounds
        self._notify_vector_overlay_changed(canvas)

    def _translate_vector_object(self, obj: VectorObject, dx: float, dy: float) -> VectorObject:
        paths: list[VectorPath] = []
        for path in obj.paths:
            anchors: list[Anchor] = []
            for anchor in path.anchors:
                anchors.append(
                    Anchor(
                        pos=(anchor.pos[0] + dx, anchor.pos[1] + dy),
                        kind=anchor.kind,
                        h_in=anchor.h_in,
                        h_out=anchor.h_out,
                    )
                )
            paths.append(VectorPath(anchors=anchors, closed=path.closed))
        meta = dict(obj.meta)
        # Update parametric shape reference_bbox
        param_payload = meta.get("shape_manager_parametric")
        if isinstance(param_payload, Mapping):
            param_copy = dict(param_payload)
            ref_bbox = param_copy.get("reference_bbox")
            if isinstance(ref_bbox, (list, tuple)) and len(ref_bbox) == 4:
                try:
                    param_copy["reference_bbox"] = [
                        float(ref_bbox[0]) + dx,
                        float(ref_bbox[1]) + dy,
                        float(ref_bbox[2]) + dx,
                        float(ref_bbox[3]) + dy,
                    ]
                    meta["shape_manager_parametric"] = param_copy
                except (TypeError, ValueError):
                    logger.debug("parametric bbox translate failed", exc_info=True)
        return VectorObject(
            id=obj.id,
            paths=paths,
            style=obj.style,
            meta=meta,
            preview=obj.preview,
        )

    def _point_in_bounds(self, pt: StrokePoint, bounds: tuple[float, float, float, float]) -> bool:
        x0, y0, x1, y1 = bounds
        px = float(pt.pos[0])
        py = float(pt.pos[1])
        return x0 <= px <= x1 and y0 <= py <= y1

    # --- Transform helpers -------------------------------------------------

    def _begin_transform_mode(self, mode: str) -> None:
        if not self._ensure_transform_session():
            return
        self._transform_mode_override = mode
        self._update_transform_preview()

    def _ensure_transform_session(self) -> bool:
        if self._transform_active:
            return True
        payload = self._extract_selection_payload()
        if payload is None:
            self._notify_message("No selection to transform.")
            return False
        self._transform_payload = payload
        self._transform_pivot_local = (payload.bounds[2] * 0.5, payload.bounds[3] * 0.5)
        self._transform_matrix = Affine2D.translation(payload.bounds[0], payload.bounds[1])
        self._transform_mask_mode = "soft" if self.settings.feather_enabled else "binary"
        self._transform_duplicate_latched = False
        self._transform_mode_override = None
        self._transform_cut_command = None
        self._capture_transform_base_selection()
        self._transform_active = True
        if not self._apply_preview_cut():
            self._transform_active = False
            self._transform_payload = None
            self._transform_matrix = None
            return False
        self._sync_transform_properties()
        self._update_transform_preview()
        return True

    def _apply_preview_cut(self) -> bool:
        if not self.host_api or not self._transform_active:
            return False
        if self._transform_cut_command is not None:
            return True
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        cmd = CutSelectionCommand(None)
        if not cmd.apply(canvas):
            return False
        if cmd.payload is not None:
            self._transform_payload = cmd.payload
        self._transform_cut_command = cmd
        self._bump_preview_cut_texture_version(cmd)
        self._recompose_canvas()
        return True

    def _restore_preview_cut(self) -> None:
        if not self.host_api or not self._transform_cut_command:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        cmd = self._transform_cut_command
        cmd.revert(canvas)
        self._bump_preview_cut_texture_version(cmd)
        self._transform_cut_command = None
        self._recompose_canvas()

    def _bump_preview_cut_texture_version(self, cmd: CutSelectionCommand) -> None:
        layer = getattr(cmd, "_layer_ref", None)
        if layer is None:
            payload = getattr(cmd, "payload", None)
            layer_id = getattr(payload, "source_layer_id", None)
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            getter = getattr(canvas, "_get_layer_by_id", None)
            if isinstance(layer_id, str) and callable(getter):
                layer = getter(layer_id)
        if layer is None:
            return
        version = getattr(layer, "texture_version", None)
        if isinstance(version, int):
            layer.texture_version = version + 1

    def _capture_transform_base_selection(self) -> None:
        if not self.host_api:
            self._transform_base_mask = None
            self._transform_base_bounds = None
            self._transform_base_full_bounds = None
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._transform_base_mask = None
            self._transform_base_bounds = None
            self._transform_base_full_bounds = None
            return
        getter = getattr(canvas, "get_selection_mask", None)
        if callable(getter):
            self._transform_base_mask = getter()
        else:
            self._transform_base_mask = getattr(canvas, "_selection_mask", None)
        bounds_getter = getattr(canvas, "get_selection_bounds", None)
        if callable(bounds_getter):
            self._transform_base_bounds = self._normalize_bounds(bounds_getter())
        else:
            self._transform_base_bounds = self._normalize_bounds(
                getattr(canvas, "_selection_bounds", None)
            )
        self._transform_base_full_bounds = self._normalize_bounds(
            getattr(canvas, "_selection_bounds_full", None)
        )

    def _restore_transform_base_selection(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        mask = self._transform_base_mask
        bounds = self._transform_base_bounds
        setter = getattr(canvas, "set_selection_mask", None)
        if callable(setter):
            setter(mask, bounds)
        else:
            if hasattr(canvas, "_selection_mask"):
                canvas._selection_mask = mask
            if hasattr(canvas, "_selection_bounds"):
                canvas._selection_bounds = bounds
        _set_selection_full_bounds(canvas, self._transform_base_full_bounds)
        self._recompose_canvas()

    def _begin_transform_drag(  # noqa: PLR0912
        self,
        pt: StrokePoint,
        *,
        alt: bool,
        shift: bool,
        ctrl: bool,
    ) -> bool:
        if not self._transform_active or not self._transform_payload or not self._transform_matrix:
            return False
        hit = self._hit_test_transform_handle(pt)
        if self._transform_mode_override:
            mode = self._transform_mode_override
            if mode == "move":
                handle = "move-area"
            elif mode == "rotate":
                handle = "rotate"
            else:
                handle = "corner-br"
        else:
            if not hit:
                return False
            handle, mode = hit

        duplicate = bool(alt)
        if duplicate and self._transform_cut_command is not None:
            self._restore_preview_cut()
        elif (
            not duplicate and self._transform_cut_command is None and not self._apply_preview_cut()
        ):
            return False
        self._transform_duplicate_latched = duplicate

        pointer0 = (float(pt.pos[0]), float(pt.pos[1]))
        matrix0 = self._transform_matrix
        if mode == "rotate":
            anchor_local = self._transform_pivot_local
        else:
            anchor_local = self._anchor_for_handle(handle)
        anchor_canvas = matrix0.apply(anchor_local[0], anchor_local[1])
        v0 = None
        axis_x = None
        axis_y = None
        den_x = 1.0
        den_y = 1.0
        handle_local = None
        if mode == "rotate":
            v0 = (pointer0[0] - anchor_canvas[0], pointer0[1] - anchor_canvas[1])
        elif mode == "scale":
            handle_local = self._handle_local(handle)
            ax = matrix0.a
            ay = matrix0.c
            bx = matrix0.b
            by = matrix0.d
            axis_x = self._normalize_vec((ax, ay))
            axis_y = self._normalize_vec((bx, by))
            if handle_local is not None and axis_x and axis_y:
                hx, hy = matrix0.apply(handle_local[0], handle_local[1])
                h0 = (hx - anchor_canvas[0], hy - anchor_canvas[1])
                den_x = self._dot(h0, axis_x)
                den_y = self._dot(h0, axis_y)

        self._transform_drag = _TransformDragState(
            mode=mode,
            handle=handle,
            pointer0=pointer0,
            matrix0=matrix0,
            anchor_local=anchor_local,
            anchor_canvas=anchor_canvas,
            v0=v0,
            axis_x=axis_x,
            axis_y=axis_y,
            den_x=den_x,
            den_y=den_y,
            handle_local=handle_local,
            shift_locked=bool(shift),
            snap_override=bool(ctrl),
            duplicate=duplicate,
        )
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        return True

    def _update_transform_drag(self, pt: StrokePoint) -> None:  # noqa: PLR0912, PLR0915
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        if not self._transform_drag:
            return
        drag = self._transform_drag
        matrix0 = drag.matrix0
        cx, cy = float(pt.pos[0]), float(pt.pos[1])
        if drag.mode == "move":
            dx = cx - drag.pointer0[0]
            dy = cy - drag.pointer0[1]
            if self.settings.pixel_perfect and not drag.snap_override:
                dx = round(dx)
                dy = round(dy)
            # Grid / guide snap
            if self._is_snap_enabled():
                target_x = matrix0.tx + dx
                target_y = matrix0.ty + dy
                sw = sh = 0.0
                tb = self._transform_base_bounds
                if tb and len(tb) >= 4:
                    sw, sh = float(tb[2]), float(tb[3])
                sx, sy = self._snap_position(target_x, target_y, sw, sh)
                dx = sx - matrix0.tx
                dy = sy - matrix0.ty
            self._transform_matrix = Affine2D(
                matrix0.a,
                matrix0.b,
                matrix0.c,
                matrix0.d,
                matrix0.tx + dx,
                matrix0.ty + dy,
            )
        elif drag.mode == "rotate":
            v0 = drag.v0
            anchor = drag.anchor_canvas
            if v0 is None:
                return
            v1 = (cx - anchor[0], cy - anchor[1])
            if self._length(v0) < 1e-6 or self._length(v1) < 1e-6:
                return
            dtheta = math.atan2(self._cross(v0, v1), self._dot(v0, v1))
            shift_now = bool(
                QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            if shift_now:
                snap = math.radians(15.0)
                dtheta = round(dtheta / snap) * snap
            cos_a = math.cos(dtheta)
            sin_a = math.sin(dtheta)
            a = cos_a * matrix0.a - sin_a * matrix0.c
            b = cos_a * matrix0.b - sin_a * matrix0.d
            c = sin_a * matrix0.a + cos_a * matrix0.c
            d = sin_a * matrix0.b + cos_a * matrix0.d
            tx = cos_a * (matrix0.tx - anchor[0]) - sin_a * (matrix0.ty - anchor[1]) + anchor[0]
            ty = sin_a * (matrix0.tx - anchor[0]) + cos_a * (matrix0.ty - anchor[1]) + anchor[1]
            self._transform_matrix = Affine2D(a, b, c, d, tx, ty)
        elif drag.mode == "scale":
            axis_x = drag.axis_x
            axis_y = drag.axis_y
            if axis_x is None or axis_y is None:
                return
            v = (cx - drag.anchor_canvas[0], cy - drag.anchor_canvas[1])
            sx = self._dot(v, axis_x) / drag.den_x if abs(drag.den_x) > 1e-6 else 1.0
            sy = self._dot(v, axis_y) / drag.den_y if abs(drag.den_y) > 1e-6 else 1.0
            if drag.handle in ("edge-top", "edge-bottom"):
                sx = 1.0
            if drag.handle in ("edge-left", "edge-right"):
                sy = 1.0
            sign_x = -1.0 if sx < 0 else 1.0
            sign_y = -1.0 if sy < 0 else 1.0
            shift_now = bool(
                QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            if shift_now and drag.handle.startswith("corner"):
                s = max(abs(sx), abs(sy))
                sx = sign_x * s
                sy = sign_y * s
            sx = sign_x * max(abs(sx), 1e-3)
            sy = sign_y * max(abs(sy), 1e-3)
            a = matrix0.a * sx
            b = matrix0.b * sy
            c = matrix0.c * sx
            d = matrix0.d * sy
            tx = drag.anchor_canvas[0] - (a * drag.anchor_local[0] + b * drag.anchor_local[1])
            ty = drag.anchor_canvas[1] - (c * drag.anchor_local[0] + d * drag.anchor_local[1])
            self._transform_matrix = Affine2D(a, b, c, d, tx, ty)
        else:
            return
        self._update_transform_preview()

    def _finish_transform_drag(self) -> None:
        if self.host_api:
            self.host_api.input.release_capture(self.descriptor().id)
        self._transform_drag = None
        self._transform_mode_override = None

    def _cancel_transform_session(self) -> None:
        if not self._transform_active:
            return
        if self._transform_cut_command is not None:
            self._restore_preview_cut()
        else:
            self._restore_transform_base_selection()
        self._clear_transform_preview()
        self._transform_active = False
        self._transform_payload = None
        self._transform_matrix = None
        self._transform_drag = None
        self._transform_mode_override = None
        self._transform_duplicate_latched = False
        self._transform_cut_command = None

    def _build_transform_commit_command(self) -> TransformSelectionCommand | None:
        if not self._transform_active or not self._transform_payload or not self._transform_matrix:
            return None
        if self._transform_cut_command is not None:
            self._restore_preview_cut()
        else:
            self._restore_transform_base_selection()
        self._clear_transform_preview()
        cmd = TransformSelectionCommand(
            self._transform_payload,
            self._transform_matrix,
            duplicate=self._transform_duplicate_latched,
            mask_mode=self._transform_mask_mode,
        )
        self._transform_active = False
        self._transform_payload = None
        self._transform_matrix = None
        self._transform_drag = None
        self._transform_mode_override = None
        self._transform_duplicate_latched = False
        self._transform_cut_command = None
        return cmd

    def _apply_transform_flip(self, fx: float, fy: float) -> None:
        if not self._ensure_transform_session() or not self._transform_matrix:
            return
        pivot = self._transform_matrix.apply(
            self._transform_pivot_local[0], self._transform_pivot_local[1]
        )
        matrix0 = self._transform_matrix
        a = fx * matrix0.a
        b = fx * matrix0.b
        c = fy * matrix0.c
        d = fy * matrix0.d
        tx = fx * (matrix0.tx - pivot[0]) + pivot[0]
        ty = fy * (matrix0.ty - pivot[1]) + pivot[1]
        self._transform_matrix = Affine2D(a, b, c, d, tx, ty)
        self._update_transform_preview()

    def _reset_transform(self) -> None:
        if not self._ensure_transform_session() or not self._transform_payload:
            return
        self._transform_matrix = Affine2D.translation(
            self._transform_payload.bounds[0], self._transform_payload.bounds[1]
        )
        self._update_transform_preview()

    def _apply_transform_setting(self, key: str, value: Any) -> None:
        if not hasattr(self.settings, key):
            return
        try:
            val = float(value)
        except (TypeError, ValueError):
            return
        setattr(self.settings, key, val)
        if not self._ensure_transform_session() or not self._transform_matrix:
            return
        matrix0 = self._transform_matrix
        if key in {"transform_x", "transform_y"}:
            tx = val if key == "transform_x" else matrix0.tx
            ty = val if key == "transform_y" else matrix0.ty
            if self.settings.pixel_perfect:
                tx = round(tx)
                ty = round(ty)
            self._transform_matrix = Affine2D(matrix0.a, matrix0.b, matrix0.c, matrix0.d, tx, ty)
        else:
            pivot_canvas = matrix0.apply(
                self._transform_pivot_local[0], self._transform_pivot_local[1]
            )
            scale_x = float(self.settings.transform_scale_x)
            scale_y = float(self.settings.transform_scale_y)
            theta = math.radians(float(self.settings.transform_rotation))
            cos_a = math.cos(theta)
            sin_a = math.sin(theta)
            a = cos_a * scale_x
            b = -sin_a * scale_y
            c = sin_a * scale_x
            d = cos_a * scale_y
            tx = pivot_canvas[0] - (
                a * self._transform_pivot_local[0] + b * self._transform_pivot_local[1]
            )
            ty = pivot_canvas[1] - (
                c * self._transform_pivot_local[0] + d * self._transform_pivot_local[1]
            )
            self._transform_matrix = Affine2D(a, b, c, d, tx, ty)
        self._update_transform_preview()

    def _update_transform_preview_qt(  # noqa: PLR0911
        self, canvas: Any, width: int, height: int
    ) -> bool:
        payload = self._transform_payload
        transform = self._transform_matrix
        if payload is None or transform is None:
            return False
        try:
            from PySide6.QtCore import Qt  # noqa: PLC0415
            from PySide6.QtGui import QImage, QPainter, QTransform  # noqa: PLC0415
        except Exception:
            return False
        if not isinstance(payload.image, QImage) or not isinstance(payload.mask, QImage):
            return False
        if payload.image.isNull() or payload.mask.isNull():
            return False
        bounds = _affine_bounds(transform, payload.mask.width(), payload.mask.height())
        if bounds is None:
            return False
        clipped_bounds = _clip_bounds_to_canvas(canvas, bounds)
        if clipped_bounds is None:
            return False
        dest_x, dest_y, dest_w, dest_h = clipped_bounds

        image_source = payload.image
        if image_source.format() != QImage.Format.Format_ARGB32_Premultiplied:
            image_source = image_source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        # Apply selection mask so only selected pixels are visible during drag
        import numpy as np  # noqa: PLC0415

        mask_src = payload.mask
        if mask_src.format() != QImage.Format.Format_Alpha8:
            mask_src = mask_src.convertToFormat(QImage.Format.Format_Alpha8)
        mw, mh = mask_src.width(), mask_src.height()
        mbpl = mask_src.bytesPerLine()
        mptr = mask_src.bits()
        if mptr is not None:
            if hasattr(mptr, "setsize"):
                mptr.setsize(mbpl * mh)
            mask_arr = np.frombuffer(bytes(mptr), dtype=np.uint8).reshape(mh, mbpl)[:, :mw].copy()
        else:
            mask_arr = np.full((mh, mw), 255, dtype=np.uint8)

        iw, ih = image_source.width(), image_source.height()
        ibpl = image_source.bytesPerLine()
        iptr = image_source.bits()
        if iptr is not None:
            if hasattr(iptr, "setsize"):
                iptr.setsize(ibpl * ih)
            img_arr = np.frombuffer(bytes(iptr), dtype=np.uint8).reshape(ih, ibpl)
            img_arr = img_arr[:, : iw * 4].reshape(ih, iw, 4).copy()
        else:
            img_arr = np.zeros((ih, iw, 4), dtype=np.uint8)

        mch = min(ih, mh)
        mcw = min(iw, mw)
        mask_f = mask_arr[:mch, :mcw].astype(np.float32) * (1.0 / 255.0)
        img_arr[:mch, :mcw] = (
            img_arr[:mch, :mcw].astype(np.float32) * mask_f[:, :, np.newaxis]
        ).astype(np.uint8)
        if ih > mch:
            img_arr[mch:, :, :] = 0
        if iw > mcw:
            img_arr[:, mcw:, :] = 0

        masked_source = QImage(
            img_arr.data,
            iw,
            ih,
            iw * 4,
            QImage.Format.Format_ARGB32_Premultiplied,
        ).copy()

        # GPU fast path hook: retired GL implementation returns None and CPU fallback continues.
        preview_image = None
        warp_gpu = getattr(canvas, "warp_qimage_gpu", None)
        if callable(warp_gpu):
            preview_image = warp_gpu(
                masked_source,
                dest_w,
                dest_h,
                (
                    transform.a,
                    transform.b,
                    transform.c,
                    transform.d,
                    transform.tx - dest_x,
                    transform.ty - dest_y,
                ),
                True,
            )
        if preview_image is None:
            preview_image = QImage(dest_w, dest_h, QImage.Format.Format_ARGB32_Premultiplied)
            preview_image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(preview_image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setTransform(
                QTransform(
                    transform.a,
                    transform.c,
                    transform.b,
                    transform.d,
                    transform.tx - dest_x,
                    transform.ty - dest_y,
                )
            )
            painter.drawImage(0, 0, masked_source)
            painter.end()

        mask_source = payload.mask
        if mask_source.format() != QImage.Format.Format_Alpha8:
            mask_source = mask_source.convertToFormat(QImage.Format.Format_Alpha8)

        # GPU fast path: warp mask as ARGB then extract alpha channel
        mask_full = None
        soft_mask = self._transform_mask_mode == "soft"
        if callable(warp_gpu):
            mask_argb = mask_source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            warped_argb = warp_gpu(
                mask_argb,
                width,
                height,
                (
                    transform.a,
                    transform.b,
                    transform.c,
                    transform.d,
                    transform.tx,
                    transform.ty,
                ),
                soft_mask,
            )
            if warped_argb is not None:
                warped_argb = warped_argb.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                mbpl = warped_argb.bytesPerLine()
                mptr = warped_argb.bits()
                if mptr is not None:
                    if hasattr(mptr, "setsize"):
                        mptr.setsize(mbpl * height)
                    argb_arr = (
                        np.frombuffer(bytes(mptr), dtype=np.uint8)
                        .reshape(height, mbpl)[:, : width * 4]
                        .reshape(height, width, 4)
                    )
                    # Format_ARGB32_Premultiplied on little-endian: B,G,R,A
                    alpha_arr = np.ascontiguousarray(argb_arr[:, :, 3])
                    if not soft_mask:
                        alpha_arr = np.where(alpha_arr > 127, 255, 0).astype(np.uint8)
                    mask_full = QImage(
                        alpha_arr.data,
                        width,
                        height,
                        width,
                        QImage.Format.Format_Alpha8,
                    ).copy()
        if mask_full is None:
            mask_full = QImage(width, height, QImage.Format.Format_Alpha8)
            mask_full.fill(0)
            painter = QPainter(mask_full)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.setRenderHint(
                QPainter.RenderHint.SmoothPixmapTransform,
                soft_mask,
            )
            painter.setTransform(
                QTransform(
                    transform.a,
                    transform.c,
                    transform.b,
                    transform.d,
                    transform.tx,
                    transform.ty,
                )
            )
            painter.drawImage(0, 0, mask_source)
            painter.end()

        UpdateSelectionMaskCommand._set_selection(canvas, mask_full, clipped_bounds)
        _set_selection_full_bounds(canvas, bounds)
        canvas._selection_preview_image = preview_image
        canvas._selection_preview_bounds = clipped_bounds
        self._recompose_canvas()
        return True

    def _update_transform_preview(self) -> None:
        if not self._transform_active or not self._transform_payload or not self._transform_matrix:
            return
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return
        if self._update_transform_preview_qt(canvas, width, height):
            self._sync_transform_properties()
            return

        premultiplied = _is_qimage_layer(_resolve_target_layer(canvas, None))
        transformed = _transform_payload_raster(
            self._transform_payload,
            self._transform_matrix,
            premultiplied=premultiplied,
            mask_mode=self._transform_mask_mode,
            canvas_size=(width, height),
        )
        if transformed is None:
            return
        dest_image, dest_mask, dest_bounds = transformed
        mask_full, bounds, _ = _place_mask_on_canvas(
            dest_mask,
            position=(dest_bounds[0], dest_bounds[1]),
            width=width,
            height=height,
        )
        if bounds is None:
            UpdateSelectionMaskCommand._set_selection(canvas, None, None)
            _set_selection_full_bounds(canvas, None)
        else:
            canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
                canvas, mask_full, width, height
            )
            UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, bounds)
            _set_selection_full_bounds(canvas, dest_bounds)

        preview_image = None
        if premultiplied:
            import numpy as np  # noqa: PLC0415

            mask_f = dest_mask.astype(np.float32) * (1.0 / 255.0)
            dest_image = (dest_image.astype(np.float32) * mask_f[:, :, np.newaxis]).astype(np.uint8)
            preview_image = _qimage_from_bgra(dest_image, premultiplied=True)
        canvas._selection_preview_image = preview_image
        canvas._selection_preview_bounds = dest_bounds
        self._recompose_canvas()
        self._sync_transform_properties()

    def _clear_transform_preview(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        canvas._selection_preview_image = None
        canvas._selection_preview_bounds = None
        self._recompose_canvas()

    def _sync_transform_properties(self) -> None:
        if not self._transform_matrix:
            return
        tx, ty, sx, sy, rot = self._decompose_transform(self._transform_matrix)
        self.settings.transform_x = tx
        self.settings.transform_y = ty
        self.settings.transform_scale_x = sx
        self.settings.transform_scale_y = sy
        self.settings.transform_rotation = rot
        host = self.host_api
        if not host:
            return
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        sync_tool_setting = (
            getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        )
        if not callable(sync_tool_setting):
            return
        tool_id = self.descriptor().id
        sync_tool_setting(tool_id, "transform_x", tx)
        sync_tool_setting(tool_id, "transform_y", ty)
        sync_tool_setting(tool_id, "transform_scale_x", sx)
        sync_tool_setting(tool_id, "transform_scale_y", sy)
        sync_tool_setting(tool_id, "transform_rotation", rot)

    @staticmethod
    def _decompose_transform(transform: Affine2D) -> tuple[float, float, float, float, float]:
        scale_x = math.hypot(transform.a, transform.c)
        rotation = math.degrees(math.atan2(transform.c, transform.a)) if scale_x > 0 else 0.0
        det = transform.a * transform.d - transform.b * transform.c
        scale_y = det / scale_x if scale_x > 0 else 1.0
        return (transform.tx, transform.ty, scale_x, scale_y, rotation)

    def _hit_test_transform_handle(self, pt: StrokePoint) -> tuple[str, str] | None:
        return self._selection_overlay.hit_test(pt)

    def _handle_positions(self) -> dict[str, tuple[float, float]] | None:
        if not self._transform_payload or not self._transform_matrix:
            return None
        w = float(self._transform_payload.bounds[2])
        h = float(self._transform_payload.bounds[3])
        if w <= 0.0 or h <= 0.0:
            return None
        t = self._transform_matrix

        return {
            name: t.apply(x, y)
            for name, (x, y) in handle_positions_for_rect(
                (0.0, 0.0, w, h), include_rotate=True
            ).items()
        }

    def _point_inside_obb(self, px: float, py: float) -> bool:
        if not self._transform_matrix or not self._transform_payload:
            return False
        inv = self._transform_matrix.inverse()
        if inv is None:
            return False
        u, v = inv.apply(px, py)
        w = float(self._transform_payload.bounds[2])
        h = float(self._transform_payload.bounds[3])
        return 0.0 <= u <= w and 0.0 <= v <= h

    def _anchor_for_handle(self, handle: str) -> tuple[float, float]:  # noqa: PLR0911
        if not self._transform_payload:
            return (0.0, 0.0)
        w = float(self._transform_payload.bounds[2])
        h = float(self._transform_payload.bounds[3])
        return handle_anchor_for_rect(handle, (0.0, 0.0, w, h))

    def _handle_local(self, handle: str) -> tuple[float, float] | None:
        if not self._transform_payload:
            return None
        w = float(self._transform_payload.bounds[2])
        h = float(self._transform_payload.bounds[3])
        return handle_position_for_rect(handle, (0.0, 0.0, w, h))

    @staticmethod
    def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1]

    @staticmethod
    def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[1] - a[1] * b[0]

    @staticmethod
    def _length(v: tuple[float, float]) -> float:
        return math.hypot(v[0], v[1])

    @staticmethod
    def _normalize_vec(v: tuple[float, float]) -> tuple[float, float] | None:
        length = math.hypot(v[0], v[1])
        if length <= 1e-8:
            return None
        return (v[0] / length, v[1] / length)

    def _notify_message(self, message: str) -> None:
        ui = getattr(self.host_api, "ui", None) if self.host_api else None
        if ui and hasattr(ui, "show_message"):
            try:
                ui.show_message(message)
            except Exception:
                return

    def _recompose_canvas(self) -> None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        recomposer = getattr(canvas, "_recompose", None)
        if callable(recomposer):
            recomposer()
            return
        invalidator = getattr(canvas, "_invalidate_composite", None)
        if callable(invalidator):
            invalidator()

    # ------------------------------------------------------------------
    # Snap helpers — delegates to sdk.snap
    # ------------------------------------------------------------------

    def _snap_position(
        self, x: float, y: float, w: float = 0.0, h: float = 0.0
    ) -> tuple[float, float]:
        from ...plugins.sdk.snap import snap_position  # noqa: PLC0415

        return snap_position(self.host_api, x, y, w, h)

    def _is_snap_enabled(self) -> bool:
        from ...plugins.sdk.snap import is_snap_enabled  # noqa: PLC0415

        return is_snap_enabled(self.host_api)


PLUGIN_METADATA = PluginMetadata(
    id="loomstroke.basic_selection",
    name=_ts("Basic Selection"),
    version="1.0.0",
    description=_ts("Rectangle selection tool"),
    author="LoomStroke Team",
    types=[PluginType.TOOL],
)


def create_plugin() -> BasicSelectionPlugin:
    return BasicSelectionPlugin()


from .selection_plugin import BasicSelectionPlugin as BasicSelectionPlugin  # noqa: E402, PLC0414
