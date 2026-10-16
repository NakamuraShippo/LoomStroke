"""Layer transform math helpers for the basic move tool."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...core.geometry import Affine2D
from ...plugins.sdk.handles import handle_anchor_for_rect, handle_position_for_rect

if TYPE_CHECKING:
    from ...core.selection_clipboard import SelectionPayload


@dataclass(slots=True)
class LayerTransformState:
    layer: Any
    layer_id: str | None
    payload: SelectionPayload
    original_image: Any
    original_mask: Any
    local_mask: Any
    had_original_mask: bool
    original_offset: tuple[int, int]
    original_transform: Any
    local_bounds: tuple[int, int, int, int]
    matrix: Affine2D
    mode: str
    handle: str
    pointer0: tuple[float, float]
    matrix0: Affine2D
    anchor_local: tuple[float, float]
    anchor_canvas: tuple[float, float]
    v0: tuple[float, float] | None
    axis_x: tuple[float, float] | None
    axis_y: tuple[float, float] | None
    den_x: float
    den_y: float
    handle_local: tuple[float, float] | None
    original_visible: bool


class LayerTransformController:
    """Pure Affine2D calculations for direct raster layer transforms."""

    @staticmethod
    def bounds_from_transform(
        transform: Affine2D, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float, float, float] | None:
        x, y, w, h = local_bounds
        if w <= 0 or h <= 0:
            return None
        points = (
            transform.apply(float(x), float(y)),
            transform.apply(float(x + w), float(y)),
            transform.apply(float(x + w), float(y + h)),
            transform.apply(float(x), float(y + h)),
        )
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def transform_changed(matrix: Affine2D, original: Affine2D) -> bool:
        return any(
            abs(a - b) > 1e-4
            for a, b in (
                (matrix.a, original.a),
                (matrix.b, original.b),
                (matrix.c, original.c),
                (matrix.d, original.d),
                (matrix.tx, original.tx),
                (matrix.ty, original.ty),
            )
        )

    @staticmethod
    def anchor_for_handle(
        handle: str, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float]:
        return handle_anchor_for_rect(handle, local_bounds)

    @staticmethod
    def handle_local_for_payload(
        handle: str, local_bounds: tuple[int, int, int, int]
    ) -> tuple[float, float] | None:
        return handle_position_for_rect(handle, local_bounds)

    @staticmethod
    def dot(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1]

    @staticmethod
    def cross(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[1] - a[1] * b[0]

    @staticmethod
    def length(v: tuple[float, float]) -> float:
        return math.hypot(v[0], v[1])

    @staticmethod
    def normalize_vec(v: tuple[float, float]) -> tuple[float, float] | None:
        length = math.hypot(v[0], v[1])
        if length <= 1e-8:
            return None
        return (v[0] / length, v[1] / length)

    def update_matrix(
        self,
        state: LayerTransformState,
        point: tuple[float, float],
        *,
        shift_constrain: bool,
    ) -> bool:
        matrix0 = state.matrix0
        cx, cy = point
        if state.mode == "rotate":
            return self._update_rotation_matrix(
                state,
                matrix0,
                cx,
                cy,
                shift_constrain=shift_constrain,
            )
        if state.mode == "scale":
            return self._update_scale_matrix(
                state,
                matrix0,
                cx,
                cy,
                shift_constrain=shift_constrain,
            )
        return False

    def _update_rotation_matrix(
        self,
        state: LayerTransformState,
        matrix0: Affine2D,
        cx: float,
        cy: float,
        *,
        shift_constrain: bool,
    ) -> bool:
        v0 = state.v0
        if v0 is None:
            return False
        v1 = (cx - state.anchor_canvas[0], cy - state.anchor_canvas[1])
        if self.length(v0) < 1e-6 or self.length(v1) < 1e-6:
            return False
        dtheta = math.atan2(self.cross(v0, v1), self.dot(v0, v1))
        if shift_constrain:
            snap = math.radians(15.0)
            dtheta = round(dtheta / snap) * snap
        cos_a = math.cos(dtheta)
        sin_a = math.sin(dtheta)
        state.matrix = Affine2D(
            cos_a * matrix0.a - sin_a * matrix0.c,
            cos_a * matrix0.b - sin_a * matrix0.d,
            sin_a * matrix0.a + cos_a * matrix0.c,
            sin_a * matrix0.b + cos_a * matrix0.d,
            cos_a * (matrix0.tx - state.anchor_canvas[0])
            - sin_a * (matrix0.ty - state.anchor_canvas[1])
            + state.anchor_canvas[0],
            sin_a * (matrix0.tx - state.anchor_canvas[0])
            + cos_a * (matrix0.ty - state.anchor_canvas[1])
            + state.anchor_canvas[1],
        )
        return True

    def _update_scale_matrix(
        self,
        state: LayerTransformState,
        matrix0: Affine2D,
        cx: float,
        cy: float,
        *,
        shift_constrain: bool,
    ) -> bool:
        if state.axis_x is None or state.axis_y is None:
            return False
        v = (cx - state.anchor_canvas[0], cy - state.anchor_canvas[1])
        sx = self.dot(v, state.axis_x) / state.den_x if abs(state.den_x) > 1e-6 else 1.0
        sy = self.dot(v, state.axis_y) / state.den_y if abs(state.den_y) > 1e-6 else 1.0
        if state.handle in {"edge-top", "edge-bottom"}:
            sx = 1.0
        if state.handle in {"edge-left", "edge-right"}:
            sy = 1.0
        sign_x = -1.0 if sx < 0 else 1.0
        sign_y = -1.0 if sy < 0 else 1.0
        if shift_constrain and state.handle.startswith("corner"):
            s = max(abs(sx), abs(sy))
            sx = sign_x * s
            sy = sign_y * s
        sx = sign_x * max(abs(sx), 1e-3)
        sy = sign_y * max(abs(sy), 1e-3)
        a = matrix0.a * sx
        b = matrix0.b * sy
        c = matrix0.c * sx
        d = matrix0.d * sy
        state.matrix = Affine2D(
            a,
            b,
            c,
            d,
            state.anchor_canvas[0] - (a * state.anchor_local[0] + b * state.anchor_local[1]),
            state.anchor_canvas[1] - (c * state.anchor_local[0] + d * state.anchor_local[1]),
        )
        return True
