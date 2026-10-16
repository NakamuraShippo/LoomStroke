from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...core.commands import SelectionOp
from ...host_api import StrokePoint
from ...plugins.vector_tools.common import VectorObject

if TYPE_CHECKING:
    from .rect_tool import BasicSelectionRectTool


@dataclass(slots=True)
class VectorSelectionState:
    drag_mode: str = "none"
    transform_mode_override: str | None = None
    drag_items: list[dict[str, Any]] = field(default_factory=list)
    drag_originals: list[tuple[str, str | None, VectorObject]] = field(default_factory=list)
    drag_bounds: tuple[float, float, float, float] | None = None
    drag_start: tuple[float, float] | None = None
    drag_handle: str | None = None
    drag_pivot: tuple[float, float] | None = None
    drag_press_vec: tuple[float, float] | None = None
    last_delta: tuple[float, float] = (0.0, 0.0)


class VectorSelectionController:
    """Owns vector selection/transform state for the rect tool."""

    def __init__(self, tool: BasicSelectionRectTool) -> None:
        self._tool = tool
        self.state = VectorSelectionState()

    def reset_pointer_state(self) -> None:
        self.state.drag_mode = "none"
        self.state.transform_mode_override = None
        self.state.drag_items = []
        self.state.drag_originals = []
        self.state.drag_bounds = None
        self.state.drag_start = None
        self.state.drag_handle = None
        self.state.drag_pivot = None
        self.state.drag_press_vec = None
        self.state.last_delta = (0.0, 0.0)

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del ctrl, meta
        tool = self._tool
        if not tool.host_api:
            return
        if tool._transform_active:
            tool._cancel_transform_session()
        tool._start = pt
        tool._current = pt
        tool._press_pos = (pt.pos[0], pt.pos[1])
        tool._initial_modifiers = {"shift": bool(shift), "alt": bool(alt)}
        tool._current_modifiers = dict(tool._initial_modifiers)
        tool._capture_base_selection()

        if tool.host_api:
            tool.host_api.input.pointer_capture(tool.descriptor().id)

        op = tool._op_from_modifiers(tool._initial_modifiers)
        bounds = tool._vector_selection_bounds_from_canvas()
        if op == SelectionOp.REPLACE and bounds:
            override = tool._vector_transform_mode_override
            if override in {"move", "scale", "rotate"}:
                handle = {
                    "move": "move-area",
                    "scale": "corner-br",
                    "rotate": "rotate",
                }[override]
                if tool._begin_vector_transform(pt, override, handle, bounds):
                    return
                tool._vector_transform_mode_override = None
            hit = tool._hit_test_vector_transform_handle(pt, bounds)
            if hit and tool._begin_vector_transform(pt, hit[1], hit[0], bounds):
                return
            if tool._point_in_bounds(pt, bounds) and tool._begin_vector_move(pt):
                return

        tool._vector_drag_mode = "select"

    def on_pointer_move(self, points: list[StrokePoint]) -> None:
        tool = self._tool
        if not tool._start or not points:
            return
        tool._current = points[-1]
        if tool._vector_drag_mode in {"move", "scale", "rotate"}:
            tool._update_vector_transform_preview(points[-1])
            return
        if not tool._preview_enabled:
            return
        rect, full_rect = tool._compute_rect_geometry_with_full(
            tool._start,
            tool._current,
            square=False,
            center=False,
        )
        if rect is None and full_rect is None:
            tool._restore_base_selection(recompose=True)
            tool._set_vector_selection_preview_rect(None)
            return
        preview_rect = rect or full_rect
        tool._set_vector_selection_preview_rect(preview_rect)

    def on_pointer_up(self, pt: StrokePoint) -> None:
        tool = self._tool
        if not tool._start or not tool.host_api:
            return
        tool._current = pt
        if tool._vector_drag_mode in {"move", "scale", "rotate"}:
            tool._finish_vector_transform(pt)
            return

        rect, full_rect = tool._compute_rect_geometry_with_full(
            tool._start,
            tool._current,
            square=False,
            center=False,
        )
        is_click = not tool._movement_exceeded_threshold(pt)
        if is_click:
            rect = (int(pt.pos[0]), int(pt.pos[1]), 1, 1)
            full_rect = rect

        if rect is None and full_rect is None:
            tool._restore_base_selection(recompose=True)
            tool._finish_pointer()
            return

        select_rect = rect or full_rect
        if select_rect is None:
            tool._restore_base_selection(recompose=True)
            tool._finish_pointer()
            return

        op = tool._op_from_modifiers(tool._initial_modifiers)
        existing = tool._vector_selection_items()
        picked = tool._vector_items_from_rect(select_rect)
        items = tool._vector_apply_op(existing, picked, op)
        bounds = tool._vector_selection_bounds_for_items(items)
        tool._set_vector_selection(items, bounds)
        if tool._base_mask is None and tool._base_bounds is None:
            tool._clear_raster_selection(recompose=True)
        else:
            tool._restore_base_selection(recompose=True)
        tool._finish_pointer()
