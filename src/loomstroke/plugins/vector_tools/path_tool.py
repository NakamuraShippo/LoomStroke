from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor

from ...core.color_state import ColorDescriptor
from ...host_api import StrokePoint as HostStrokePoint
from ..common.vector_hit_test_geometry import VectorHitTestGeometry
from ..sdk import PropertySpec, PropertyType, ToolDescriptor, ToolMetadata
from ..sdk.base import PluginMetadata, PluginType
from ..sdk.cursors import apply_hover_cursor
from ..sdk.tool import ContextToolbarSpec
from .common import (
    Anchor,
    VectorCreateCommand,
    VectorPath,
    VectorStyle,
    make_vector_object,
    normalize_rgb,
)
from .cubic_hit_test import CubicHitTest

logger = logging.getLogger(__name__)


def _point_near_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float, threshold: float
) -> bool:
    """Return True if point (px, py) is within *threshold* of segment AB."""
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-6:
        return False
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2 <= threshold * threshold


def _point_hits_vector(px: float, py: float, vec: object, threshold: float) -> bool:
    return CubicHitTest.hits_vector(px, py, vec, threshold)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("VectorPathTool", text, use_fallback=True)


@dataclass
class PathToolConfig:
    stroke_width: float = 2.0
    stroke_color: tuple[int, int, int] = (0, 0, 0)
    fill_enabled: bool = False
    fill_color: tuple[int, int, int] | None = None
    join: str = "miter"
    cap: str = "butt"
    dash: tuple[float, ...] = ()
    shift_mode: str = "constraint"  # "constraint" | "disable_snap"
    snap_grid: bool = True
    snap_guides: bool = True
    snap_anchors: bool = True
    snap_edges: bool = True
    auto_close_tolerance_px: float = 8.0
    switch_tool_policy: str = "auto_commit"  # "auto_commit" | "auto_discard" | "confirm"
    remember_last_vector_tool: bool = True


class VectorPathTool:
    def __init__(self) -> None:
        self.host_api = None
        self.config = PathToolConfig()
        self.state: Literal["idle", "creating_path", "editing_path", "transforming"] = "idle"
        self._anchors: list[Anchor] = []
        self._pending_commands: list[VectorCreateCommand] = []
        self._pointer_down: HostStrokePoint | None = None
        self._hover_cursor_set: bool = False
        self._pointer_modifiers: dict[str, bool] = {}
        self._current_drag_vec: tuple[float, float] | None = None
        self._preview_layer_id: str | None = None
        self._target_layer_id: str | None = None
        self._created_layer_id: str | None = None

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id="vector_path_tool",
            display_name=_ts("Path"),
            cursor="crosshair",
            category="vector",
            icon_svg="icons/vector_path.svg",
            order=32,
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
            property_keys=["stroke_width", "stroke_color", "fill_enabled", "fill_color"],
            anchor_mode="stroke_bbox",
            mode="settings",
        )

    def activate(self, host) -> None:
        self.host_api = host

    def enable(self) -> None:
        self._reset()

    def disable(self) -> None:
        self._on_tool_switch()
        if self._hover_cursor_set:
            self._hover_cursor_set = apply_hover_cursor(None, is_set=self._hover_cursor_set)

    def deactivate(self) -> None:
        self._on_tool_switch()
        self.host_api = None

    def get_settings(self) -> dict[str, object]:
        return dict(vars(self.config))

    def set_settings(self, settings: PathToolConfig | dict[str, object]) -> None:
        if isinstance(settings, PathToolConfig):
            data = vars(settings)
        elif isinstance(settings, dict):
            data = settings
        else:
            data = {}
        merged = dict(vars(self.config))
        merged.update(data)
        stroke = normalize_rgb(merged.get("stroke_color")) or self.config.stroke_color
        merged["stroke_color"] = stroke
        merged["fill_color"] = normalize_rgb(merged.get("fill_color"))
        if bool(merged.get("fill_enabled")) and merged.get("fill_color") is None:
            merged["fill_color"] = stroke
        if merged.get("fill_color") is None:
            merged["fill_enabled"] = False
        self.config = PathToolConfig(**merged)
        self._sync_active_color_from_settings()

    def set_setting(self, key: str, value: object) -> None:
        if not hasattr(self.config, key):
            return
        if key == "stroke_color":
            normalized = normalize_rgb(value)
            if normalized is not None:
                self.config.stroke_color = normalized
        elif key == "fill_color":
            self.config.fill_color = normalize_rgb(value)
            if self.config.fill_color is None:
                self.config.fill_enabled = False
            else:
                self.config.fill_enabled = True
        elif key == "fill_enabled":
            self.config.fill_enabled = bool(value)
            if self.config.fill_enabled and self.config.fill_color is None:
                self.config.fill_color = self.config.stroke_color
        else:
            setattr(self.config, key, value)
        if key in ("stroke_color", "fill_color"):
            self._sync_active_color_from_settings()

    def get_settings_dict(self) -> dict[str, object]:
        return dict(vars(self.config))

    def on_pointer_down(
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del ctrl, meta

        # If not currently creating a path, check for existing objects first
        if self.state != "creating_path":
            if self._try_switch_to_edit_tool(pt):
                return
            # Clicking empty area: clear any lingering vector selection
            self._clear_vector_selection()
            self._anchors.clear()
            self.state = "creating_path"
            self._target_layer_id = None

        # Clicking near the first anchor closes immediately
        if len(self._anchors) >= 3 and self._is_near_start(pt.pos):
            self._finalize(close=True)
            return

        self._apply_active_color()
        self._pointer_down = pt
        self._pointer_modifiers = {"alt": alt, "shift": shift}
        self._current_drag_vec = None

    def on_pointer_move(self, pts: Iterable[HostStrokePoint]) -> None:
        if self.state != "creating_path" or self._pointer_down is None:
            return
        last = None
        for _last in pts:
            last = _last
        if last is None:
            return
        self._current_drag_vec = (
            float(last.pos[0] - self._pointer_down.pos[0]),
            float(last.pos[1] - self._pointer_down.pos[1]),
        )
        self._update_canvas_preview(
            provisional_anchor=self._make_anchor(
                pos=self._apply_snap(last.pos, shift=self._pointer_modifiers.get("shift", False)),
                drag_vec=self._current_drag_vec,
                modifiers=self._pointer_modifiers,
            )
        )

    def on_pointer_up(self, pt: HostStrokePoint) -> None:
        if self.state != "creating_path" or self._pointer_down is None:
            return

        drag_vec = (
            float(pt.pos[0] - self._pointer_down.pos[0]),
            float(pt.pos[1] - self._pointer_down.pos[1]),
        )
        self._append_node(pt, drag_vec=drag_vec, modifiers=self._pointer_modifiers)

        if len(self._anchors) >= 3 and self._is_near_start(pt.pos):
            self._finalize(close=True)
        else:
            self._pointer_down = None
            self._pointer_modifiers = {}
            self._current_drag_vec = None

    def on_pointer_hover(self, pt: HostStrokePoint) -> None:

        cursor = None
        if self.state == "editing_path" and self._anchors:
            cursor = QCursor(Qt.CursorShape.SizeAllCursor)
        self._hover_cursor_set = apply_hover_cursor(cursor, is_set=self._hover_cursor_set)

    def on_key(self, key: str, pressed: bool) -> bool:
        if not pressed:
            return False
        lowered = key.lower()
        if lowered in ("esc", "escape"):
            self._cancel()
            return True
        if lowered in ("enter", "return"):
            self._finalize(close=False)
            return True
        if lowered in ("backspace", "delete"):
            if self._undo_last_anchor():
                return True
            self._cancel()
            return True
        return False

    def on_wheel(self, delta: float, pt: HostStrokePoint) -> bool:
        del delta, pt
        return False

    def build_commands(self) -> list[VectorCreateCommand]:
        pending = list(self._pending_commands)
        self._pending_commands.clear()
        return pending

    def can_undo(self) -> bool:
        return self.state == "creating_path" and bool(self._anchors)

    def on_undo(self) -> bool:
        if not self.can_undo():
            return False
        return self._undo_last_anchor()

    def _property_specs(self) -> list[PropertySpec]:
        return [
            PropertySpec(
                key="shift_mode",
                display_name=_ts("Shift Mode"),
                type=PropertyType.ENUM,
                enum_items=["constraint", "disable_snap"],
                default_value=self.config.shift_mode,
            ),
            PropertySpec(
                key="auto_close_tolerance_px",
                display_name=_ts("Auto Close Tolerance (px)"),
                type=PropertyType.FLOAT,
                min_value=0.0,
                max_value=64.0,
                step=0.5,
                default_value=self.config.auto_close_tolerance_px,
            ),
            PropertySpec(
                key="stroke_width",
                display_name=_ts("Stroke Width"),
                type=PropertyType.FLOAT,
                min_value=0.1,
                max_value=64.0,
                default_value=self.config.stroke_width,
                step=0.1,
                is_common=True,
            ),
            PropertySpec(
                key="stroke_color",
                display_name=_ts("Stroke Color"),
                type=PropertyType.COLOR,
                default_value=self.config.stroke_color,
                is_common=True,
            ),
            PropertySpec(
                key="fill_enabled",
                display_name=_ts("Fill Enabled"),
                type=PropertyType.BOOL,
                default_value=self.config.fill_enabled,
                is_common=True,
            ),
            PropertySpec(
                key="fill_color",
                display_name=_ts("Fill Color"),
                type=PropertyType.COLOR,
                default_value=self.config.fill_color,
                is_common=True,
            ),
        ]

    def _on_tool_switch(self) -> None:
        if self.state != "creating_path":
            return
        policy = self.config.switch_tool_policy
        if policy == "auto_discard":
            self._cancel()
            return
        # "auto_commit" or "confirm" fall back to commit for now
        self._finalize(close=False)

    def _append_node(
        self,
        pt: HostStrokePoint,
        *,
        drag_vec: tuple[float, float] | None,
        modifiers: dict[str, bool],
    ) -> None:
        anchor = self._make_anchor(
            pos=self._apply_snap(pt.pos, shift=modifiers.get("shift", False)),
            drag_vec=drag_vec,
            modifiers=modifiers,
        )
        if self._anchors and self._anchors[-1].pos == anchor.pos:
            self._anchors[-1] = anchor
        else:
            self._anchors.append(anchor)
        self._update_canvas_preview()

    def _make_anchor(
        self,
        *,
        pos: tuple[float, float],
        drag_vec: tuple[float, float] | None,
        modifiers: dict[str, bool],
    ) -> Anchor:
        handle_vec = drag_vec
        if handle_vec is not None and math.hypot(handle_vec[0], handle_vec[1]) < 1e-3:
            handle_vec = None
        if handle_vec and modifiers.get("shift", False) and self.config.shift_mode == "constraint":
            handle_vec = self._apply_constraint(handle_vec)
        if handle_vec:
            return Anchor(
                pos=pos,
                kind="smooth",
                h_in=(-handle_vec[0], -handle_vec[1]),
                h_out=handle_vec,
            )
        return Anchor(pos=pos, kind="corner", h_in=(0.0, 0.0), h_out=(0.0, 0.0))

    def _apply_snap(self, pos: tuple[float, float], *, shift: bool) -> tuple[float, float]:
        if shift and self.config.shift_mode == "disable_snap":
            return (float(pos[0]), float(pos[1]))
        if len(self._anchors) >= 3 and self.config.snap_anchors and self._is_near_start(pos):
            start = self._anchors[0].pos
            return (float(start[0]), float(start[1]))
        return (float(pos[0]), float(pos[1]))

    def _apply_constraint(self, handle_vec: tuple[float, float]) -> tuple[float, float]:
        angle = math.atan2(handle_vec[1], handle_vec[0])
        snapped = round(angle / (math.pi / 4)) * (math.pi / 4)
        length = math.hypot(handle_vec[0], handle_vec[1])
        if length <= 1e-3:
            return (0.0, 0.0)
        return (math.cos(snapped) * length, math.sin(snapped) * length)

    def _is_near_start(self, pos: tuple[float, float]) -> bool:
        if not self._anchors:
            return False
        start = self._anchors[0].pos
        dx = float(pos[0]) - float(start[0])
        dy = float(pos[1]) - float(start[1])
        return math.hypot(dx, dy) <= self.config.auto_close_tolerance_px

    def _finalize(self, *, close: bool) -> None:
        if len(self._anchors) < 2:
            self._cancel()
            return
        closed = close or self._is_near_start(self._anchors[-1].pos)
        anchors = list(self._anchors)
        path = VectorPath(anchors=anchors, closed=closed)
        style = VectorStyle(
            stroke_color=self.config.stroke_color,
            stroke_width=self.config.stroke_width,
            fill_enabled=self.config.fill_enabled,
            fill_color=self.config.fill_color,
            join=self.config.join,
            cap=self.config.cap,
            dash=self.config.dash,
        )
        meta = {
            "tool": "vector_path_tool",
            "autoCloseTolerancePx": self.config.auto_close_tolerance_px,
            "shiftMode": self.config.shift_mode,
        }
        obj = make_vector_object([path], style, meta)
        cmd = VectorCreateCommand(
            obj,
            layer_id=self._ensure_target_layer_id(),
            merge_key="vector_path_tool",
            created_layer_id=self._created_layer_id,
        )
        self._created_layer_id = None
        self._queue_command(cmd)
        self._clear_canvas_preview()
        self._reset(keep_pending=True)

    def _queue_command(self, cmd: VectorCreateCommand) -> None:
        if self.host_api and getattr(self.host_api, "canvas", None):
            try:
                success = self.host_api.canvas.submit(cmd, merge_key="vector_path_tool")
                if success:
                    return
            except Exception:
                logger.debug("VectorPathTool immediate submit failed", exc_info=True)
        self._pending_commands.append(cmd)

    def _resolve_existing_vector_layer_id(self) -> str | None:
        """Return the active layer's ID if it is a draw-vector layer (not shape)."""
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        current_layer = getattr(canvas, "current_layer", None)
        if callable(current_layer):
            try:
                ref = current_layer()
                vector_kind = str(getattr(ref, "vector_kind", "draw") or "draw").lower()
                name = str(getattr(ref, "name", "") or "").strip().lower()
                if (
                    ref is not None
                    and getattr(ref, "layer_type", None) == "vector"
                    and vector_kind == "draw"
                    and not name.startswith("text layer")
                ):
                    return getattr(ref, "id", None)
            except Exception:
                logger.debug("current_layer lookup failed", exc_info=True)
        return None

    def _create_vector_layer_id(self) -> str | None:
        # Reuse existing vector layer if available
        existing = self._resolve_existing_vector_layer_id()
        if existing:
            return existing
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            return None
        add_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer()
                if ref is not None:
                    created = getattr(ref, "id", None)
                    if created:
                        self._created_layer_id = created
                    return created
            except Exception:
                logger.debug("VectorPathTool add_vector_layer failed")
        return None

    def _cancel(self) -> None:
        try:
            self._cleanup_created_layer()
        finally:
            self._reset()

    def _reset(self, *, keep_pending: bool = False) -> None:
        self._anchors.clear()
        if not keep_pending:
            self._pending_commands.clear()
        self._pointer_down = None
        self._pointer_modifiers = {}
        self._current_drag_vec = None
        self._clear_canvas_preview()
        self.state = "idle"
        self._preview_layer_id = None
        self._target_layer_id = None
        self._created_layer_id = None

    def _undo_last_anchor(self) -> bool:
        if not self._anchors:
            return False
        self._anchors.pop()
        self._pointer_down = None
        self._pointer_modifiers = {}
        self._current_drag_vec = None
        if not self._anchors:
            self._cancel()
            return True
        self._update_canvas_preview()
        return True

    def _cleanup_created_layer(self) -> None:
        if self._created_layer_id is None:
            return
        canvas = getattr(self.host_api, "canvas", None)
        if canvas is None:
            self._created_layer_id = None
            return
        layer_id = self._created_layer_id
        self._created_layer_id = None
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                if any(lid == layer_id for lid, _ in getter()):
                    return
            except Exception:
                return
        remover = getattr(canvas, "remove_layer", None)
        if callable(remover):
            try:
                remover(layer_id)
            except Exception:
                logger.debug("VectorPathTool remove_layer failed", exc_info=True)

    def _apply_active_color(self) -> None:
        """Sync stroke/fill colors from the shared Color panel state."""
        host = self.host_api
        if not host or not getattr(host, "color_state", None):
            return
        try:
            rgba = host.color_state.current.rgba  # (r,g,b,a) floats 0-1
            rgb = tuple(int(max(0, min(255, round(c * 255)))) for c in rgba[:3])
            self.config.stroke_color = rgb
            if self.config.fill_enabled and self.config.fill_color is None:
                self.config.fill_color = rgb
        except Exception:
            logger.debug("VectorPathTool active color fetch failed", exc_info=True)

    def _sync_active_color_from_settings(self) -> None:
        """Push stroke color to the shared Color panel for palette consistency."""
        host = self.host_api
        if not host or not getattr(host, "color_state", None):
            return
        try:
            rgb = self.config.stroke_color
            if rgb is None:
                return
            r, g, b = rgb
            # stroke_color is always stored as 0-255 ints by normalize_rgb
            r, g, b = (max(0, min(255, round(c))) for c in (r, g, b))
            host.color_state.apply(ColorDescriptor((r / 255, g / 255, b / 255, 1.0)))
        except Exception:
            logger.debug("VectorPathTool color push failed", exc_info=True)

    def _update_canvas_preview(self, provisional_anchor: Anchor | None = None) -> None:
        if not self.host_api or not getattr(self.host_api, "canvas", None):
            return
        if not self._anchors and provisional_anchor is None:
            self._clear_canvas_preview()
            return
        anchors = list(self._anchors)
        if provisional_anchor:
            if anchors and anchors[-1].pos == provisional_anchor.pos:
                anchors[-1] = provisional_anchor
            else:
                anchors.append(provisional_anchor)
        path = VectorPath(anchors=anchors, closed=False)
        style = VectorStyle(
            stroke_color=self.config.stroke_color,
            stroke_width=self.config.stroke_width,
            fill_enabled=self.config.fill_enabled,
            fill_color=self.config.fill_color,
            join=self.config.join,
            cap=self.config.cap,
            dash=self.config.dash,
        )
        obj = make_vector_object([path], style, {"preview": True})
        try:
            canvas = self.host_api.canvas
            if self._target_layer_id is None:
                # 最初のプレビュー時に1度だけレイヤーを確保し、確定時も同じIDを使う
                self._target_layer_id = self._create_vector_layer_id()
            self._preview_layer_id = self._target_layer_id
            canvas._vector_preview = obj  # type: ignore[attr-defined]  # duck-typed canvas attr
            canvas._vector_preview_layer_id = self._preview_layer_id  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            # logger.debug("VectorPathTool preview update failed", exc_info=True)
            return

    def _clear_canvas_preview(self) -> None:
        if not self.host_api or not getattr(self.host_api, "canvas", None):
            return
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
            logger.debug("VectorPathTool preview clear failed", exc_info=True)

    def _ensure_target_layer_id(self) -> str | None:
        if self._target_layer_id:
            return self._target_layer_id
        if not self._anchors and not self._pending_commands:
            return None
        created = self._create_vector_layer_id()
        self._target_layer_id = created
        return created

    def _clear_vector_selection(self) -> None:
        """Clear vector selection state on canvas when switching away."""
        canvas = getattr(self.host_api, "canvas", None) if self.host_api else None
        if canvas is None:
            return
        try:
            if hasattr(canvas, "_vector_selection"):
                canvas._vector_selection = None  # type: ignore[attr-defined]  # duck-typed canvas attr
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            logger.debug("vector selection clear failed", exc_info=True)

    def _vector_store(self) -> list[tuple[str | None, object]]:
        canvas = getattr(self.host_api, "canvas", None)
        if not canvas:
            return []
        getter = getattr(canvas, "get_vector_objects", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                logger.debug("get_vector_objects failed", exc_info=True)
        return []

    def _try_switch_to_edit_tool(self, pt: HostStrokePoint) -> bool:
        """If pt hits an existing vector object, switch to edit tool with auto-return."""
        if not self.host_api:
            return False
        store = self._vector_store()
        if not store:
            return False
        px, py = float(pt.pos[0]), float(pt.pos[1])
        threshold = VectorHitTestGeometry(self.host_api).canvas_radius()
        for layer_id, vec in store:
            if _point_hits_vector(px, py, vec, threshold):
                return self._activate_edit_tool_with_return(getattr(vec, "id", None), layer_id)
        return False

    def _activate_edit_tool_with_return(self, vec_id: str | None, layer_id: str | None) -> bool:
        """Switch to edit tool with auto-return to this tool."""
        host = self.host_api
        if not host:
            return False
        canvas = getattr(host, "canvas", None)
        if canvas and vec_id:
            with suppress(Exception):
                canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                    "id": vec_id,
                    "layer_id": layer_id,
                    "auto_return_tool": "vector_path_tool",
                    "auto_return_origin_id": vec_id,
                }
        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
        activate = getattr(tool_manager, "activate_tool", None) if tool_manager else None
        if not callable(activate):
            return False
        try:
            activate("vector_path_edit_tool")
            return True
        except Exception:
            return False


class VectorPathPlugin:
    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id="vector_path_tool",
            name=_ts("Vector Path Tool"),
            version="0.1.0",
            description=_ts("Click-based vector path tool"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self.tool = VectorPathTool()

    def __getattr__(self, name: str):
        return getattr(self.tool, name)

    def activate(self, host_api) -> None:
        self.tool.activate(host_api)

    def deactivate(self) -> None:
        self.tool.deactivate()


PLUGIN_METADATA = VectorPathPlugin().metadata


def create_plugin() -> VectorPathPlugin:
    return VectorPathPlugin()
