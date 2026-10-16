"""Shared transform handle utilities for tool plugins."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from . import handles as _handles

HANDLE_CORNER_SIZE = _handles.HANDLE_CORNER_SIZE
HANDLE_DETECT_PAD = _handles.HANDLE_DETECT_PAD
HANDLE_EDGE_SIZE = _handles.HANDLE_EDGE_SIZE
HANDLE_ROTATION_OFFSET = _handles.HANDLE_ROTATION_OFFSET
handle_anchor_for_rect = _handles.handle_anchor_for_rect
handle_position_for_rect = _handles.handle_position_for_rect
handle_positions_for_bounds = _handles.handle_positions_for_bounds
handle_positions_for_rect = _handles.handle_positions_for_rect
hit_test_handle_points = _handles.hit_test_handle_points
point_in_radius = _handles.point_in_radius


# ---------------------------------------------------------------------------
# AABB-based handle hit-test
# ---------------------------------------------------------------------------

_CORNERS: dict[str, tuple[int, int, int, int]] = {
    # multiplier indices: (x_idx, y_idx) where 0=min, 1=max
    "corner-tl": (0, 0, 0, 0),
    "corner-tr": (1, 0, 1, 0),
    "corner-bl": (0, 1, 0, 1),
    "corner-br": (1, 1, 1, 1),
}


def hit_test_handle_aabb(  # noqa: PLR0911
    px: float,
    py: float,
    bounds: tuple[float, float, float, float],
    *,
    include_move_area: bool = True,
    corner_half_extent: float | None = None,
    edge_radius: float | None = None,
    rotation_radius: float | None = None,
    corner_inclusive: bool = True,
) -> tuple[str, str] | None:
    """Hit-test transform handles on an axis-aligned bounding box.

    *bounds* is ``(min_x, min_y, max_x, max_y)``.
    Returns ``(handle_name, mode)`` or ``None``.
    """
    min_x, min_y, max_x, max_y = bounds
    return hit_test_handle_points(
        px,
        py,
        handle_positions_for_bounds(bounds, include_rotate=True),
        include_move_area=include_move_area,
        contains_move_area=lambda x, y: min_x <= x <= max_x and min_y <= y <= max_y,
        corner_half_extent=corner_half_extent,
        edge_radius=edge_radius,
        rotation_radius=rotation_radius,
        corner_inclusive=corner_inclusive,
        rotation_first=True,
    )


# ---------------------------------------------------------------------------
# Pivot / scale computation
# ---------------------------------------------------------------------------


def compute_pivot(  # noqa: PLR0911
    handle: str,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Return the pivot point opposite to *handle* within *bounds*.

    *bounds* is ``(min_x, min_y, max_x, max_y)``.
    """
    min_x, min_y, max_x, max_y = bounds
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    if handle == "corner-tl":
        return (max_x, max_y)
    if handle == "corner-tr":
        return (min_x, max_y)
    if handle == "corner-bl":
        return (max_x, min_y)
    if handle == "corner-br":
        return (min_x, min_y)
    if handle == "edge-top":
        return (cx, max_y)
    if handle == "edge-bottom":
        return (cx, min_y)
    if handle == "edge-left":
        return (max_x, cy)
    if handle == "edge-right":
        return (min_x, cy)
    return (cx, cy)


def compute_scale_factors(
    *,
    handle: str,
    pivot: tuple[float, float],
    press_vec: tuple[float, float] | None,
    current: tuple[float, float],
    bbox: tuple[float, float, float, float],
    uniform: bool,
) -> tuple[float, float]:
    """Compute (sx, sy) scale factors for a handle drag.

    *press_vec* is the vector from pivot to the initial press point.
    *current* is the current pointer position.
    *bbox* is ``(min_x, min_y, max_x, max_y)``.
    """
    if press_vec is None:
        return (1.0, 1.0)
    base_dx, base_dy = press_vec
    cur_dx = current[0] - pivot[0]
    cur_dy = current[1] - pivot[1]
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    eps = 1e-3
    sx = cur_dx / base_dx if abs(base_dx) > eps else 1.0
    sy = cur_dy / base_dy if abs(base_dy) > eps else 1.0
    if handle in {"edge-top", "edge-bottom"}:
        sx = 1.0
    if handle in {"edge-left", "edge-right"}:
        sy = 1.0
    min_sx = 1.0 / max(width, 1.0) if width > eps else 0.1
    min_sy = 1.0 / max(height, 1.0) if height > eps else 0.1
    sign_x = -1.0 if sx < 0 else 1.0
    sign_y = -1.0 if sy < 0 else 1.0
    sx = sign_x * max(abs(sx), min_sx)
    sy = sign_y * max(abs(sy), min_sy)
    if uniform and handle.startswith("corner"):
        factor = max(abs(sx), abs(sy))
        sx = sign_x * factor
        sy = sign_y * factor
    return (sx, sy)


@dataclass(slots=True)
class TransformGestureState:
    """SDK-owned pointer gesture state for simple move/scale/rotate transforms."""

    mode: str
    handle: str
    start_bounds: tuple[float, float, float, float]
    press_point: tuple[float, float]
    pivot: tuple[float, float]
    press_vec: tuple[float, float] | None = None


@dataclass(slots=True)
class TransformGesturePreview:
    """Pure preview result from a transform gesture update."""

    bounds: tuple[float, float, float, float]
    angle_delta_deg: float = 0.0


def make_transform_gesture_state(
    *,
    mode: str,
    handle: str,
    bounds: tuple[float, float, float, float],
    press_point: tuple[float, float],
) -> TransformGestureState:
    """Create transform gesture state from initial handle and pointer data."""
    pivot = compute_pivot(handle, bounds)
    press_vec = None
    if mode == "scale":
        press_vec = (press_point[0] - pivot[0], press_point[1] - pivot[1])
    return TransformGestureState(
        mode=str(mode),
        handle=str(handle),
        start_bounds=bounds,
        press_point=press_point,
        pivot=pivot,
        press_vec=press_vec,
    )


def preview_transform_gesture(
    state: TransformGestureState,
    current_point: tuple[float, float],
    *,
    uniform: bool = False,
) -> TransformGesturePreview:
    """Return a pure preview for a move/scale/rotate gesture update."""
    min_x, min_y, max_x, max_y = state.start_bounds
    if state.mode == "move":
        dx = current_point[0] - state.press_point[0]
        dy = current_point[1] - state.press_point[1]
        return TransformGesturePreview(bounds=(min_x + dx, min_y + dy, max_x + dx, max_y + dy))
    if state.mode == "scale":
        sx, sy = compute_scale_factors(
            handle=state.handle,
            pivot=state.pivot,
            press_vec=state.press_vec,
            current=current_point,
            bbox=state.start_bounds,
            uniform=uniform,
        )
        corners = (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        )
        transformed = tuple(
            (
                state.pivot[0] + (cx - state.pivot[0]) * sx,
                state.pivot[1] + (cy - state.pivot[1]) * sy,
            )
            for cx, cy in corners
        )
        xs = [point[0] for point in transformed]
        ys = [point[1] for point in transformed]
        return TransformGesturePreview(
            bounds=(min(xs), min(ys), max(xs), max(ys)),
        )
    if state.mode == "rotate":
        angle = math.atan2(
            current_point[1] - state.pivot[1],
            current_point[0] - state.pivot[0],
        ) - math.atan2(
            state.press_point[1] - state.pivot[1],
            state.press_point[0] - state.pivot[0],
        )
        if uniform:
            snap = math.radians(15.0)
            angle = round(angle / snap) * snap
        return TransformGesturePreview(
            bounds=state.start_bounds,
            angle_delta_deg=math.degrees(angle),
        )
    return TransformGesturePreview(bounds=state.start_bounds)


class TransformStateMachine:
    """Small overridable transform gesture pipeline for tool controllers."""

    def __init__(self) -> None:
        self.state: TransformGestureState | None = None
        self.last_preview: TransformGesturePreview | None = None

    def begin(self, state: TransformGestureState) -> TransformGesturePreview:
        self.state = state
        preview = TransformGesturePreview(bounds=state.start_bounds)
        self.last_preview = preview
        self.apply_preview(preview)
        return preview

    def update(
        self,
        current_point: tuple[float, float],
        *,
        uniform: bool = False,
    ) -> TransformGesturePreview | None:
        state = self.state
        if state is None:
            return None
        preview = preview_transform_gesture(state, current_point, uniform=uniform)
        self.last_preview = preview
        self.apply_preview(preview)
        return preview

    def commit(self) -> Any:
        state = self.state
        preview = self.last_preview
        self.state = None
        self.last_preview = None
        if state is None or preview is None:
            return None
        return self.build_commit(state, preview)

    def cancel(self) -> None:
        state = self.state
        self.state = None
        self.last_preview = None
        if state is not None:
            self.cancel_preview(state)

    def apply_preview(self, preview: TransformGesturePreview) -> None:
        del preview

    def build_commit(
        self,
        state: TransformGestureState,
        preview: TransformGesturePreview,
    ) -> Any:
        del state, preview
        return None

    def cancel_preview(self, state: TransformGestureState) -> None:
        del state
