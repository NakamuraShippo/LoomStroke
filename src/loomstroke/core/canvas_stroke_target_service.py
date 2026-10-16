"""Stroke target and input normalization helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor

from loomstroke.core.commands import EraseCommand, StampBrushCommand

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasStrokeTargetService:
    """Resolve stroke targets and lightweight stroke input state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def resolve_erase_target(self, cmd: EraseCommand) -> Any | None:
        owner = self._owner
        layer_id = getattr(cmd, "layer_id", None)
        layer = owner._get_layer_by_id(layer_id) if isinstance(layer_id, str) else None
        if layer is None:
            layer = owner._get_active_layer()
            if layer is None:
                logger.error("No active layer for erasing")
                return None

        if layer.layer_type == "vector" and not owner._mask_edit_flag:
            logger.info("Layer %s is vector; erase skipped", layer.name)
            return None
        if layer.locked:
            logger.info("Layer %s is locked; ignoring erase command", layer.name)
            return None
        if layer.alpha_lock:
            logger.info("Layer %s has alpha lock; erase blocked", layer.name)
            return None
        if layer.image is None:
            logger.error("Layer %s has no image buffer", layer.name)
            return None
        return layer

    @staticmethod
    def stroke_points_from_command(cmd: object) -> list[Any]:
        points = getattr(cmd, "points", []) or []
        return list(points)

    def apply_selection_clip(self, bounds: QRect) -> QRect | None:
        selection_mask, selection_bounds = self._owner._selection_clip_state()
        if selection_mask is None or selection_bounds is None:
            return bounds
        clipped = bounds.intersected(selection_bounds)
        if clipped.isEmpty():
            return None
        return clipped

    def resolve_paint_target(self) -> Any | None:
        owner = self._owner
        layer = owner._get_active_layer()
        if layer is None:
            logger.error("No active layer for painting")
            return None
        if layer.layer_type == "vector" and not owner._mask_edit_flag:
            logger.info("Active layer %s is vector; paint skipped", layer.name)
            return None
        if layer.locked:
            logger.info("Layer %s is locked; ignoring paint command", layer.name)
            return None
        if layer.image is None:
            logger.error("Layer %s has no image buffer", layer.name)
            return None
        return layer

    @staticmethod
    def paint_color_from_brush(brush: Any) -> QColor:
        color = QColor(0, 0, 0)
        if hasattr(brush, "color") and brush.color and len(brush.color) >= 3:
            color = QColor(brush.color[0], brush.color[1], brush.color[2])
            if len(brush.color) >= 4:
                color.setAlpha(brush.color[3])
        return color

    def stamp_target_layer(self, cmd: StampBrushCommand) -> Any | None:
        owner = self._owner
        layer = owner._get_active_layer()
        if layer is None:
            logger.error("No active layer for stamping")
            return None
        if layer.layer_type == "vector" and not owner._mask_edit_flag:
            logger.info("Active layer %s is vector; stamp skipped", layer.name)
            return None
        if layer.locked:
            logger.info("Layer %s is locked; stamp ignored", layer.name)
            return None
        if layer.image is None:
            logger.error("Layer %s has no image buffer", layer.name)
            return None
        return layer

    @staticmethod
    def stamp_points(cmd: StampBrushCommand) -> list[Any]:
        return list(getattr(cmd, "points", []))

    def stamp_bounds_and_selection_alpha(
        self,
        points: list[Any],
        brush: Any,
    ) -> tuple[QRect | None, NDArray[Any] | None]:
        owner = self._owner
        bounds = self.compute_stamp_bounds(points, brush)
        if bounds is None:
            return None, None

        selection_mask, selection_bounds = owner._selection_clip_state()
        if selection_mask is not None and selection_bounds is not None:
            bounds = bounds.intersected(selection_bounds)
            if bounds.isEmpty():
                return None, None

        selection_alpha = None
        if selection_mask is not None:
            mask_array = owner._selection_mask_to_numpy(selection_mask)
            if mask_array is not None:
                selection_alpha = mask_array.astype(np.float32) / 255.0
        return bounds, selection_alpha

    def compute_stroke_bounds(self, points: Sequence[Any], brush: Any) -> QRect | None:
        coordinates: list[tuple[float, float]] = []
        for point in points:
            coords = self.point_coordinates(point)
            if coords is not None:
                coordinates.append(coords)

        if not coordinates:
            return None

        size = float(getattr(brush, "size", 1.0) or 1.0)
        padding = int(max(2.0, size)) + 2
        xs = [c[0] for c in coordinates]
        ys = [c[1] for c in coordinates]
        min_x = min(xs) - padding
        max_x = max(xs) + padding
        min_y = min(ys) - padding
        max_y = max(ys) + padding

        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return None

        rect = QRect(
            math.floor(min_x),
            math.floor(min_y),
            math.ceil(width),
            math.ceil(height),
        )
        return self.clamp_rect(rect)

    def compute_stamp_bounds(self, points: Sequence[Any], brush: Any) -> QRect | None:
        coordinates: list[tuple[float, float]] = []
        radii: list[float] = []
        size = float(getattr(brush, "size", 1.0) or 1.0)
        for point in points:
            coords = self.point_coordinates(point)
            if coords is None:
                continue
            pressure = max(0.0, min(1.0, float(getattr(point, "pressure", 1.0))))
            coordinates.append(coords)
            radii.append(size * pressure)

        if not coordinates:
            return None

        min_x = min(c[0] - r for c, r in zip(coordinates, radii, strict=False))
        max_x = max(c[0] + r for c, r in zip(coordinates, radii, strict=False))
        min_y = min(c[1] - r for c, r in zip(coordinates, radii, strict=False))
        max_y = max(c[1] + r for c, r in zip(coordinates, radii, strict=False))

        width = max_x - min_x
        height = max_y - min_y
        if width <= 0 or height <= 0:
            return None

        rect = QRect(
            math.floor(min_x),
            math.floor(min_y),
            math.ceil(width),
            math.ceil(height),
        )
        return self.clamp_rect(rect)

    @staticmethod
    def point_coordinates(point: Any) -> tuple[float, float] | None:
        if hasattr(point, "x") and hasattr(point, "y"):
            try:
                return float(point.x), float(point.y)
            except Exception:  # pragma: no cover - defensive
                return None
        pos = getattr(point, "pos", None)
        if (
            isinstance(pos, Sequence)
            and len(pos) >= 2
            and isinstance(pos[0], (int, float))
            and isinstance(pos[1], (int, float))
        ):
            return float(pos[0]), float(pos[1])
        return None

    def clamp_rect(self, rect: QRect) -> QRect | None:
        canvas_rect = QRect(0, 0, self._owner._width, self._owner._height)
        clamped = rect.intersected(canvas_rect)
        if clamped.isEmpty():
            return None
        return clamped
