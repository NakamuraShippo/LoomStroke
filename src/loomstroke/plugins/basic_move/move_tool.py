"""
Basic move tool plugin.

Moves active raster layers and supports direct vector/text object dragging.
"""

from __future__ import annotations

import contextlib
import logging
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...core.commands import (
    CutSelectionCommand,
    LayerTransform,
    MoveLayerCommand,
    TransformLayerCommand,
    TransformSelectionCommand,
    UpdateSelectionMaskCommand,
    _affine_bounds,
    _bump_layer_mask_version,
    _clip_bounds_to_canvas,
    _is_qimage_layer,
    _layer_mask_to_local,
    _layer_transform,
    _mask_size_matches_layer_image,
    _place_mask_on_canvas,
    _resolve_target_layer,
    _set_selection_full_bounds,
    _transform_payload_raster,
)
from ...core.geometry import Affine2D
from ...core.selection_clipboard import SelectionPayload
from ...host_api import HostAPI, StrokePoint
from ...plugins.sdk.base import PluginBase, PluginMetadata, PluginType
from ...plugins.sdk.tool import (
    ContextToolbarSpec,
    PropertySpec,
    PropertyType,
    ToolDescriptor,
    ToolMetadata,
    ToolSettings,
)
from ...plugins.sdk.transform import (  # noqa: E402
    compute_pivot,
    compute_scale_factors,
    hit_test_handle_aabb,
)
from ...plugins.text_tool.common import (
    VERTICAL_FLOW_LEFT,
    TextToolSettings,
    copy_text_tool_settings,
    extract_text_content,
    extract_word_wrap,
    normalize_vertical_flow,
)
from ...plugins.text_tool.editing_metadata import replace_edited_text
from ...plugins.vector_tools.common import (
    Anchor,
    VectorObject,
    VectorPath,
    VectorReplaceCommand,
    copy_vector_object,
)
from ..common.vector_hit_test_geometry import VectorHitTestGeometry
from .comic_balloon_move import ComicBalloonMove
from .comic_panel_move import ComicPanelMove
from .layer_transform_controller import (
    LayerTransformController,
)
from .layer_transform_controller import LayerTransformState as _LayerTransformState

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("BasicMoveTool", text, use_fallback=True)


@dataclass
class MoveSettings(ToolSettings):
    """Settings for the move tool."""

    pixel_perfect: bool = True
    grid_snap: bool = False
    grid_size: int = 32


@dataclass(slots=True)
class _VectorHit:
    vec_id: str
    layer_id: str | None
    obj: Any
    bounds: tuple[float, float, float, float]
    is_text: bool


@dataclass(slots=True)
class _VectorTransformState:
    hit: _VectorHit
    handle: str
    mode: str  # "scale" | "rotate"
    press_point: tuple[float, float]
    pivot: tuple[float, float]
    press_vec: tuple[float, float] | None
    start_bounds: tuple[float, float, float, float]
    original: VectorObject
    text_settings: TextToolSettings | None
    text_content: str
    word_wrap: bool
    text_rect: tuple[float, float, float, float] | None
    text_rotation_deg: float


class BasicMoveTool:
    """Move tool for selection payload, vector/text objects, or active raster layers."""

    def __init__(self) -> None:
        self.host_api: HostAPI | None = None
        self.settings = MoveSettings()
        self._pending_commands: list[Any] = []
        self._hover_cursor_set: bool = False
        self._dragging = False
        self._mode: str | None = None  # "selection" | "vector_object" | "layer"
        self._start_pos: tuple[float, float] | None = None

        self._layer_id: str | None = None
        self._layer_ref: Any | None = None
        self._layer_offset0: tuple[int, int] | None = None
        self._layer_drag_transform0: LayerTransform | None = None
        self._layer_size: tuple[float, float] = (0.0, 0.0)
        self._layer_bounds0: tuple[float, float, float, float] | None = None
        self._layer_transform_controller = LayerTransformController()
        self._comic_balloon_move = ComicBalloonMove(self)
        self._comic_panel_move = ComicPanelMove(self)
        self._layer_transform_state: _LayerTransformState | None = None
        self._group_child_offsets: list[tuple[Any, int, int]] = []
        self._group_vec_originals: list[tuple[Any, VectorObject]] = []
        self._pressed_vector_hit: _VectorHit | None = None
        self._vector_drag_originals: list[tuple[str, str | None, VectorObject]] = []
        self._vector_drag_items: list[dict[str, Any]] = []
        self._vector_drag_bounds: tuple[float, float, float, float] | None = None
        self._vector_drag_start: tuple[float, float] | None = None
        self._vector_last_delta: tuple[float, float] = (0.0, 0.0)
        self._vector_transform_state: _VectorTransformState | None = None
        self._vector_transform_last_key: tuple[Any, ...] | None = None
        self._click_distance_threshold_px = 4.0
        self._double_click_interval_ms = 350.0
        self._double_click_distance_px = 6.0
        self._last_click_time_ms: float | None = None
        self._last_click_pos: tuple[float, float] | None = None
        self._last_click_vec_id: str | None = None

        # Mask snapshots for preview-time sync (restored on finish)
        self._layer_mask_snapshot: Any | None = None  # QImage | None
        self._group_child_mask_snapshots: list[tuple[Any, Any]] = []  # [(child, QImage|None)]
        self._vector_drag_mask_layer: Any | None = None
        self._vector_drag_mask_snapshot: Any | None = None

        self._selection_payload: SelectionPayload | None = None
        self._selection_matrix: Affine2D | None = None
        self._selection_base: tuple[float, float] | None = None
        self._selection_size: tuple[float, float] = (0.0, 0.0)
        self._selection_cut_command: CutSelectionCommand | None = None

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="basic_move",
            display_name=_ts("Move"),
            category="transform",
            cursor="move",
            shortcut="V",
            shortcut_category="Tools",
            shortcut_description=_ts("Activate Move Tool"),
            order=15,
        )

    def tool_metadata(self) -> ToolMetadata:
        descriptor = self.descriptor()
        return ToolMetadata(
            tool_id=descriptor.id,
            display_name=descriptor.display_name,
            category=descriptor.category or "transform",
            tags=["move", "transform"],
            properties=[
                PropertySpec(
                    key="pixel_perfect",
                    display_name=_ts("Pixel Perfect"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.pixel_perfect,
                ),
                PropertySpec(
                    key="grid_snap",
                    display_name=_ts("Grid Snap"),
                    type=PropertyType.BOOL,
                    default_value=self.settings.grid_snap,
                ),
                PropertySpec(
                    key="grid_size",
                    display_name=_ts("Grid Size"),
                    type=PropertyType.INT,
                    default_value=self.settings.grid_size,
                    min_value=1,
                    max_value=1024,
                    step=1,
                ),
            ],
        )

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        return ContextToolbarSpec(
            show_trigger="pointer_up",
            property_keys=["pixel_perfect", "grid_snap", "grid_size"],
            anchor_mode="stroke_bbox",
            mode="settings",
        )

    def activate(self, host: HostAPI) -> None:
        self.host_api = host
        # Restore persisted grid snap settings
        settings = getattr(host, "settings", None)
        if settings is not None:
            self.settings.grid_snap = bool(settings.get("view/grid_snap", False))
            self.settings.grid_size = max(1, int(settings.get("view/grid_size", 32)))
        self._install_layer_transform_overlay()

    def enable(self) -> None:
        self._reset_state()
        self._install_layer_transform_overlay()

    def disable(self) -> None:
        self._cancel_drag()
        self._clear_layer_transform_overlay(clear_selection=True)
        self._reset_state()
        self._clear_last_click_state()
        self._pending_commands.clear()
        if self._hover_cursor_set:
            from ...plugins.sdk.cursors import apply_hover_cursor  # noqa: PLC0415

            self._hover_cursor_set = apply_hover_cursor(None, is_set=self._hover_cursor_set)

    def deactivate(self) -> None:
        self.disable()
        self.host_api = None

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        if not self.host_api:
            return
        sel_exists = self._selection_exists()
        if sel_exists:
            sel_started = self._begin_selection_drag(pt)
            if sel_started:
                return
        if self._comic_balloon_move.begin(pt):
            return
        if self._comic_panel_move.begin(pt):
            return
        layer_handle = self._hit_test_layer_transform(pt)
        if layer_handle is not None:
            handle, mode = layer_handle
            self._clear_last_click_state()
            if mode in {"scale", "rotate"} and self._begin_layer_transform(pt, handle, mode):
                return
            if mode == "move" and self._begin_layer_drag(pt):
                return
        selected_hit = self._selected_vector_hit()
        if selected_hit is not None:
            handle_hit = VectorHitTestGeometry(self.host_api).transform_handle(
                float(pt.pos[0]), float(pt.pos[1]), selected_hit.bounds
            )
            if handle_hit is not None and handle_hit[1] in {"scale", "rotate"}:
                self._pressed_vector_hit = selected_hit
                self._clear_last_click_state()
                self._set_vector_selection_payload(
                    vec_id=selected_hit.vec_id,
                    layer_id=selected_hit.layer_id,
                    transform_bounds=selected_hit.bounds,
                    preview=False,
                )
                self._notify_vector_canvas_changed()
                if self._begin_vector_transform(pt, selected_hit, handle_hit[0], handle_hit[1]):
                    return
                self._pressed_vector_hit = None
        # If the active layer is a group, skip vector hit test and do group move
        active_is_group = self._active_layer_is_group()
        if not active_is_group:
            vector_hit = self._find_vector_hit(pt)
            if vector_hit is not None:
                self._pressed_vector_hit = vector_hit
                self._set_vector_selection_payload(
                    vec_id=vector_hit.vec_id,
                    layer_id=vector_hit.layer_id,
                    transform_bounds=vector_hit.bounds,
                    preview=False,
                )
                self._notify_vector_canvas_changed()
                if self._begin_vector_object_drag(pt, vector_hit):
                    return
                self._pressed_vector_hit = None
        self._clear_last_click_state()
        self._clear_vector_selection_on_miss()
        self._begin_layer_drag(pt)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        points = list(pts)
        if not self._dragging or not points:
            return
        pt = points[-1]
        if self._mode == "comic_balloon":
            self._comic_balloon_move.update(pt)
        elif self._mode == "comic_panel":
            self._comic_panel_move.update(pt)
        elif self._mode == "selection":
            self._update_selection_drag(pt)
        elif self._mode == "vector_object":
            self._update_vector_object_drag(pt)
        elif self._mode == "vector_transform":
            self._update_vector_transform(pt)
        elif self._mode == "layer_transform":
            self._update_layer_transform(pt)
        elif self._mode == "layer":
            self._update_layer_drag(pt)

    def on_pointer_up(self, pt: StrokePoint) -> None:
        if not self._dragging:
            return
        if self._mode == "comic_balloon":
            self._comic_balloon_move.finish(pt)
            return
        if self._mode == "comic_panel":
            self._comic_panel_move.finish(pt)
            return
        mode = self._mode
        pressed_hit = self._pressed_vector_hit
        is_click = mode == "vector_object" and self._distance_from_start(pt) <= float(
            self._click_distance_threshold_px
        )
        if self._mode == "selection":
            self._finish_selection_drag(pt)
        elif self._mode == "vector_object":
            self._finish_vector_object_drag(pt)
        elif self._mode == "vector_transform":
            self._finish_vector_transform(pt)
        elif self._mode == "layer_transform":
            self._finish_layer_transform(pt)
        elif self._mode == "layer":
            self._finish_layer_drag(pt)
        self._finish_pointer()
        if mode == "vector_object":
            if is_click and pressed_hit is not None:
                self._handle_vector_click(pressed_hit, pt)
            elif not is_click:
                self._clear_last_click_state()
        elif mode in {"vector_transform", "layer_transform"}:
            self._clear_last_click_state()

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        from ...plugins.sdk.cursors import apply_hover_cursor, cursor_for_handle  # noqa: PLC0415

        cursor = None
        layer_handle = self._hit_test_layer_transform(pt)
        if layer_handle is not None:
            cursor = cursor_for_handle(*layer_handle)
        selected_hit = self._selected_vector_hit()
        if cursor is None and selected_hit is not None:
            handle_result = VectorHitTestGeometry(self.host_api).transform_handle(
                float(pt.pos[0]), float(pt.pos[1]), selected_hit.bounds
            )
            if handle_result is not None:
                cursor = cursor_for_handle(*handle_result)
        self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:
        if not pressed:
            return False
        if key.lower() in {"esc", "escape"}:
            self._cancel_drag()
            self._finish_pointer()
            return True
        return False

    def build_commands(self) -> list[Any]:
        commands = list(self._pending_commands)
        self._pending_commands.clear()
        return commands

    def get_settings(self) -> MoveSettings:
        return self.settings

    def set_settings(self, settings: ToolSettings | dict[str, Any]) -> None:
        if isinstance(settings, dict):
            if "pixel_perfect" in settings:
                self.settings.pixel_perfect = bool(settings["pixel_perfect"])
            if "grid_snap" in settings:
                self.settings.grid_snap = bool(settings["grid_snap"])
            if "grid_size" in settings:
                self.settings.grid_size = max(1, int(settings["grid_size"]))
            return
        if isinstance(settings, MoveSettings):
            self.settings = settings

    def set_setting(self, key: str, value: Any) -> None:
        if key == "pixel_perfect":
            self.settings.pixel_perfect = bool(value)
        elif key == "grid_snap":
            self.settings.grid_snap = bool(value)
            self._persist_grid_setting("view/grid_snap", self.settings.grid_snap)
        elif key == "grid_size":
            self.settings.grid_size = max(1, int(value))
            self._persist_grid_setting("view/grid_size", self.settings.grid_size)
            # Also update canvas grid rendering
            self._sync_canvas_grid_size()

    def _persist_grid_setting(self, key: str, value: Any) -> None:
        if not self.host_api:
            return
        settings = getattr(self.host_api, "settings", None)
        if settings is not None:
            import contextlib  # noqa: PLC0415

            with contextlib.suppress(Exception):
                settings.set(key, value)

    def _sync_canvas_grid_size(self) -> None:
        """Keep canvas grid_size in sync with the tool property."""
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        state = getattr(canvas, "state", None)
        if state is not None:
            state.grid_size = max(1, self.settings.grid_size)

    def _begin_layer_drag(self, pt: StrokePoint) -> bool:
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        layer = _resolve_target_layer(canvas, None)
        if layer is None or getattr(layer, "locked", False):
            return False
        if getattr(layer, "layer_type", "raster") == "vector":
            # Vector/text objects are handled by per-object hit testing.
            return False
        self._layer_ref = layer
        self._layer_id = getattr(layer, "id", None)
        self._layer_offset0 = (
            int(getattr(layer, "offset_x", 0)),
            int(getattr(layer, "offset_y", 0)),
        )
        self._layer_drag_transform0 = _layer_transform(layer)
        image = getattr(layer, "image", None)
        image_w = image.width() if image is not None and hasattr(image, "width") else 0
        image_h = image.height() if image is not None and hasattr(image, "height") else 0
        layer_transform = _layer_transform(layer).to_affine()
        local_bounds = (
            self._layer_content_local_bounds(layer, image)
            if image is not None and hasattr(image, "isNull") and not image.isNull()
            else None
        )
        if local_bounds is None:
            local_bounds = (0, 0, int(image_w), int(image_h))
        self._layer_bounds0 = self._bounds_from_transform(layer_transform, local_bounds)
        if self._layer_bounds0 is None:
            self._layer_bounds0 = self._layer_bounds_from_values(
                self._layer_offset0[0],
                self._layer_offset0[1],
                float(getattr(layer, "width", image_w) or image_w),
                float(getattr(layer, "height", image_h) or image_h),
            )
        self._layer_size = self._bounds_size(
            self._layer_bounds0,
            fallback=(
                float(getattr(layer, "width", image_w) or image_w),
                float(getattr(layer, "height", image_h) or image_h),
            ),
        )
        # Snapshot masks before drag so they can be shifted during preview
        mask = getattr(layer, "layer_mask", None)
        self._layer_mask_snapshot = mask.copy() if mask is not None else None
        # For group layers, record child offsets for coordinated movement
        self._group_child_offsets = []
        self._group_vec_originals = []
        self._group_child_mask_snapshots = []
        if getattr(layer, "layer_type", "raster") == "group":
            self._collect_child_offsets(layer)
        self._mode = "layer"
        self._dragging = True
        self._start_pos = (float(pt.pos[0]), float(pt.pos[1]))
        self._set_layer_transform_payload(self._layer_bounds0)
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        return True

    def _collect_child_offsets(self, group: Any) -> None:
        """Recursively record initial offsets of all children in a group."""
        for child in getattr(group, "children", []):
            # Vector children move via their translated anchors during the
            # drag preview; shifting their offsets too would double-apply
            # the move in renderers that honor vector-layer offsets
            # (mirrors MoveLayerCommand._apply_group_move).
            if getattr(child, "layer_type", "") != "vector":
                self._group_child_offsets.append(
                    (
                        child,
                        int(getattr(child, "offset_x", 0)),
                        int(getattr(child, "offset_y", 0)),
                    )
                )
            # Snapshot mask for preview-time sync
            cmask = getattr(child, "layer_mask", None)
            self._group_child_mask_snapshots.append(
                (child, cmask.copy() if cmask is not None else None)
            )
            # For vector/text layers, snapshot their objects for translation
            if getattr(child, "layer_type", "") == "vector":
                for obj in getattr(child, "vector_objects", []):
                    with contextlib.suppress(Exception):
                        self._group_vec_originals.append((child, copy_vector_object(obj)))
            if getattr(child, "layer_type", "") == "group":
                self._collect_child_offsets(child)

    def _update_layer_drag(self, pt: StrokePoint) -> None:
        if not self._layer_ref or not self._layer_offset0 or not self._start_pos:
            return
        dx, dy = self._delta_from_start(pt)
        dx, dy = self._apply_snap_layer(dx, dy)
        if _is_qimage_layer(self._layer_ref) and self._layer_drag_transform0 is not None:
            preview = self._layer_drag_transform0.with_translation(
                self._layer_drag_transform0.tx + dx,
                self._layer_drag_transform0.ty + dy,
            )
            self._set_runtime_layer_preview_transform(preview)
            self._set_layer_transform_payload(self._offset_bounds(self._layer_bounds0, dx, dy))
            self._recompose_canvas()
            return
        if hasattr(self._layer_ref, "offset_x") and hasattr(self._layer_ref, "offset_y"):
            self._layer_ref.offset_x = int(self._layer_offset0[0] + dx)
            self._layer_ref.offset_y = int(self._layer_offset0[1] + dy)
            # Shift layer mask in sync during preview
            self._preview_shift_mask(self._layer_ref, self._layer_mask_snapshot, dx, dy)
            # Move group children in sync
            for child, ox, oy in self._group_child_offsets:
                child.offset_x = int(ox + dx)
                child.offset_y = int(oy + dy)
            for child, snap in self._group_child_mask_snapshots:
                self._preview_shift_mask(child, snap, dx, dy)
            # Translate vector objects in group children
            for child_layer, original in self._group_vec_originals:
                moved = self._translate_vector_object(original, dx, dy)
                self._replace_vector_object(child_layer, moved)
            self._set_layer_transform_payload(self._offset_bounds(self._layer_bounds0, dx, dy))
            self._recompose_canvas()

    def _finish_layer_drag(self, pt: StrokePoint) -> None:
        if not self._layer_ref or not self._layer_offset0 or not self._start_pos:
            return
        dx, dy = self._delta_from_start(pt)
        dx, dy = self._apply_snap_layer(dx, dy)
        if _is_qimage_layer(self._layer_ref) and self._layer_drag_transform0 is not None:
            final_transform = self._layer_drag_transform0.with_translation(
                self._layer_drag_transform0.tx + dx,
                self._layer_drag_transform0.ty + dy,
            )
            self._clear_runtime_layer_preview_transform()
            self._set_layer_transform_payload(self._offset_bounds(self._layer_bounds0, dx, dy))
            if dx == 0 and dy == 0:
                self._recompose_canvas()
                return
            self._pending_commands.append(
                TransformLayerCommand(
                    final_transform,
                    layer_id=self._layer_id,
                    old_transform=self._layer_drag_transform0,
                )
            )
            return
        # Reset to original offsets and masks before issuing command
        if hasattr(self._layer_ref, "offset_x") and hasattr(self._layer_ref, "offset_y"):
            self._layer_ref.offset_x = int(self._layer_offset0[0])
            self._layer_ref.offset_y = int(self._layer_offset0[1])
        self._restore_mask(self._layer_ref, self._layer_mask_snapshot)
        for child, ox, oy in self._group_child_offsets:
            child.offset_x = ox
            child.offset_y = oy
        for child, snap in self._group_child_mask_snapshots:
            self._restore_mask(child, snap)
        # Restore vector objects to originals
        for child_layer, original in self._group_vec_originals:
            self._replace_vector_object(child_layer, original)
        self._set_layer_transform_payload(self._offset_bounds(self._layer_bounds0, dx, dy))
        if dx == 0 and dy == 0:
            return
        self._pending_commands.append(MoveLayerCommand(dx, dy, layer_id=self._layer_id))

    def _hit_test_layer_transform(self, pt: StrokePoint) -> tuple[str, str] | None:
        selection = self._layer_transform_selection()
        if selection is None:
            return None
        bounds = self._parse_transform_bounds(selection.get("transform_bounds"))
        if bounds is None:
            return None
        if not self._active_layer_matches(selection.get("layer_id")):
            return None
        return hit_test_handle_aabb(float(pt.pos[0]), float(pt.pos[1]), bounds)

    def _begin_layer_transform(self, pt: StrokePoint, handle: str, mode: str) -> bool:
        if not self.host_api or mode not in {"scale", "rotate"}:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        layer = _resolve_target_layer(canvas, None)
        if (
            layer is None
            or getattr(layer, "locked", False)
            or getattr(layer, "layer_type", "raster") != "raster"
            or not _is_qimage_layer(layer)
        ):
            return False
        image = getattr(layer, "image", None)
        if image is None or not hasattr(image, "copy"):
            return False
        payload = self._layer_payload(layer)
        if payload is None:
            return False
        local_bounds = self._layer_content_local_bounds(layer, image) or (
            0,
            0,
            int(image.width()),
            int(image.height()),
        )
        mask = getattr(layer, "layer_mask", None)
        local_mask = (
            _layer_mask_to_local(layer, mask, image.width(), image.height())
            if mask is not None and hasattr(mask, "isNull") and not mask.isNull()
            else None
        )
        original_transform = _layer_transform(layer)
        matrix = original_transform.to_affine()
        pointer0 = (float(pt.pos[0]), float(pt.pos[1]))
        anchor_local = self._anchor_for_layer_handle(handle, local_bounds)
        anchor_canvas = matrix.apply(anchor_local[0], anchor_local[1])
        v0 = None
        axis_x = None
        axis_y = None
        den_x = 1.0
        den_y = 1.0
        handle_local = None
        if mode == "rotate":
            v0 = (pointer0[0] - anchor_canvas[0], pointer0[1] - anchor_canvas[1])
        else:
            handle_local = self._handle_local_for_payload(handle, local_bounds)
            axis_x = self._normalize_vec((matrix.a, matrix.c))
            axis_y = self._normalize_vec((matrix.b, matrix.d))
            if handle_local is not None and axis_x and axis_y:
                hx, hy = matrix.apply(handle_local[0], handle_local[1])
                h0 = (hx - anchor_canvas[0], hy - anchor_canvas[1])
                den_x = self._dot(h0, axis_x)
                den_y = self._dot(h0, axis_y)

        original_visible = bool(getattr(layer, "visible", True))
        self._layer_transform_state = _LayerTransformState(
            layer=layer,
            layer_id=getattr(layer, "id", None),
            payload=payload,
            original_image=image.copy(),
            original_mask=mask.copy() if hasattr(mask, "copy") else mask,
            local_mask=local_mask,
            had_original_mask=hasattr(layer, "layer_mask"),
            original_offset=(
                int(getattr(layer, "offset_x", 0)),
                int(getattr(layer, "offset_y", 0)),
            ),
            original_transform=original_transform,
            local_bounds=local_bounds,
            matrix=matrix,
            mode=mode,
            handle=handle,
            pointer0=pointer0,
            matrix0=matrix,
            anchor_local=anchor_local,
            anchor_canvas=anchor_canvas,
            v0=v0,
            axis_x=axis_x,
            axis_y=axis_y,
            den_x=den_x,
            den_y=den_y,
            handle_local=handle_local,
            original_visible=original_visible,
        )
        self._mode = "layer_transform"
        self._dragging = True
        self._start_pos = pointer0
        self._update_layer_transform_preview()
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        return True

    def _update_layer_transform(self, pt: StrokePoint) -> None:
        state = self._layer_transform_state
        if state is None:
            return
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        shift_constrain = bool(
            QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
        )
        if self._layer_transform_controller.update_matrix(
            state,
            (float(pt.pos[0]), float(pt.pos[1])),
            shift_constrain=shift_constrain,
        ):
            self._update_layer_transform_preview()

    def _finish_layer_transform(self, pt: StrokePoint) -> None:
        self._update_layer_transform(pt)
        state = self._layer_transform_state
        if state is None:
            return
        self._restore_layer_transform_preview(state)
        self._clear_layer_transform_preview()
        final_bounds = self._bounds_from_transform(state.matrix, state.local_bounds)
        self._set_layer_transform_payload(final_bounds)
        if self._layer_transform_changed(state.matrix, state.matrix0):
            new_transform = LayerTransform.from_affine(state.matrix)
            self._pending_commands.append(
                TransformLayerCommand(
                    new_transform,
                    layer_id=state.layer_id,
                    old_transform=state.original_transform,
                )
            )
        self._recompose_canvas()

    def _update_layer_transform_preview(self) -> None:
        state = self._layer_transform_state
        if state is None or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        if self._apply_layer_transform_preview_qt(state):
            self._clear_layer_transform_preview()
            self._set_layer_transform_payload(
                self._bounds_from_transform(state.matrix, state.local_bounds)
            )
            self._recompose_canvas()
            return
        self._set_layer_transform_payload(
            self._bounds_from_transform(state.matrix, state.local_bounds)
        )
        self._recompose_canvas()

    def _apply_layer_transform_preview_qt(self, state: _LayerTransformState) -> bool:
        transform = LayerTransform.from_affine(state.matrix)
        state.layer.transform = transform
        state.layer.offset_x = round(transform.tx)
        state.layer.offset_y = round(transform.ty)
        self._mark_layer_transform_preview_changed(state.layer)
        return True

    def _restore_layer_transform_preview(self, state: _LayerTransformState) -> None:
        state.layer.transform = state.original_transform
        state.layer.offset_x = int(state.original_offset[0])
        state.layer.offset_y = int(state.original_offset[1])
        state.layer.visible = state.original_visible
        self._mark_layer_transform_preview_changed(state.layer)

    def _clear_layer_transform_preview(self) -> None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        canvas._selection_preview_image = None
        canvas._selection_preview_bounds = None

    def _set_runtime_layer_preview_transform(self, transform: LayerTransform) -> None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is not None and self._layer_id:
            setter = getattr(canvas, "set_layer_transform_preview", None)
            if callable(setter):
                with contextlib.suppress(Exception):
                    if setter(self._layer_id, transform):
                        return
        if self._layer_ref is not None:
            with contextlib.suppress(Exception):
                self._layer_ref._preview_transform = transform
                self._layer_ref._preview_transform_version = (
                    int(getattr(self._layer_ref, "_preview_transform_version", 0)) + 1
                )

    def _clear_runtime_layer_preview_transform(self) -> None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is not None and self._layer_id:
            clearer = getattr(canvas, "clear_layer_transform_preview", None)
            if callable(clearer):
                with contextlib.suppress(Exception):
                    if clearer(self._layer_id):
                        return
        if self._layer_ref is not None:
            with contextlib.suppress(Exception):
                self._layer_ref._preview_transform = None
                self._layer_ref._preview_transform_version = (
                    int(getattr(self._layer_ref, "_preview_transform_version", 0)) + 1
                )

    def _layer_payload(self, layer: Any) -> SelectionPayload | None:
        try:
            from PySide6.QtCore import QRect, Qt  # noqa: PLC0415
            from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415
        except Exception:
            return None
        image = getattr(layer, "image", None)
        if not isinstance(image, QImage) or image.isNull():
            return None
        source = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        local_bounds = self._layer_content_local_bounds(layer, source) or (
            0,
            0,
            int(source.width()),
            int(source.height()),
        )
        lx, ly, lw, lh = local_bounds
        source = source.copy(QRect(lx, ly, lw, lh))
        mask = getattr(layer, "layer_mask", None)
        if isinstance(mask, QImage) and not mask.isNull():
            layer_image = getattr(layer, "image", None)
            full_w = layer_image.width() if isinstance(layer_image, QImage) else source.width()
            full_h = layer_image.height() if isinstance(layer_image, QImage) else source.height()
            mask = _layer_mask_to_local(layer, mask, full_w, full_h).copy(QRect(lx, ly, lw, lh))
            alpha_mask = self._mask_luminance_to_alpha(mask)
            source = source.copy()
            painter = QPainter(source)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, alpha_mask)
            painter.end()
        alpha = QImage(source.width(), source.height(), QImage.Format.Format_Alpha8)
        alpha.fill(Qt.GlobalColor.white)
        transform = _layer_transform(layer).to_affine()
        bounds = self._bounds_from_transform(transform, local_bounds)
        if bounds is None:
            ox, oy = transform.apply(float(lx), float(ly))
            bounds = (ox, oy, ox + float(lw), oy + float(lh))
        return SelectionPayload(
            image=source.copy(),
            mask=alpha,
            bounds=(
                round(bounds[0]),
                round(bounds[1]),
                max(1, round(bounds[2] - bounds[0])),
                max(1, round(bounds[3] - bounds[1])),
            ),
            source_layer_id=getattr(layer, "id", None),
        )

    def _layer_content_local_bounds(
        self, layer: Any, source: Any
    ) -> tuple[int, int, int, int] | None:
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except Exception:
            return None
        if not isinstance(source, QImage) or source.isNull():
            return None
        alpha = source.convertToFormat(QImage.Format.Format_Alpha8)
        alpha_arr = self._alpha8_array(alpha)
        if alpha_arr is None:
            return None
        visible = alpha_arr > 0
        mask = getattr(layer, "layer_mask", None)
        if isinstance(mask, QImage) and not mask.isNull():
            local_mask = _layer_mask_to_local(layer, mask, source.width(), source.height())
            mask_alpha = self._mask_luminance_to_alpha(local_mask)
            mask_arr = self._alpha8_array(mask_alpha)
            if mask_arr is not None and mask_arr.shape == visible.shape:
                visible &= mask_arr > 0
        coords = np.argwhere(visible)
        if coords.size == 0:
            return None
        y0, x0 = coords.min(axis=0)
        y1, x1 = coords.max(axis=0)
        return (int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1))

    @staticmethod
    def _alpha8_array(image: Any) -> np.ndarray[Any, np.dtype[np.uint8]] | None:
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except Exception:
            return None
        if not isinstance(image, QImage) or image.isNull():
            return None
        alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
        width = int(alpha.width())
        height = int(alpha.height())
        bytes_per_line = int(alpha.bytesPerLine())
        data = memoryview(alpha.bits()).cast("B")
        arr = np.frombuffer(data, dtype=np.uint8, count=height * bytes_per_line)
        return arr.reshape((height, bytes_per_line))[:, :width].copy()

    def _layer_transform_selection(self) -> Mapping[str, Any] | None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if not isinstance(selection, Mapping):
            return None
        if selection.get("owner") != "move_tool_layer":
            return None
        return selection

    def _set_layer_transform_payload(
        self, bounds: tuple[float, float, float, float] | None
    ) -> None:
        if not self.host_api or bounds is None:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        layer_id = self._layer_id
        if layer_id is None:
            layer = _resolve_target_layer(canvas, None)
            layer_id = getattr(layer, "id", None) if layer is not None else None
        try:
            canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                "id": f"__raster_layer_transform__:{layer_id or ''}",
                "path": 0,
                "anchor": 0,
                "layer_id": layer_id,
                "preview": False,
                "transform_bounds": bounds,
                "mode": "transform",
                "owner": "move_tool_layer",
            }
        except Exception:
            return

    def _clear_layer_transform_overlay(self, *, clear_selection: bool) -> None:
        widget = self._get_canvas_widget()
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            with contextlib.suppress(Exception):
                widget.set_tool_overlay_fn(None)
        self._clear_layer_transform_preview()
        if not clear_selection:
            return
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        selection = getattr(canvas, "_vector_selection", None) if canvas is not None else None
        if isinstance(selection, Mapping) and selection.get("owner") == "move_tool_layer":
            with contextlib.suppress(Exception):
                canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr

    def _install_layer_transform_overlay(self) -> None:
        widget = self._get_canvas_widget()
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            with contextlib.suppress(Exception):
                widget.set_tool_overlay_fn(self._paint_layer_transform_overlay)

    def _paint_layer_transform_overlay(self, painter: Any) -> None:
        selection = self._layer_transform_selection()
        if selection is None:
            return
        bounds = self._parse_transform_bounds(selection.get("transform_bounds"))
        if bounds is None:
            return
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        draw_overlay = getattr(canvas, "_draw_vector_transform_overlay", None)
        if callable(draw_overlay):
            draw_overlay(painter, bounds)

    def _get_canvas_widget(self) -> Any:
        if not self.host_api:
            return None
        ui = getattr(self.host_api, "ui", None)
        getter = getattr(ui, "get_main_window", None) if ui else None
        if not callable(getter):
            return None
        try:
            mw = getter()
        except Exception:
            return None
        widget = getattr(mw, "canvas", None) if mw is not None else None
        if widget is not None and hasattr(widget, "set_tool_overlay_fn"):
            return widget
        return None

    def _mark_layer_transform_preview_changed(self, layer: Any) -> None:
        if layer is not None and hasattr(layer, "transform_version"):
            with contextlib.suppress(Exception):
                layer.transform_version = int(getattr(layer, "transform_version", 0)) + 1
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        invalidator = (
            getattr(canvas, "_invalidate_layer_cache", None) if canvas is not None else None
        )
        if callable(invalidator):
            with contextlib.suppress(Exception):
                invalidator()

    def _active_layer_matches(self, layer_id: Any) -> bool:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        layer = _resolve_target_layer(canvas, None) if canvas is not None else None
        if layer is None or getattr(layer, "locked", False):
            return False
        if getattr(layer, "layer_type", "raster") != "raster":
            return False
        current_id = getattr(layer, "id", None)
        return layer_id is None or current_id is None or str(layer_id) == str(current_id)

    @staticmethod
    def _layer_bounds_from_values(
        x: float, y: float, width: float, height: float
    ) -> tuple[float, float, float, float] | None:
        if width <= 0.0 or height <= 0.0:
            return None
        return (float(x), float(y), float(x) + float(width), float(y) + float(height))

    @staticmethod
    def _bounds_size(
        bounds: tuple[float, float, float, float] | None,
        *,
        fallback: tuple[float, float],
    ) -> tuple[float, float]:
        if bounds is None:
            return fallback
        return (max(0.0, bounds[2] - bounds[0]), max(0.0, bounds[3] - bounds[1]))

    @staticmethod
    def _offset_bounds(
        bounds: tuple[float, float, float, float] | None, dx: float, dy: float
    ) -> tuple[float, float, float, float] | None:
        if bounds is None:
            return None
        return (bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy)

    @staticmethod
    def _parse_transform_bounds(value: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            return None
        try:
            bounds = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
        except (TypeError, ValueError):
            return None
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return None
        return bounds

    @staticmethod
    def _selection_rect_to_overlay(
        rect: tuple[int, int, int, int],
    ) -> tuple[float, float, float, float]:
        x, y, w, h = rect
        return (float(x), float(y), float(x + w), float(y + h))

    def _bounds_from_transform(
        self, transform: Affine2D, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float, float, float] | None:
        return LayerTransformController.bounds_from_transform(transform, local_bounds)

    @staticmethod
    def _layer_transform_changed(matrix: Affine2D, original: Affine2D) -> bool:
        return LayerTransformController.transform_changed(matrix, original)

    @staticmethod
    def _anchor_for_layer_handle(
        handle: str, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float]:
        return LayerTransformController.anchor_for_handle(handle, local_bounds)

    @staticmethod
    def _handle_local_for_payload(
        handle: str, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float] | None:
        return LayerTransformController.handle_local_for_payload(handle, local_bounds)

    @staticmethod
    def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
        return LayerTransformController.dot(a, b)

    @staticmethod
    def _cross(a: tuple[float, float], b: tuple[float, float]) -> float:
        return LayerTransformController.cross(a, b)

    @staticmethod
    def _length(v: tuple[float, float]) -> float:
        return LayerTransformController.length(v)

    @staticmethod
    def _normalize_vec(v: tuple[float, float]) -> tuple[float, float] | None:
        return LayerTransformController.normalize_vec(v)

    @staticmethod
    def _mask_luminance_to_alpha(mask: Any) -> Any:
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415

            from loomstroke.core.mask_coverage import mask_coverage_alpha8  # noqa: PLC0415
        except Exception:
            return mask
        if not isinstance(mask, QImage) or mask.isNull():
            return mask
        return mask_coverage_alpha8(mask)

    @staticmethod
    def _preview_shift_mask(layer: Any, snapshot: Any, dx: int, dy: int) -> None:
        """Shift layer_mask from *snapshot* by (dx, dy) for drag preview."""
        if snapshot is None or layer is None:
            return
        if _mask_size_matches_layer_image(layer, snapshot):
            return
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

        w, h = snapshot.width(), snapshot.height()
        shifted = QImage(w, h, snapshot.format())
        shifted.fill(Qt.GlobalColor.white)
        p = QPainter(shifted)
        p.drawImage(int(dx), int(dy), snapshot)
        p.end()
        layer.layer_mask = shifted
        # Bump version so the GPU mask texture cache re-uploads the shifted data.
        _bump_layer_mask_version(layer)

    @staticmethod
    def _restore_mask(layer: Any, snapshot: Any) -> None:
        """Restore layer_mask from snapshot."""
        if layer is None or snapshot is None:
            if layer is not None and snapshot is None:
                return  # no mask to restore
            return
        layer.layer_mask = snapshot.copy()
        _bump_layer_mask_version(layer)

    def _active_layer_is_group(self) -> bool:
        """Check if the currently active layer is a group."""
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        active = getattr(canvas, "active_layer", None)
        return getattr(active, "layer_type", "") == "group"

    @staticmethod
    def _replace_vector_object(layer: Any, obj: VectorObject) -> None:
        """Replace a vector object in a layer's vector_objects list by ID."""
        objects = getattr(layer, "vector_objects", None)
        if objects is None:
            return
        obj_id = obj.id
        for i, existing in enumerate(objects):
            if getattr(existing, "id", None) == obj_id:
                objects[i] = obj
                return

    def _begin_vector_object_drag(self, pt: StrokePoint, hit: _VectorHit) -> bool:
        if not self.host_api:
            return False
        try:
            original = copy_vector_object(hit.obj)
        except Exception:
            return False
        self._vector_drag_originals = [(hit.vec_id, hit.layer_id, original)]
        self._vector_drag_items = [{"id": hit.vec_id, "layer_id": hit.layer_id}]
        self._vector_drag_bounds = hit.bounds
        self._vector_drag_start = (float(pt.pos[0]), float(pt.pos[1]))
        self._vector_last_delta = (0.0, 0.0)
        # Snapshot the vector layer's mask for preview-time sync
        self._vector_drag_mask_layer = None
        self._vector_drag_mask_snapshot = None
        if hit.layer_id:
            canvas = getattr(self.host_api, "canvas", None)
            if canvas is not None:
                vlayer = _resolve_target_layer(canvas, hit.layer_id)
                if vlayer is not None:
                    mask = getattr(vlayer, "layer_mask", None)
                    if mask is not None:
                        self._vector_drag_mask_layer = vlayer
                        self._vector_drag_mask_snapshot = mask.copy()
        self._mode = "vector_object"
        self._dragging = True
        self._start_pos = (float(pt.pos[0]), float(pt.pos[1]))
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        return True

    def _update_vector_object_drag(self, pt: StrokePoint) -> None:
        if not self._vector_drag_start or not self._vector_drag_originals:
            return
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        dx = float(pt.pos[0]) - self._vector_drag_start[0]
        dy = float(pt.pos[1]) - self._vector_drag_start[1]
        if self.settings.pixel_perfect:
            dx = float(round(dx))
            dy = float(round(dy))
        dx, dy = self._apply_snap_vector(dx, dy)
        if (dx, dy) == self._vector_last_delta:
            return
        self._vector_last_delta = (dx, dy)
        for vec_id, layer_id, original in self._vector_drag_originals:
            moved = self._translate_vector_object(original, dx, dy)
            self._apply_vector_replacement(canvas, vec_id=vec_id, layer_id=layer_id, obj=moved)
        # Shift mask in sync during preview
        self._preview_shift_mask(
            self._vector_drag_mask_layer,
            self._vector_drag_mask_snapshot,
            int(dx),
            int(dy),
        )
        if self._vector_drag_bounds and self._vector_drag_items:
            item = self._vector_drag_items[0]
            item_id = item.get("id")
            if isinstance(item_id, str):
                layer_id = item.get("layer_id")
                self._set_vector_selection_payload(
                    vec_id=item_id,
                    layer_id=layer_id if isinstance(layer_id, str) else None,
                    transform_bounds=(
                        self._vector_drag_bounds[0] + dx,
                        self._vector_drag_bounds[1] + dy,
                        self._vector_drag_bounds[2] + dx,
                        self._vector_drag_bounds[3] + dy,
                    ),
                    preview=True,
                )
        self._notify_vector_canvas_changed()

    def _finish_vector_object_drag(self, pt: StrokePoint) -> None:
        if not self._vector_drag_start or not self._vector_drag_originals:
            return
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        dx = float(pt.pos[0]) - self._vector_drag_start[0]
        dy = float(pt.pos[1]) - self._vector_drag_start[1]
        if self.settings.pixel_perfect:
            dx = float(round(dx))
            dy = float(round(dy))
        dx, dy = self._apply_snap_vector(dx, dy)
        # Restore originals and mask without recompose to avoid visible flash
        self._restore_vector_drag_originals_quiet()
        self._restore_mask(self._vector_drag_mask_layer, self._vector_drag_mask_snapshot)
        if dx != 0.0 or dy != 0.0:
            begin_tx = getattr(canvas, "begin_transaction", None)
            end_tx = getattr(canvas, "end_transaction", None)
            if callable(begin_tx) and callable(end_tx):
                begin_tx("Move Vector Object")
            try:
                for vec_id, layer_id, original in self._vector_drag_originals:
                    moved = self._translate_vector_object(original, dx, dy)
                    cmd = VectorReplaceCommand(vec_id, moved, layer_id=layer_id)
                    try:
                        canvas.submit(cmd, merge_key=None)
                    except Exception:
                        self._pending_commands.append(cmd)
                # Shift mask via MoveLayerCommand for undo support
                if self._vector_drag_mask_snapshot is not None:
                    mask_layer_id = self._vector_drag_originals[0][1]
                    if mask_layer_id:
                        mask_cmd = MoveLayerCommand(
                            int(dx),
                            int(dy),
                            layer_id=mask_layer_id,
                        )
                        try:
                            canvas.submit(mask_cmd, merge_key=None)
                        except Exception:
                            self._pending_commands.append(mask_cmd)
            finally:
                if callable(end_tx):
                    end_tx()
        if self._vector_drag_bounds and self._vector_drag_items:
            item = self._vector_drag_items[0]
            item_id = item.get("id")
            if isinstance(item_id, str):
                layer_id = item.get("layer_id")
                self._set_vector_selection_payload(
                    vec_id=item_id,
                    layer_id=layer_id if isinstance(layer_id, str) else None,
                    transform_bounds=(
                        self._vector_drag_bounds[0] + dx,
                        self._vector_drag_bounds[1] + dy,
                        self._vector_drag_bounds[2] + dx,
                        self._vector_drag_bounds[3] + dy,
                    ),
                    preview=False,
                )
        self._notify_vector_canvas_changed()

    def _restore_vector_drag_originals(self) -> None:
        self._restore_vector_drag_originals_quiet()
        self._notify_vector_canvas_changed()

    def _restore_vector_drag_originals_quiet(self) -> None:
        """Restore originals without recompose (used at drag finish to avoid flash)."""
        if not self._vector_drag_originals or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        for vec_id, layer_id, original in self._vector_drag_originals:
            self._apply_vector_replacement(canvas, vec_id=vec_id, layer_id=layer_id, obj=original)

    def _selected_vector_hit(self) -> _VectorHit | None:  # noqa: PLR0911, PLR0912
        if not self.host_api:
            return None
        canvas = getattr(self.host_api, "canvas", None)
        getter = getattr(canvas, "get_vector_objects", None) if canvas else None
        if canvas is None or not callable(getter):
            return None
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, Mapping):
            return None
        selected_id = selection.get("id")
        if not isinstance(selected_id, str) or not selected_id:
            return None
        selected_layer_raw = selection.get("layer_id")
        selected_layer = (
            str(selected_layer_raw)
            if isinstance(selected_layer_raw, str) and selected_layer_raw
            else None
        )
        try:
            objects = list(getter())
        except Exception:
            return None
        if not objects:
            return None
        overlay_bounds = selection.get("transform_bounds")
        parsed_overlay_bounds: tuple[float, float, float, float] | None = None
        if isinstance(overlay_bounds, (tuple, list)) and len(overlay_bounds) == 4:
            try:
                parsed_overlay_bounds = (
                    float(overlay_bounds[0]),
                    float(overlay_bounds[1]),
                    float(overlay_bounds[2]),
                    float(overlay_bounds[3]),
                )
            except (TypeError, ValueError):
                parsed_overlay_bounds = None
        for layer_id, obj in reversed(objects):
            vec_id = getattr(obj, "id", None)
            if str(vec_id) != selected_id:
                continue
            current_layer_id = str(layer_id) if isinstance(layer_id, str) else None
            if selected_layer is not None and current_layer_id != selected_layer:
                continue
            if self._is_layer_locked(current_layer_id):
                return None
            bounds = parsed_overlay_bounds or self._vector_bounds(obj)
            if bounds is None:
                continue
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            return _VectorHit(
                vec_id=selected_id,
                layer_id=current_layer_id,
                obj=obj,
                bounds=bounds,
                is_text=self._is_text_vector_object(obj),
            )
        return None

    def _begin_vector_transform(
        self,
        pt: StrokePoint,
        hit: _VectorHit,
        handle: str,
        mode: str,
    ) -> bool:
        if not self.host_api:
            return False
        if mode not in {"scale", "rotate"}:
            return False
        try:
            original = copy_vector_object(hit.obj)
        except Exception:
            return False
        pivot = compute_pivot(handle, hit.bounds)
        press_point = (float(pt.pos[0]), float(pt.pos[1]))
        press_vec = None
        if mode == "scale":
            press_vec = (press_point[0] - pivot[0], press_point[1] - pivot[1])

        text_settings: TextToolSettings | None = None
        text_content = ""
        word_wrap = True
        text_rect: tuple[float, float, float, float] | None = None
        text_rotation_deg = 0.0
        if hit.is_text:
            payload = getattr(original, "meta", {}).get("text_tool")
            if isinstance(payload, Mapping):
                text_settings = self._text_settings_from_payload(payload)
                text_content = extract_text_content(payload)
                word_wrap = extract_word_wrap(payload)
                text_rect = self._text_rect_from_payload(
                    payload,
                    fallback=(
                        hit.bounds[0],
                        hit.bounds[1],
                        max(1.0, hit.bounds[2] - hit.bounds[0]),
                        max(1.0, hit.bounds[3] - hit.bounds[1]),
                    ),
                )
                text_rotation_deg = self._text_rotation_from_payload(payload)

        self._vector_drag_originals = [(hit.vec_id, hit.layer_id, original)]
        self._vector_drag_items = [{"id": hit.vec_id, "layer_id": hit.layer_id}]
        self._vector_drag_bounds = hit.bounds
        self._vector_transform_state = _VectorTransformState(
            hit=hit,
            handle=handle,
            mode=mode,
            press_point=press_point,
            pivot=pivot,
            press_vec=press_vec,
            start_bounds=hit.bounds,
            original=original,
            text_settings=text_settings,
            text_content=text_content,
            word_wrap=word_wrap,
            text_rect=text_rect,
            text_rotation_deg=text_rotation_deg,
        )
        self._vector_transform_last_key = None
        self._mode = "vector_transform"
        self._dragging = True
        self._start_pos = press_point
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        return True

    def _update_vector_transform(self, pt: StrokePoint) -> None:
        if not self._vector_transform_state or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        preview = self._build_vector_transform_preview(pt)
        if preview is None:
            return
        preview_obj, preview_bounds, _changed, preview_key = preview
        if preview_key is not None and preview_key == self._vector_transform_last_key:
            return
        self._vector_transform_last_key = preview_key
        state = self._vector_transform_state
        self._apply_vector_replacement(
            canvas,
            vec_id=state.hit.vec_id,
            layer_id=state.hit.layer_id,
            obj=preview_obj,
        )
        self._set_vector_selection_payload(
            vec_id=state.hit.vec_id,
            layer_id=state.hit.layer_id,
            transform_bounds=preview_bounds,
            preview=True,
        )
        self._notify_vector_canvas_changed()

    def _finish_vector_transform(self, pt: StrokePoint) -> None:
        state = self._vector_transform_state
        if state is None or not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        preview = self._build_vector_transform_preview(pt)
        final_obj = state.original
        final_bounds = state.start_bounds
        changed = False
        if preview is not None:
            final_obj, final_bounds, changed, _preview_key = preview
        self._restore_vector_drag_originals()
        if changed:
            begin_tx = getattr(canvas, "begin_transaction", None)
            end_tx = getattr(canvas, "end_transaction", None)
            if callable(begin_tx) and callable(end_tx):
                begin_tx("Transform Vector Object")
            try:
                cmd = VectorReplaceCommand(state.hit.vec_id, final_obj, layer_id=state.hit.layer_id)
                try:
                    canvas.submit(cmd, merge_key=None)
                except Exception:
                    self._pending_commands.append(cmd)
            finally:
                if callable(end_tx):
                    end_tx()
        self._set_vector_selection_payload(
            vec_id=state.hit.vec_id,
            layer_id=state.hit.layer_id,
            transform_bounds=final_bounds,
            preview=False,
        )
        self._notify_vector_canvas_changed()

    def _build_vector_transform_preview(
        self,
        pt: StrokePoint,
    ) -> (
        tuple[VectorObject, tuple[float, float, float, float], bool, tuple[Any, ...] | None] | None
    ):
        state = self._vector_transform_state
        if state is None:
            return None
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        shift = bool(QGuiApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if state.hit.is_text:
            preview = self._build_text_transform_preview(state, pt, uniform=shift)
            if preview is not None:
                return preview
        return self._build_non_text_transform_preview(state, pt, uniform=shift)

    def _build_non_text_transform_preview(
        self,
        state: _VectorTransformState,
        pt: StrokePoint,
        *,
        uniform: bool = False,
    ) -> tuple[VectorObject, tuple[float, float, float, float], bool, tuple[Any, ...]]:
        if state.mode == "scale":
            factors = compute_scale_factors(
                handle=state.handle,
                pivot=state.pivot,
                press_vec=state.press_vec,
                current=(float(pt.pos[0]), float(pt.pos[1])),
                bbox=state.start_bounds,
                uniform=uniform,
            )
            sx, sy = factors
            preview_obj = self._scale_vector_object_geom(state.original, state.pivot, sx, sy)
            changed = not (abs(sx - 1.0) <= 1e-4 and abs(sy - 1.0) <= 1e-4)
            key: tuple[Any, ...] = ("scale", round(sx, 4), round(sy, 4))
        else:
            angle = math.atan2(
                float(pt.pos[1]) - state.pivot[1],
                float(pt.pos[0]) - state.pivot[0],
            ) - math.atan2(
                state.press_point[1] - state.pivot[1],
                state.press_point[0] - state.pivot[0],
            )
            if uniform:
                snap = math.radians(15.0)
                angle = round(angle / snap) * snap
            preview_obj = self._rotate_vector_object_geom(state.original, state.pivot, angle)
            changed = abs(angle) > 1e-4
            key = ("rotate", round(math.degrees(angle), 3))
        preview_bounds = self._vector_bounds(preview_obj) or state.start_bounds
        return (preview_obj, preview_bounds, changed, key)

    def _build_text_transform_preview(
        self,
        state: _VectorTransformState,
        pt: StrokePoint,
        *,
        uniform: bool = False,
    ) -> tuple[VectorObject, tuple[float, float, float, float], bool, tuple[Any, ...]] | None:
        if state.text_settings is None or state.text_rect is None:
            return None
        preview_settings = self._copy_text_settings(state.text_settings)
        preview_rect = state.text_rect
        preview_rotation = state.text_rotation_deg
        # auto_size is always False (word_wrap mode only)
        changed = False
        if state.mode == "scale":
            factors = compute_scale_factors(
                handle=state.handle,
                pivot=state.pivot,
                press_vec=state.press_vec,
                current=(float(pt.pos[0]), float(pt.pos[1])),
                bbox=state.start_bounds,
                uniform=uniform,
            )
            sx, sy = factors
            x, y, w, h = state.text_rect
            corners = (
                (x, y),
                (x + w, y),
                (x + w, y + h),
                (x, y + h),
            )
            transformed: list[tuple[float, float]] = []
            for cx, cy in corners:
                transformed.append(
                    (
                        state.pivot[0] + (cx - state.pivot[0]) * sx,
                        state.pivot[1] + (cy - state.pivot[1]) * sy,
                    )
                )
            xs = [p[0] for p in transformed]
            ys = [p[1] for p in transformed]
            min_x = min(xs)
            min_y = min(ys)
            max_x = max(xs)
            max_y = max(ys)
            preview_rect = (
                min_x,
                min_y,
                max(1.0, max_x - min_x),
                max(1.0, max_y - min_y),
            )
            changed = not (abs(sx - 1.0) <= 1e-4 and abs(sy - 1.0) <= 1e-4)
            # Resizing the box never changes font size / tracking
            key: tuple[Any, ...] = (
                "scale_text",
                round(preview_rect[0], 3),
                round(preview_rect[1], 3),
                round(preview_rect[2], 3),
                round(preview_rect[3], 3),
                round(float(preview_settings.font_size_pt), 3),
                round(float(preview_settings.tracking), 3),
            )
        else:
            angle = math.atan2(
                float(pt.pos[1]) - state.pivot[1],
                float(pt.pos[0]) - state.pivot[0],
            ) - math.atan2(
                state.press_point[1] - state.pivot[1],
                state.press_point[0] - state.pivot[0],
            )
            if uniform:
                snap_r = math.radians(15.0)
                angle = round(angle / snap_r) * snap_r
            preview_rotation = state.text_rotation_deg + math.degrees(angle)
            changed = abs(angle) > 1e-4
            key = ("rotate_text", round(preview_rotation, 3))

        preview_obj = replace_edited_text(
            state.original,
            content=state.text_content,
            rect=preview_rect,
            rotation_deg=preview_rotation,
        )
        preview_bounds = self._vector_bounds(preview_obj) or state.start_bounds
        return (preview_obj, preview_bounds, changed, key)

    def _scale_vector_object_geom(
        self,
        obj: VectorObject,
        pivot: tuple[float, float],
        sx: float,
        sy: float,
    ) -> VectorObject:
        px, py = pivot
        paths: list[VectorPath] = []
        for path in obj.paths:
            anchors: list[Anchor] = []
            for anchor in path.anchors:
                ax, ay = float(anchor.pos[0]), float(anchor.pos[1])
                pos = (
                    px + (ax - px) * sx,
                    py + (ay - py) * sy,
                )
                h_in = (
                    (float(anchor.h_in[0]) * sx, float(anchor.h_in[1]) * sy)
                    if anchor.h_in
                    else None
                )
                h_out = (
                    (float(anchor.h_out[0]) * sx, float(anchor.h_out[1]) * sy)
                    if anchor.h_out
                    else None
                )
                anchors.append(
                    Anchor(
                        pos=pos,
                        kind=anchor.kind,
                        h_in=h_in,
                        h_out=h_out,
                    )
                )
            paths.append(VectorPath(anchors=anchors, closed=path.closed))
        meta = dict(obj.meta)
        param_payload = meta.get("shape_manager_parametric")
        if isinstance(param_payload, Mapping):
            param_copy = dict(param_payload)
            ref_bbox = param_copy.get("reference_bbox")
            if isinstance(ref_bbox, (list, tuple)) and len(ref_bbox) == 4:
                try:
                    param_copy["reference_bbox"] = [
                        px + (float(ref_bbox[0]) - px) * sx,
                        py + (float(ref_bbox[1]) - py) * sy,
                        px + (float(ref_bbox[2]) - px) * sx,
                        py + (float(ref_bbox[3]) - py) * sy,
                    ]
                    meta["shape_manager_parametric"] = param_copy
                except (TypeError, ValueError):
                    logger.debug("parametric bbox scale failed", exc_info=True)
        return VectorObject(
            id=obj.id,
            paths=paths,
            style=obj.style,
            meta=meta,
            preview=obj.preview,
        )

    def _rotate_vector_object_geom(
        self,
        obj: VectorObject,
        pivot: tuple[float, float],
        angle: float,
    ) -> VectorObject:
        px, py = pivot
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        def _rotate_point(x: float, y: float) -> tuple[float, float]:
            dx = x - px
            dy = y - py
            return (
                px + (dx * cos_a) - (dy * sin_a),
                py + (dx * sin_a) + (dy * cos_a),
            )

        def _rotate_vec(vec: tuple[float, float] | None) -> tuple[float, float] | None:
            if vec is None:
                return None
            vx = float(vec[0])
            vy = float(vec[1])
            return (
                (vx * cos_a) - (vy * sin_a),
                (vx * sin_a) + (vy * cos_a),
            )

        paths: list[VectorPath] = []
        for path in obj.paths:
            anchors: list[Anchor] = []
            for anchor in path.anchors:
                pos = _rotate_point(float(anchor.pos[0]), float(anchor.pos[1]))
                anchors.append(
                    Anchor(
                        pos=pos,
                        kind=anchor.kind,
                        h_in=_rotate_vec(anchor.h_in),
                        h_out=_rotate_vec(anchor.h_out),
                    )
                )
            paths.append(VectorPath(anchors=anchors, closed=path.closed))
        return VectorObject(
            id=obj.id,
            paths=paths,
            style=obj.style,
            meta=dict(obj.meta),
            preview=obj.preview,
        )

    @staticmethod
    def _text_rect_from_payload(
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
    def _text_rotation_from_payload(payload: Mapping[str, Any]) -> float:
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

    @staticmethod
    def _copy_text_settings(settings: TextToolSettings) -> TextToolSettings:
        return copy_text_tool_settings(settings)

    @staticmethod
    def _text_settings_from_payload(
        payload: Mapping[str, Any],
    ) -> TextToolSettings:
        fallback = TextToolSettings()
        style_defaults: Mapping[str, Any] | None = None
        style_map = payload.get("style_map")
        if isinstance(style_map, Mapping):
            candidate = style_map.get("default")
            if isinstance(candidate, Mapping):
                style_defaults = candidate

        color_raw: Any = payload.get("color_rgba", list(fallback.color_rgba))
        if style_defaults is not None:
            color_raw = style_defaults.get("color_rgba", color_raw)
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

        align = str(align_raw or fallback.align).strip().lower()
        if align not in {"left", "center", "right", "justify"}:
            align = fallback.align

        font_family_raw: Any = payload.get("font_family", fallback.font_family)
        font_style_raw: Any = payload.get("font_style", fallback.font_style)
        font_size_raw: Any = payload.get("font_size_pt", fallback.font_size_pt)
        vertical_raw: Any = payload.get("vertical", fallback.vertical)
        tracking_raw: Any = payload.get("tracking", fallback.tracking)
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
                default=VERTICAL_FLOW_LEFT,
            ),
            align=align,
            line_height=max(0.5, min(4.0, line_height)),
            tracking=max(-64.0, min(128.0, tracking)),
        )

    def _apply_vector_replacement(
        self,
        canvas: Any,
        *,
        vec_id: str,
        layer_id: str | None,
        obj: VectorObject,
    ) -> bool:
        replacer = getattr(canvas, "replace_vector_object", None)
        if callable(replacer):
            try:
                return replacer(vec_id, obj, layer_id=layer_id, recompose=False) is not None
            except Exception:
                return False
        return self._replace_vector_object_fallback(
            canvas,
            vec_id=vec_id,
            layer_id=layer_id,
            obj=obj,
        )

    def _replace_vector_object_fallback(  # noqa: PLR0912
        self,
        canvas: Any,
        *,
        vec_id: str,
        layer_id: str | None,
        obj: VectorObject,
    ) -> bool:
        if layer_id is not None:
            getter = getattr(canvas, "_get_layer_by_id", None)
            if callable(getter):
                layer = getter(layer_id)
                objects = getattr(layer, "vector_objects", None) if layer is not None else None
                if isinstance(objects, list):
                    for idx, existing in enumerate(objects):
                        if getattr(existing, "id", None) == vec_id:
                            objects[idx] = obj
                            return True
        layers = getattr(canvas, "_layers", None)
        if isinstance(layers, list):
            for layer in layers:
                if getattr(layer, "layer_type", None) != "vector":
                    continue
                objects = getattr(layer, "vector_objects", None)
                if not isinstance(objects, list):
                    continue
                for idx, existing in enumerate(objects):
                    if getattr(existing, "id", None) == vec_id:
                        objects[idx] = obj
                        return True
        store = getattr(canvas, "_vector_objects", None)
        if isinstance(store, list):
            for idx, existing in enumerate(store):
                if getattr(existing, "id", None) == vec_id:
                    store[idx] = obj
                    return True
        return False

    def _find_vector_hit(self, pt: StrokePoint) -> _VectorHit | None:
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
        if not objects:
            return None
        for layer_id, obj in reversed(objects):
            if obj is None:
                continue
            current_layer_id = str(layer_id) if isinstance(layer_id, str) else None
            if self._is_layer_locked(current_layer_id):
                continue
            vec_id = getattr(obj, "id", None)
            if not isinstance(vec_id, str):
                continue
            bounds = self._vector_bounds(obj)
            if bounds is None:
                continue
            if not self._point_in_bounds(pt, bounds):
                continue
            # Skip if the vector object is occluded by an opaque layer above it
            if self._is_occluded_at(canvas, current_layer_id, pt):
                continue
            return _VectorHit(
                vec_id=vec_id,
                layer_id=current_layer_id,
                obj=obj,
                bounds=bounds,
                is_text=self._is_text_vector_object(obj),
            )
        return None

    def _vector_bounds(self, obj: Any) -> tuple[float, float, float, float] | None:  # noqa: PLR0911
        bounds = self._vector_bbox(obj)
        if bounds is not None:
            return bounds
        meta = getattr(obj, "meta", None)
        if not isinstance(meta, Mapping):
            return None
        payload = meta.get("text_tool")
        if not isinstance(payload, Mapping):
            return None
        frame = payload.get("frame")
        if isinstance(frame, Mapping):
            try:
                x = float(frame.get("x", payload.get("x")))
                y = float(frame.get("y", payload.get("y")))
                w = float(frame.get("width", payload.get("width")))
                h = float(frame.get("height", payload.get("height")))
            except (TypeError, ValueError):
                return None
        else:
            try:
                x = float(payload.get("x"))
                y = float(payload.get("y"))
                w = float(payload.get("width"))
                h = float(payload.get("height"))
            except (TypeError, ValueError):
                return None
        if w <= 0.0 or h <= 0.0:
            return None
        return (x, y, x + w, y + h)

    def _vector_bbox(self, obj: Any) -> tuple[float, float, float, float] | None:
        from ...plugins.shape_manager.ops.bbox import compute_bbox  # noqa: PLC0415

        paths = getattr(obj, "paths", None) or []
        if not paths:
            return None
        try:
            return compute_bbox(paths)
        except Exception:
            return None

    def _is_text_vector_object(self, obj: Any) -> bool:
        meta = getattr(obj, "meta", None)
        if not isinstance(meta, Mapping):
            return False
        payload = meta.get("text_tool")
        return isinstance(payload, Mapping)

    def _is_layer_locked(self, layer_id: str | None) -> bool:
        if not self.host_api or not layer_id:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        getter = getattr(canvas, "_get_layer_by_id", None)
        if not callable(getter):
            getter = getattr(canvas, "get_layer_by_id", None)
        if not callable(getter):
            return False
        try:
            layer = getter(layer_id)
        except Exception:
            return False
        return bool(getattr(layer, "locked", False))

    @staticmethod
    def _is_occluded_at(canvas: Any, layer_id: str | None, pt: StrokePoint) -> bool:
        """Return True if any visible layer above *layer_id* has an opaque pixel at *pt*.

        This prevents selecting vector objects that are visually hidden behind
        opaque raster/group content above them.
        """
        if layer_id is None:
            return False
        flatten = getattr(canvas, "_flatten_layers", None)
        if not callable(flatten):
            return False
        try:
            flat: list[Any] = flatten()
        except Exception:
            return False

        # Find the target layer's position in the flat list
        target_idx: int | None = None
        for i, layer in enumerate(flat):
            if getattr(layer, "id", None) == layer_id:
                target_idx = i
                break
        if target_idx is None:
            return False

        px, py = int(pt.pos[0]), int(pt.pos[1])

        # Check layers above (rendered later = visually on top)
        for layer in flat[target_idx + 1 :]:
            if not getattr(layer, "visible", True):
                continue
            lt = getattr(layer, "layer_type", "raster")
            if lt in ("effect", "group"):
                continue
            if lt == "vector":
                # Another vector layer above — check bounding boxes
                for obj in getattr(layer, "vector_objects", []):
                    from ...plugins.shape_manager.ops.bbox import (  # noqa: PLC0415
                        compute_object_bbox,
                    )

                    bbox = compute_object_bbox(obj)
                    if bbox is not None:
                        x1, y1, x2, y2 = bbox
                        if x1 <= px <= x2 and y1 <= py <= y2:
                            return True
                continue
            # Raster layer: check pixel alpha at (px, py)
            img = getattr(layer, "image", None)
            if img is None:
                continue
            ox = int(getattr(layer, "offset_x", 0))
            oy = int(getattr(layer, "offset_y", 0))
            local_x = px - ox
            local_y = py - oy
            if 0 <= local_x < img.width() and 0 <= local_y < img.height():
                from PySide6.QtGui import QColor  # noqa: PLC0415

                pixel = QColor(img.pixelColor(local_x, local_y))
                if pixel.alpha() > 32:  # threshold for "opaque enough"
                    return True
        return False

    def _set_vector_selection_payload(
        self,
        *,
        vec_id: str,
        layer_id: str | None,
        transform_bounds: tuple[float, float, float, float] | None,
        preview: bool,
        auto_return_tool: str | None = None,
        auto_return_origin_id: str | None = None,
    ) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        try:
            canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                "id": vec_id,
                "path": 0,
                "anchor": 0,
                "layer_id": layer_id,
                "preview": bool(preview),
                "transform_bounds": transform_bounds,
                "mode": "transform",
                "owner": "move_tool",
            }
            if isinstance(auto_return_tool, str) and auto_return_tool:
                canvas._vector_selection["auto_return_tool"] = auto_return_tool  # type: ignore[index,attr-defined]  # duck-typed dict access
            if isinstance(auto_return_origin_id, str) and auto_return_origin_id:
                canvas._vector_selection["auto_return_origin_id"] = auto_return_origin_id  # type: ignore[index,attr-defined]  # duck-typed dict access
        except Exception:
            return

    def _handle_vector_click(self, hit: _VectorHit, pt: StrokePoint) -> None:
        click_time = self._event_time_ms(pt)
        if self._is_double_click(hit.vec_id, click_time, pt):
            self._clear_last_click_state()
            if hit.is_text:
                self._set_vector_selection_payload(
                    vec_id=hit.vec_id,
                    layer_id=hit.layer_id,
                    transform_bounds=hit.bounds,
                    preview=False,
                    auto_return_tool=self.descriptor().id,
                    auto_return_origin_id=hit.vec_id,
                )
                self._activate_text_edit_tool(pt, hit)
            else:
                self._set_vector_selection_payload(
                    vec_id=hit.vec_id,
                    layer_id=hit.layer_id,
                    transform_bounds=hit.bounds,
                    preview=False,
                    auto_return_tool=self.descriptor().id,
                    auto_return_origin_id=hit.vec_id,
                )
                self._activate_tool("vector_path_edit_tool")
            return
        self._last_click_time_ms = click_time
        self._last_click_pos = (float(pt.pos[0]), float(pt.pos[1]))
        self._last_click_vec_id = hit.vec_id

    def _clear_vector_selection_on_miss(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, Mapping):
            return
        has_vector_target = selection.get("id") is not None or selection.get("items") is not None
        if not has_vector_target:
            return
        try:
            canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
        except Exception:
            return
        self._notify_vector_canvas_changed()

    def _is_double_click(self, vec_id: str, click_time_ms: float, pt: StrokePoint) -> bool:
        if self._last_click_time_ms is None or self._last_click_vec_id != vec_id:
            return False
        if click_time_ms < self._last_click_time_ms:
            return False
        if (click_time_ms - self._last_click_time_ms) > self._double_click_interval_ms:
            return False
        if self._last_click_pos is None:
            return False
        dx = float(pt.pos[0]) - self._last_click_pos[0]
        dy = float(pt.pos[1]) - self._last_click_pos[1]
        return math.hypot(dx, dy) <= self._double_click_distance_px

    @staticmethod
    def _event_time_ms(pt: StrokePoint) -> float:
        if pt.time is None:
            return float(time.time() * 1000.0)
        try:
            return float(pt.time)
        except (TypeError, ValueError):
            return float(time.time() * 1000.0)

    def _activate_text_edit_tool(self, pt: StrokePoint, hit: _VectorHit | None = None) -> None:
        tool_id = "text_tool"
        if hit is not None:
            payload = self._extract_text_payload_from_hit(hit)
            if payload is not None:
                try:
                    if self._text_settings_from_payload(payload).vertical:
                        tool_id = "text_tool_vertical"
                except Exception:
                    logger.debug("text settings vertical check failed", exc_info=True)
        if self._activate_tool(tool_id):
            self._simulate_click_on_active_tool(pt)
            return
        if tool_id != "text_tool" and self._activate_tool("text_tool"):
            self._simulate_click_on_active_tool(pt)

    def _extract_text_payload_from_hit(self, hit: _VectorHit) -> Mapping[str, Any] | None:
        meta = getattr(hit.obj, "meta", None)
        if not isinstance(meta, Mapping):
            return None
        payload = meta.get("text_tool")
        return payload if isinstance(payload, Mapping) else None

    def _activate_tool(self, tool_id: str) -> bool:
        if not self.host_api:
            return False
        tool_manager = getattr(self.host_api, "_tool_manager", None) or getattr(
            self.host_api,
            "tool_manager",
            None,
        )
        activate = getattr(tool_manager, "activate_tool", None) if tool_manager else None
        if not callable(activate):
            return False
        try:
            activate(tool_id)
        except Exception:
            return False
        return True

    def _simulate_click_on_active_tool(self, pt: StrokePoint) -> None:
        if not self.host_api:
            return
        tool_manager = getattr(self.host_api, "_tool_manager", None) or getattr(
            self.host_api,
            "tool_manager",
            None,
        )
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

    @staticmethod
    def _point_in_bounds(pt: StrokePoint, bounds: tuple[float, float, float, float]) -> bool:
        x0, y0, x1, y1 = bounds
        px = float(pt.pos[0])
        py = float(pt.pos[1])
        return x0 <= px <= x1 and y0 <= py <= y1

    def _distance_from_start(self, pt: StrokePoint) -> float:
        if not self._start_pos:
            return 0.0
        dx = float(pt.pos[0]) - float(self._start_pos[0])
        dy = float(pt.pos[1]) - float(self._start_pos[1])
        return math.hypot(dx, dy)

    def _clear_last_click_state(self) -> None:
        self._last_click_time_ms = None
        self._last_click_pos = None
        self._last_click_vec_id = None

    def _begin_selection_drag(self, pt: StrokePoint) -> bool:
        if not self.host_api:
            return False
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return False
        cmd = CutSelectionCommand(None)
        if not cmd.apply(canvas):
            return False
        if cmd.payload is None:
            cmd.revert(canvas)
            return False
        self._selection_cut_command = cmd
        self._selection_payload = cmd.payload
        base_x = float(cmd.payload.bounds[0])
        base_y = float(cmd.payload.bounds[1])
        self._selection_base = (base_x, base_y)
        self._selection_size = (float(cmd.payload.bounds[2]), float(cmd.payload.bounds[3]))
        self._selection_matrix = Affine2D.translation(base_x, base_y)
        self._mode = "selection"
        self._dragging = True
        self._start_pos = (float(pt.pos[0]), float(pt.pos[1]))
        if self.host_api:
            self.host_api.input.pointer_capture(self.descriptor().id)
        self._update_selection_preview()
        return True

    def _update_selection_drag(self, pt: StrokePoint) -> None:
        if not self._selection_base:
            return
        dx, dy = self._delta_from_start(pt)
        dx, dy = self._apply_snap_selection(dx, dy)
        self._selection_matrix = Affine2D.translation(
            self._selection_base[0] + dx, self._selection_base[1] + dy
        )
        self._update_selection_preview()

    def _finish_selection_drag(self, pt: StrokePoint) -> None:
        if not self._selection_payload or not self._selection_base:
            self._cancel_selection_preview()
            return
        dx, dy = self._delta_from_start(pt)
        dx, dy = self._apply_snap_selection(dx, dy)
        if self._selection_cut_command is not None:
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            if canvas is not None:
                self._selection_cut_command.revert(canvas)
        self._cancel_selection_preview()
        if dx == 0 and dy == 0:
            return
        transform = Affine2D.translation(self._selection_base[0] + dx, self._selection_base[1] + dy)
        self._pending_commands.append(
            TransformSelectionCommand(
                self._selection_payload,
                transform,
                duplicate=False,
                mask_mode="soft",
            )
        )

    def _update_selection_preview(self) -> None:
        if not self.host_api or not self._selection_payload or not self._selection_matrix:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return
        if self._update_selection_preview_qt(canvas, width, height):
            return
        premultiplied = _is_qimage_layer(_resolve_target_layer(canvas, None))
        transformed = _transform_payload_raster(
            self._selection_payload,
            self._selection_matrix,
            premultiplied=premultiplied,
            mask_mode="soft",
            canvas_size=(width, height),
        )
        if transformed is None:
            return
        _dest_image, dest_mask, dest_bounds = transformed
        full_bounds = _affine_bounds(
            self._selection_matrix,
            int(self._selection_payload.bounds[2]),
            int(self._selection_payload.bounds[3]),
        )
        mask_full, bounds, _ = _place_mask_on_canvas(
            dest_mask,
            position=(dest_bounds[0], dest_bounds[1]),
            width=width,
            height=height,
        )
        if bounds is None:
            UpdateSelectionMaskCommand._set_selection(canvas, None, None)
            _set_selection_full_bounds(canvas, full_bounds)
        else:
            canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
                canvas, mask_full, width, height
            )
            UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, bounds)
            _set_selection_full_bounds(canvas, full_bounds or bounds)

        # Apply selection mask to dest_image for raster preview
        import numpy as np  # noqa: PLC0415

        mask_f = dest_mask.astype(np.float32) * (1.0 / 255.0)
        dest_image_masked = (_dest_image.astype(np.float32) * mask_f[:, :, np.newaxis]).astype(
            np.uint8
        )
        from ...core.commands import _qimage_from_bgra  # noqa: PLC0415

        preview_image = _qimage_from_bgra(dest_image_masked, premultiplied=True)
        canvas._selection_preview_image = preview_image
        canvas._selection_preview_bounds = dest_bounds
        self._recompose_canvas()

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
        text_payload = meta.get("text_tool")
        if isinstance(text_payload, Mapping):
            payload = dict(text_payload)
            frame = payload.get("frame")
            if isinstance(frame, Mapping):
                frame_copy = dict(frame)
                frame_x = self._offset_numeric(frame_copy.get("x"), dx)
                frame_y = self._offset_numeric(frame_copy.get("y"), dy)
                if frame_x is not None:
                    frame_copy["x"] = frame_x
                if frame_y is not None:
                    frame_copy["y"] = frame_y
                payload["frame"] = frame_copy
            payload_x = self._offset_numeric(payload.get("x"), dx)
            payload_y = self._offset_numeric(payload.get("y"), dy)
            if payload_x is not None:
                payload["x"] = payload_x
            if payload_y is not None:
                payload["y"] = payload_y
            meta["text_tool"] = payload
        return VectorObject(
            id=obj.id,
            paths=paths,
            style=obj.style,
            meta=meta,
            preview=obj.preview,
        )

    @staticmethod
    def _offset_numeric(value: Any, delta: float) -> float | None:
        try:
            return float(value) + float(delta)
        except (TypeError, ValueError):
            return None

    def _update_selection_preview_qt(self, canvas: Any, width: int, height: int) -> bool:
        payload = self._selection_payload
        transform = self._selection_matrix
        if payload is None or transform is None:
            return False
        try:
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
            UpdateSelectionMaskCommand._set_selection(canvas, None, None)
            _set_selection_full_bounds(canvas, bounds)
            canvas._selection_preview_image = None
            canvas._selection_preview_bounds = None
            self._recompose_canvas()
            return True

        dest_x, dest_y, dest_w, dest_h = clipped_bounds

        # --- Build masked preview image (only selected pixels visible) ---
        import numpy as np  # noqa: PLC0415
        from PySide6.QtCore import Qt  # noqa: PLC0415

        image_source = payload.image
        if image_source.format() != QImage.Format.Format_ARGB32_Premultiplied:
            image_source = image_source.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        # Read mask as numpy
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

        # Read image as numpy (BGRA premultiplied)
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

        # Multiply all BGRA channels by mask/255 (premultiplied format)
        ch = min(ih, mh)
        cw = min(iw, mw)
        mask_f = mask_arr[:ch, :cw].astype(np.float32) * (1.0 / 255.0)
        img_arr[:ch, :cw] = (
            img_arr[:ch, :cw].astype(np.float32) * mask_f[:, :, np.newaxis]
        ).astype(np.uint8)
        if ih > ch:
            img_arr[ch:, :, :] = 0
        if iw > cw:
            img_arr[:, cw:, :] = 0

        masked_source = QImage(
            img_arr.data,
            iw,
            ih,
            iw * 4,
            QImage.Format.Format_ARGB32_Premultiplied,
        ).copy()

        # Draw masked source into destination-sized preview
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

        # --- Update marching-ants mask at the new position ---
        mask_source = payload.mask
        if mask_source.format() != QImage.Format.Format_Alpha8:
            mask_source = mask_source.convertToFormat(QImage.Format.Format_Alpha8)
        mask_full = QImage(width, height, QImage.Format.Format_Alpha8)
        mask_full.fill(0)
        painter = QPainter(mask_full)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
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

    def _cancel_selection_preview(self) -> None:
        if not self.host_api:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return
        canvas._selection_preview_image = None
        canvas._selection_preview_bounds = None
        self._recompose_canvas()

    def _selection_exists(self) -> bool:
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
                bounds_full = getattr(canvas, "_selection_bounds_full", None)
                if isinstance(bounds_full, (tuple, list)) and len(bounds_full) == 4:
                    return bounds_full[2] > 0 and bounds_full[3] > 0
                return False
            bounds = getattr(selection, "bounds", None)
            return bool(bounds)
        return False

    # ------------------------------------------------------------------
    # Snap helpers (grid + guide) — delegates to sdk.snap
    # ------------------------------------------------------------------

    def _snap_position(
        self, x: float, y: float, w: float = 0.0, h: float = 0.0
    ) -> tuple[float, float]:
        """Snap using grid and/or guide snap, checking all edges."""
        from ..sdk.snap import snap_position  # noqa: PLC0415

        return snap_position(
            self.host_api,
            x,
            y,
            w,
            h,
            grid_snap=self.settings.grid_snap,
            grid_size=self.settings.grid_size,
        )

    def _apply_snap_selection(self, dx: int, dy: int) -> tuple[int, int]:
        """Adjust selection delta so edges snap to grid/guides."""
        if not self._selection_base:
            return dx, dy
        if not self.settings.grid_snap and not self._has_guide_controller():
            return dx, dy
        target_x = self._selection_base[0] + dx
        target_y = self._selection_base[1] + dy
        sw, sh = self._selection_size
        sx, sy = self._snap_position(target_x, target_y, sw, sh)
        snapped_dx = round(sx - self._selection_base[0])
        snapped_dy = round(sy - self._selection_base[1])
        return snapped_dx, snapped_dy

    def _apply_snap_layer(self, dx: int, dy: int) -> tuple[int, int]:
        """Adjust layer delta so edges snap to grid/guides."""
        if not self._layer_offset0:
            return dx, dy
        if not self.settings.grid_snap and not self._has_guide_controller():
            return dx, dy
        if self._layer_bounds0 is not None:
            target_x = float(self._layer_bounds0[0] + dx)
            target_y = float(self._layer_bounds0[1] + dy)
            base_x = float(self._layer_bounds0[0])
            base_y = float(self._layer_bounds0[1])
        else:
            target_x = float(self._layer_offset0[0] + dx)
            target_y = float(self._layer_offset0[1] + dy)
            base_x = float(self._layer_offset0[0])
            base_y = float(self._layer_offset0[1])
        lw, lh = getattr(self, "_layer_size", (0.0, 0.0))
        sx, sy = self._snap_position(target_x, target_y, lw, lh)
        return round(sx - base_x), round(sy - base_y)

    def _apply_snap_vector(self, dx: float, dy: float) -> tuple[float, float]:
        """Adjust vector delta so bbox edges snap to grid/guides."""
        if not self._vector_drag_bounds:
            return dx, dy
        if not self.settings.grid_snap and not self._has_guide_controller():
            return dx, dy
        bx, by = self._vector_drag_bounds[0], self._vector_drag_bounds[1]
        bw = self._vector_drag_bounds[2] - bx
        bh = self._vector_drag_bounds[3] - by
        sx, sy = self._snap_position(bx + dx, by + dy, bw, bh)
        return sx - bx, sy - by

    def _has_guide_controller(self) -> bool:
        from ..sdk.snap import get_guide_controller  # noqa: PLC0415

        return get_guide_controller(self.host_api) is not None

    def _delta_from_start(self, pt: StrokePoint) -> tuple[int, int]:
        if not self._start_pos:
            return (0, 0)
        dx = float(pt.pos[0]) - float(self._start_pos[0])
        dy = float(pt.pos[1]) - float(self._start_pos[1])
        if self.settings.pixel_perfect:
            return (round(dx), round(dy))
        return (math.floor(dx), math.floor(dy))

    def _cancel_drag(self) -> None:
        if self._mode == "comic_balloon":
            self._comic_balloon_move.cancel()
        elif self._mode == "comic_panel":
            self._comic_panel_move.cancel()
        elif self._mode == "selection":
            canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
            if canvas is not None and self._selection_cut_command is not None:
                self._selection_cut_command.revert(canvas)
            self._cancel_selection_preview()
        elif self._mode in {"vector_object", "vector_transform"}:
            self._restore_vector_drag_originals()
        elif self._mode == "layer_transform":
            state = self._layer_transform_state
            if state is not None:
                self._restore_layer_transform_preview(state)
                self._set_layer_transform_payload(
                    self._bounds_from_transform(state.matrix0, state.local_bounds)
                )
            self._clear_layer_transform_preview()
            self._recompose_canvas()
        elif self._mode == "layer":
            self._clear_runtime_layer_preview_transform()
            if (
                self._layer_ref
                and self._layer_offset0
                and hasattr(self._layer_ref, "offset_x")
                and hasattr(self._layer_ref, "offset_y")
            ):
                self._layer_ref.offset_x = int(self._layer_offset0[0])
                self._layer_ref.offset_y = int(self._layer_offset0[1])
                self._recompose_canvas()

    def _finish_pointer(self) -> None:
        if self.host_api:
            self.host_api.input.release_capture(self.descriptor().id)
        self._reset_state()

    def _reset_state(self) -> None:
        self._dragging = False
        self._mode = None
        self._start_pos = None
        self._layer_id = None
        self._layer_ref = None
        self._layer_offset0 = None
        self._layer_drag_transform0 = None
        self._layer_size = (0.0, 0.0)
        self._layer_bounds0 = None
        self._layer_transform_state = None
        self._pressed_vector_hit = None
        self._vector_drag_originals = []
        self._vector_drag_items = []
        self._vector_drag_bounds = None
        self._vector_drag_start = None
        self._vector_last_delta = (0.0, 0.0)
        self._vector_transform_state = None
        self._vector_transform_last_key = None
        self._selection_payload = None
        self._selection_matrix = None
        self._selection_base = None
        self._selection_cut_command = None

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

    def _notify_vector_canvas_changed(self) -> None:
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        notifier = getattr(canvas, "notify_vector_preview_changed", None)
        if callable(notifier):
            notifier()
            return
        self._recompose_canvas()


class BasicMovePlugin(PluginBase):
    """Plugin wrapper for the move tool."""

    def __init__(self) -> None:
        super().__init__(PLUGIN_METADATA)
        self.move_tool = BasicMoveTool()

    def descriptor(self) -> ToolDescriptor:
        return self.move_tool.descriptor()

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self.move_tool.activate(host_api)
        register_tool = getattr(host_api, "register_tool", None)
        if callable(register_tool):
            register_tool(self.move_tool)

    def enable(self) -> None:
        self.move_tool.enable()

    def disable(self) -> None:
        self.move_tool.disable()

    def deactivate(self) -> None:
        self.move_tool.deactivate()
        super().deactivate()

    async def initialize(self, host_api: HostAPI) -> None:
        self.activate(host_api)

    async def cleanup(self) -> None:
        if self.host_api and hasattr(self.host_api, "unregister_tool"):
            self.host_api.unregister_tool(self.move_tool.descriptor().id)
        self.deactivate()

    def get_tools(self) -> list[BasicMoveTool]:
        return [self.move_tool]

    # --- Legacy compatibility surface ---
    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        self.move_tool.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        self.move_tool.on_pointer_move(pts)

    def on_pointer_up(self, pt: StrokePoint) -> None:
        self.move_tool.on_pointer_up(pt)

    def on_key(self, key: str, pressed: bool) -> bool:
        return self.move_tool.on_key(key, pressed)

    def build_commands(self) -> list[Any]:
        return self.move_tool.build_commands()


PLUGIN_METADATA = PluginMetadata(
    id="loomstroke.basic_move",
    name=_ts("Basic Move"),
    version="1.0.0",
    description=_ts("Move tool for selection, vector objects, or active raster layer"),
    author="LoomStroke Team",
    types=[PluginType.TOOL],
)


def create_plugin() -> BasicMovePlugin:
    return BasicMovePlugin()
