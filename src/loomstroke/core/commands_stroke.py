"""Stroke and brush commands."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

from .command_image_utils import _qimage_to_numpy_copy
from .command_types import BrushSettings, Command, DirtyRegion, StrokePoint

logger = logging.getLogger("loomstroke.core.commands")

if TYPE_CHECKING:
    from .canvas import Canvas
    from .layer import Layer


_TIP_ALPHA_CACHE: dict[tuple[str, int], NDArray[Any]] = {}
_HARDNESS_MASK_CACHE: dict[tuple[int, float, str], NDArray[Any]] = {}
_TIP_MASK_CACHE: dict[tuple[str, int, float, str], NDArray[Any]] = {}


def _tone_params_from_brush(
    brush: BrushSettings, *, white_level: float
) -> tuple[NDArray[Any], float, float, float]:
    color_values = brush.color if brush.color else (0, 0, 0)
    base_color = np.array(color_values[:3], dtype=np.float32)
    if white_level <= 1.0:
        base_color = base_color / 255.0
    tone_range = max(0.0, min(1.0, float(getattr(brush, "tone_range", 1.0))))
    tone_gamma = max(0.1, min(2.0, float(getattr(brush, "tone_gamma", 0.6))))
    return base_color, tone_range, tone_gamma, float(white_level)


def _tone_color_for_pressure(
    base_color: NDArray[Any],
    tone_range: float,
    tone_gamma: float,
    white_level: float,
    pressure: float,
) -> NDArray[Any]:
    p = max(0.0, min(1.0, float(pressure)))
    tone_pressure = p**tone_gamma
    tone_factor = (1.0 - tone_range) + tone_range * tone_pressure
    inv = 1.0 - tone_factor
    return cast("NDArray[Any]", base_color * tone_factor + white_level * inv)


def invalidate_tip_cache(tip_path: str) -> None:
    if not tip_path:
        return
    alpha_keys = [key for key in _TIP_ALPHA_CACHE if key[0] == tip_path]
    for alpha_key in alpha_keys:
        _TIP_ALPHA_CACHE.pop(alpha_key, None)
    mask_keys = [key for key in _TIP_MASK_CACHE if key[0] == tip_path]
    for mask_key in mask_keys:
        _TIP_MASK_CACHE.pop(mask_key, None)


def _radial_falloff_mask(size: int, hardness: float) -> NDArray[Any]:
    key = (int(size), float(round(hardness, 4)), "radial")
    cached = _HARDNESS_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    size = max(1, int(size))
    hardness = max(0.0, min(1.0, float(hardness)))
    if hardness >= 1.0:
        mask: NDArray[Any] = np.ones((size, size), dtype=np.float32)
        _HARDNESS_MASK_CACHE[key] = mask
        return mask
    radius = max(1.0, size / 2.0)
    yy, xx = np.mgrid[0:size, 0:size]
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    t = np.clip((r - hardness) / max(1e-6, 1.0 - hardness), 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    falloff = 1.0 - smooth
    mask = np.where(r <= hardness, 1.0, falloff)
    mask = np.where(r >= 1.0, 0.0, mask).astype(np.float32)
    _HARDNESS_MASK_CACHE[key] = mask
    return mask


def _load_tip_alpha(tip_path: str, size: int) -> NDArray[Any]:
    key = (tip_path, int(size))
    cached = _TIP_ALPHA_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QImage  # noqa: PLC0415
    except ImportError:
        alpha: NDArray[Any] = np.ones((max(1, size), max(1, size)), dtype=np.float32)
        _TIP_ALPHA_CACHE[key] = alpha
        return alpha
    image = QImage(tip_path)
    if image.isNull():
        alpha = np.ones((max(1, size), max(1, size)), dtype=np.float32)
        _TIP_ALPHA_CACHE[key] = alpha
        return alpha
    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if image.width() != size or image.height() != size:
        image = image.scaled(
            int(size),
            int(size),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    arr = _qimage_to_numpy_copy(image)
    if arr is None or arr.size == 0:
        alpha = np.ones((max(1, size), max(1, size)), dtype=np.float32)
        _TIP_ALPHA_CACHE[key] = alpha
        return alpha
    alpha = cast("NDArray[Any]", arr[:, :, 3].astype(np.float32) / 255.0)
    if alpha.min() >= 0.999:
        # No alpha channel - fallback to luminance
        b = arr[:, :, 0].astype(np.float32)
        g = arr[:, :, 1].astype(np.float32)
        r = arr[:, :, 2].astype(np.float32)
        alpha = (0.114 * b + 0.587 * g + 0.299 * r) / 255.0
    _TIP_ALPHA_CACHE[key] = alpha
    return alpha


def _load_tip_mask(tip_path: str, size: int, hardness: float, hardness_mode: str) -> NDArray[Any]:
    key = (tip_path, int(size), float(round(hardness, 4)), hardness_mode)
    cached = _TIP_MASK_CACHE.get(key)
    if cached is not None:
        return cached
    alpha = _load_tip_alpha(tip_path, size)
    if hardness_mode == "radial" and hardness < 1.0:
        alpha = alpha * _radial_falloff_mask(size, hardness)
    _TIP_MASK_CACHE[key] = alpha
    return alpha


class PaintCommand(Command):
    """
    Paint stroke command.
    Applies brush strokes to the active layer.
    """

    def __init__(
        self,
        points: Sequence[StrokePoint],
        brush: BrushSettings,
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        """
        Initialize paint command.

        Args:
            points: Stroke points to paint
            brush: Brush settings
            layer_id: Target layer ID (None for active layer)
            merge_key: Key for merging consecutive strokes
        """
        super().__init__(merge_key)
        self.points = list(points)
        self.brush = brush
        self.layer_id = layer_id
        self._backup_data: NDArray[Any] | None = None
        self._backup_region: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Canvas) -> bool:
        """Apply paint stroke to canvas."""
        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        # Calculate bounding box of stroke
        min_x = max(0, int(min(p.x - self.brush.size for p in self.points)))
        min_y = max(0, int(min(p.y - self.brush.size for p in self.points)))
        max_x = min(layer.width, int(max(p.x + self.brush.size for p in self.points)) + 1)
        max_y = min(layer.height, int(max(p.y + self.brush.size for p in self.points)) + 1)

        if min_x >= max_x or min_y >= max_y:
            return False

        selection_mask = None
        selection_bounds = None
        raw_mask = getattr(canvas, "_selection_mask", None)
        raw_bounds = getattr(canvas, "_selection_bounds", None)
        if isinstance(raw_mask, np.ndarray) and isinstance(raw_bounds, tuple):
            selection_mask = raw_mask
            selection_bounds = raw_bounds
        if selection_mask is not None and selection_bounds is not None:
            sel_x, sel_y, sel_w, sel_h = selection_bounds
            min_x = max(min_x, sel_x)
            min_y = max(min_y, sel_y)
            max_x = min(max_x, sel_x + sel_w)
            max_y = min(max_y, sel_y + sel_h)
            if min_x >= max_x or min_y >= max_y:
                return False

        # Store backup for undo
        self._backup_region = (min_x, min_y, max_x, max_y)
        self._backup_data = layer.buffer[min_y:max_y, min_x:max_x].copy()

        # Apply stroke (simplified for PR#1, actual implementation would use proper brush engine)
        self._apply_stroke_to_layer(
            layer,
            min_x,
            min_y,
            max_x,
            max_y,
            selection_mask=selection_mask,
        )

        # Mark dirty region
        self._dirty_region = DirtyRegion(min_x, min_y, max_x - min_x, max_y - min_y)
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Revert paint stroke from canvas."""
        if not self._executed or self._backup_data is None or self._backup_region is None:
            return False

        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        min_x, min_y, max_x, max_y = self._backup_region
        layer.buffer[min_y:max_y, min_x:max_x] = self._backup_data
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = False
        return True

    def _get_target_layer(self, canvas: Canvas) -> Layer | None:
        """Get the target layer for this command."""
        if self.layer_id:
            return canvas.get_layer_by_id(self.layer_id)
        return canvas.active_layer

    def _apply_stroke_to_layer(
        self,
        layer: Layer,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
        *,
        selection_mask: NDArray[Any] | None = None,
    ) -> None:
        """Apply stroke to layer buffer (simplified implementation)."""
        # This is a simplified implementation for PR#1
        # Real implementation would use proper brush engine with anti-aliasing
        base_color, tone_range, tone_gamma, white_level = _tone_params_from_brush(
            self.brush,
            white_level=1.0,
        )
        for point in self.points:
            x, y = int(point.x), int(point.y)
            radius = int(self.brush.size * point.pressure)
            pressure = max(0.0, min(1.0, point.pressure))

            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    px, py = x + dx, y + dy
                    if min_x <= px < max_x and min_y <= py < max_y:
                        dist_sq = dx * dx + dy * dy
                        if dist_sq <= radius * radius:
                            # Simple opacity calculation (pressure affects size, not opacity)
                            alpha = self.brush.opacity
                            if self.brush.hardness < 1.0:
                                edge_fade = 1.0 - (dist_sq**0.5) / radius
                                alpha *= max(0, min(1, edge_fade))
                            if selection_mask is not None:
                                mask_alpha = float(selection_mask[py, px]) / 255.0
                                if mask_alpha <= 0.0:
                                    continue
                                alpha *= mask_alpha
                            tone_color = _tone_color_for_pressure(
                                base_color,
                                tone_range,
                                tone_gamma,
                                white_level,
                                pressure,
                            )

                            # Apply color with alpha blending
                            current = layer.buffer[py, px, :3]
                            layer.buffer[py, px, :3] = current * (1 - alpha) + tone_color * alpha
                            layer.buffer[py, px, 3] = min(
                                255, layer.buffer[py, px, 3] + alpha * 255
                            )


class StampBrushCommand(Command):
    """Stamp-based brush command using a tip texture."""

    def __init__(
        self,
        points: Sequence[StrokePoint],
        brush: BrushSettings,
        *,
        tip_path: str,
        hardness_mode: str = "radial",
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key)
        self.points = list(points)
        self.brush = brush
        self.tip_path = tip_path
        self.hardness_mode = hardness_mode
        self.layer_id = layer_id
        self._backup_data: NDArray[Any] | None = None
        self._backup_region: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = self._get_target_layer(canvas)
        if not layer or not self.points or not self.tip_path:
            return False

        radii = [
            float(self.brush.size) * max(0.0, min(1.0, float(p.pressure))) for p in self.points
        ]
        min_x = max(0, int(min(p.x - r for p, r in zip(self.points, radii, strict=False))))
        min_y = max(0, int(min(p.y - r for p, r in zip(self.points, radii, strict=False))))
        max_x = min(
            layer.width,
            int(max(p.x + r for p, r in zip(self.points, radii, strict=False)) + 1),
        )
        max_y = min(
            layer.height,
            int(max(p.y + r for p, r in zip(self.points, radii, strict=False)) + 1),
        )

        if min_x >= max_x or min_y >= max_y:
            return False

        selection_mask = None
        selection_bounds = None
        raw_mask = getattr(canvas, "_selection_mask", None)
        raw_bounds = getattr(canvas, "_selection_bounds", None)
        if isinstance(raw_mask, np.ndarray) and isinstance(raw_bounds, tuple):
            selection_mask = raw_mask
            selection_bounds = raw_bounds
        if selection_mask is not None and selection_bounds is not None:
            sel_x, sel_y, sel_w, sel_h = selection_bounds
            min_x = max(min_x, sel_x)
            min_y = max(min_y, sel_y)
            max_x = min(max_x, sel_x + sel_w)
            max_y = min(max_y, sel_y + sel_h)
            if min_x >= max_x or min_y >= max_y:
                return False

        self._backup_region = (min_x, min_y, max_x, max_y)
        self._backup_data = layer.buffer[min_y:max_y, min_x:max_x].copy()

        self._apply_stamp_to_layer(
            layer,
            min_x,
            min_y,
            max_x,
            max_y,
            selection_mask=selection_mask,
        )

        self._dirty_region = DirtyRegion(min_x, min_y, max_x - min_x, max_y - min_y)
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)
        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        if not self._executed or self._backup_data is None or self._backup_region is None:
            return False
        layer = self._get_target_layer(canvas)
        if not layer:
            return False
        min_x, min_y, max_x, max_y = self._backup_region
        layer.buffer[min_y:max_y, min_x:max_x] = self._backup_data
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)
        self._executed = False
        return True

    def _get_target_layer(self, canvas: Canvas) -> Layer | None:
        if self.layer_id:
            return canvas.get_layer_by_id(self.layer_id)
        return canvas.active_layer

    def _apply_stamp_to_layer(
        self,
        layer: Layer,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
        *,
        selection_mask: NDArray[Any] | None = None,
    ) -> None:
        base_color, tone_range, tone_gamma, white_level = _tone_params_from_brush(
            self.brush,
            white_level=1.0,
        )
        opacity = max(0.0, min(1.0, float(self.brush.opacity)))
        flow = max(0.0, min(1.0, float(self.brush.flow)))
        for point in self.points:
            pressure = max(0.0, min(1.0, float(point.pressure)))
            radius = float(self.brush.size) * pressure
            stamp_size = max(1, round(radius * 2.0))
            if stamp_size <= 0:
                continue
            mask = _load_tip_mask(
                self.tip_path, stamp_size, float(self.brush.hardness), self.hardness_mode
            )
            half = stamp_size / 2.0
            origin_x = round(point.x - half)
            origin_y = round(point.y - half)

            for iy in range(mask.shape[0]):
                py = origin_y + iy
                if py < min_y or py >= max_y:
                    continue
                for ix in range(mask.shape[1]):
                    px = origin_x + ix
                    if px < min_x or px >= max_x:
                        continue
                    mask_alpha = float(mask[iy, ix])
                    if mask_alpha <= 0.0:
                        continue
                    alpha = mask_alpha * opacity * flow
                    if selection_mask is not None:
                        sel_alpha = float(selection_mask[py, px]) / 255.0
                        if sel_alpha <= 0.0:
                            continue
                        alpha *= sel_alpha
                    if alpha <= 0.0:
                        continue
                    tone_color = _tone_color_for_pressure(
                        base_color,
                        tone_range,
                        tone_gamma,
                        white_level,
                        pressure,
                    )
                    current = layer.buffer[py, px, :3]
                    layer.buffer[py, px, :3] = current * (1 - alpha) + tone_color * alpha
                    current_alpha = layer.buffer[py, px, 3]
                    layer.buffer[py, px, 3] = min(255, current_alpha + alpha * 255)


class EraseCommand(Command):
    """
    Erase stroke command.
    Removes content from the active layer.
    """

    def __init__(
        self,
        points: Sequence[StrokePoint],
        brush: BrushSettings,
        layer_id: str | None = None,
        merge_key: str | None = None,
    ) -> None:
        """
        Initialize erase command.

        Args:
            points: Stroke points to erase
            brush: Brush settings for eraser
            layer_id: Target layer ID (None for active layer)
            merge_key: Key for merging consecutive erases
        """
        super().__init__(merge_key)
        self.points = list(points)
        self.brush = brush
        self.layer_id = layer_id
        self._backup_data: NDArray[Any] | None = None
        self._backup_region: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Canvas) -> bool:
        """Apply erase stroke to canvas."""
        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        # Calculate bounding box
        min_x = max(0, int(min(p.x - self.brush.size for p in self.points)))
        min_y = max(0, int(min(p.y - self.brush.size for p in self.points)))
        max_x = min(layer.width, int(max(p.x + self.brush.size for p in self.points)) + 1)
        max_y = min(layer.height, int(max(p.y + self.brush.size for p in self.points)) + 1)

        if min_x >= max_x or min_y >= max_y:
            return False

        selection_mask = None
        selection_bounds = None
        raw_mask = getattr(canvas, "_selection_mask", None)
        raw_bounds = getattr(canvas, "_selection_bounds", None)
        if isinstance(raw_mask, np.ndarray) and isinstance(raw_bounds, tuple):
            selection_mask = raw_mask
            selection_bounds = raw_bounds
        if selection_mask is not None and selection_bounds is not None:
            sel_x, sel_y, sel_w, sel_h = selection_bounds
            min_x = max(min_x, sel_x)
            min_y = max(min_y, sel_y)
            max_x = min(max_x, sel_x + sel_w)
            max_y = min(max_y, sel_y + sel_h)
            if min_x >= max_x or min_y >= max_y:
                return False

        # Store backup for undo
        self._backup_region = (min_x, min_y, max_x, max_y)
        self._backup_data = layer.buffer[min_y:max_y, min_x:max_x].copy()

        # Apply eraser
        self._apply_erase_to_layer(
            layer,
            min_x,
            min_y,
            max_x,
            max_y,
            selection_mask=selection_mask,
        )

        # Mark dirty region
        self._dirty_region = DirtyRegion(min_x, min_y, max_x - min_x, max_y - min_y)
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = True
        return True

    def revert(self, canvas: Canvas) -> bool:
        """Revert erase stroke from canvas."""
        if not self._executed or self._backup_data is None or self._backup_region is None:
            return False

        layer = self._get_target_layer(canvas)
        if not layer:
            return False

        min_x, min_y, max_x, max_y = self._backup_region
        layer.buffer[min_y:max_y, min_x:max_x] = self._backup_data
        layer.mark_dirty(min_x, min_y, max_x - min_x, max_y - min_y)

        self._executed = False
        return True

    def _get_target_layer(self, canvas: Canvas) -> Layer | None:
        """Get the target layer for this command."""
        if self.layer_id:
            return canvas.get_layer_by_id(self.layer_id)
        return canvas.active_layer

    def _apply_erase_to_layer(
        self,
        layer: Layer,
        min_x: int,
        min_y: int,
        max_x: int,
        max_y: int,
        *,
        selection_mask: NDArray[Any] | None = None,
    ) -> None:
        """Apply eraser to layer buffer."""
        for point in self.points:
            x, y = int(point.x), int(point.y)
            radius = int(self.brush.size * point.pressure)

            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    px, py = x + dx, y + dy
                    if min_x <= px < max_x and min_y <= py < max_y:
                        dist_sq = dx * dx + dy * dy
                        if dist_sq <= radius * radius:
                            # Calculate erase strength
                            alpha = self.brush.opacity * point.pressure
                            if self.brush.hardness < 1.0:
                                edge_fade = 1.0 - (dist_sq**0.5) / radius
                                alpha *= max(0, min(1, edge_fade))
                            if selection_mask is not None:
                                mask_alpha = float(selection_mask[py, px]) / 255.0
                                if mask_alpha <= 0.0:
                                    continue
                                alpha *= mask_alpha

                            # Reduce alpha channel
                            layer.buffer[py, px, 3] = max(0, layer.buffer[py, px, 3] * (1 - alpha))
