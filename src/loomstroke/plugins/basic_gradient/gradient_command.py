"""Gradient fill command for undo/redo support."""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np

from ...core.commands import (
    Command,
    DirtyRegion,
    _ensure_qimage_premultiplied,
    _is_qimage_layer,
    _layer_allows_edit,
    _layer_offset,
    _layer_transform,
    _qimage_to_numpy_view,
    _resolve_target_layer,
)
from ...core.geometry import Affine2D

# Gradient types
GRADIENT_LINEAR = "linear"
GRADIENT_RADIAL = "radial"


# Type alias for gradient color stops: list of (position, rgba) pairs.
# Position is 0.0-1.0, rgba values are 0-255.
GradientStops = list[tuple[float, tuple[float, float, float, float]]]


def _gradient_coordinates(width: int, height: int, mapping: Affine2D | None):
    yy, xx = np.mgrid[:height, :width].astype(np.float32)
    if mapping is None:
        return xx, yy
    return (
        mapping.a * xx + mapping.b * yy + mapping.tx,
        mapping.c * xx + mapping.d * yy + mapping.ty,
    )


def _render_gradient(
    width: int,
    height: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    color_a: tuple[float, float, float, float],
    color_b: tuple[float, float, float, float],
    gradient_type: str,
    dither: bool,
    stops: GradientStops | None = None,
    *,
    pixel_transform: Affine2D | None = None,
) -> np.ndarray:
    """Render a gradient into an HxWx4 float32 RGBA array (0-255 range).

    If *stops* is provided, it defines intermediate color stops between
    *color_a* (pos=0) and *color_b* (pos=1).  Each entry is
    ``(position, (r, g, b, a))`` with values in the 0-255 range.
    """
    xx, yy = _gradient_coordinates(width, height, pixel_transform)

    if gradient_type == GRADIENT_RADIAL:
        dx = x1 - x0
        dy = y1 - y0
        radius = np.sqrt(dx * dx + dy * dy)
        radius = max(radius, 1.0)
        dist = np.sqrt((xx - x0) ** 2 + (yy - y0) ** 2)
        t = np.clip(dist / radius, 0.0, 1.0)
    else:
        # Linear gradient
        dx = x1 - x0
        dy = y1 - y0
        length_sq = dx * dx + dy * dy
        length_sq = max(length_sq, 1.0)
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / length_sq, 0.0, 1.0)

    if dither:
        noise = np.random.default_rng(42).uniform(-0.5, 0.5, t.shape).astype(np.float32)
        t = np.clip(t + noise / 255.0, 0.0, 1.0)

    # Build full stop list (always includes endpoints)
    all_stops: list[tuple[float, np.ndarray]] = [
        (0.0, np.array(color_a, dtype=np.float32)),
    ]
    if stops:
        for pos, rgba in sorted(stops, key=lambda s: s[0]):
            all_stops.append((float(pos), np.array(rgba, dtype=np.float32)))
    all_stops.append((1.0, np.array(color_b, dtype=np.float32)))

    if len(all_stops) == 2:
        # Fast path: simple two-color gradient
        ca, cb = all_stops[0][1], all_stops[1][1]
        result = np.empty((height, width, 4), dtype=np.float32)
        for ch in range(4):
            result[:, :, ch] = ca[ch] + (cb[ch] - ca[ch]) * t
        return result

    # Multi-stop: piecewise linear interpolation
    result = np.empty((height, width, 4), dtype=np.float32)
    for i in range(len(all_stops) - 1):
        p0, c0 = all_stops[i]
        p1, c1 = all_stops[i + 1]
        span = max(p1 - p0, 1e-6)
        # Mask for pixels in this segment
        if i == 0:
            mask = t < p1
        elif i == len(all_stops) - 2:
            mask = t >= p0
        else:
            mask = (t >= p0) & (t < p1)
        local_t = np.clip((t[mask] - p0) / span, 0.0, 1.0)
        for ch in range(4):
            result[:, :, ch][mask] = c0[ch] + (c1[ch] - c0[ch]) * local_t

    return result


class GradientCommand(Command):
    """Apply a gradient fill to the active layer."""

    def __init__(
        self,
        layer_id: str | None,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color_a: tuple[float, float, float, float],
        color_b: tuple[float, float, float, float],
        gradient_type: str = GRADIENT_LINEAR,
        dither: bool = True,
        *,
        stops: GradientStops | None = None,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self._layer_id = layer_id
        self._x0 = x0
        self._y0 = y0
        self._x1 = x1
        self._y1 = y1
        self._color_a = color_a
        self._color_b = color_b
        self._gradient_type = gradient_type
        self._dither = dither
        self._stops = stops
        self._backup: np.ndarray | None = None

    def set_pre_applied(self, backup: np.ndarray) -> None:
        """Mark this command as already applied during preview.

        *backup* is the original layer buffer before the gradient was drawn.
        ``apply()`` will be a no-op, but ``revert()`` will restore *backup*.
        """
        self._backup = backup
        self._executed = True
        h, w = backup.shape[:2]
        self._dirty_region = DirtyRegion(0, 0, w, h)

    def apply(self, canvas: Any) -> bool:
        if self._executed:
            return True  # Already applied during preview

        layer = _resolve_target_layer(canvas, self._layer_id)
        if layer is None or not _layer_allows_edit(layer):
            return False

        target = _get_target_array(layer)
        if target is None:
            return False

        height, width = target.shape[:2]
        mapping = _layer_transform(layer).to_affine()
        if mapping.inverse() is None:
            return False
        offset_x, offset_y = _layer_offset(layer)

        # Backup for undo
        self._backup = target.copy()

        is_qimage = _is_qimage_layer(layer)
        gradient = _render_gradient(
            width,
            height,
            self._x0,
            self._y0,
            self._x1,
            self._y1,
            self._color_a,
            self._color_b,
            self._gradient_type,
            self._dither,
            stops=self._stops,
            pixel_transform=mapping,
        )

        if is_qimage:
            # QImage is BGRA premultiplied
            r, g, b = gradient[:, :, 0], gradient[:, :, 1], gradient[:, :, 2]
            a = gradient[:, :, 3] / 255.0
            src_b = (b * a).astype(np.uint8)
            src_g = (g * a).astype(np.uint8)
            src_r = (r * a).astype(np.uint8)
            src_a = gradient[:, :, 3].astype(np.uint8)

            dst_a = target[:, :, 3].astype(np.float32) / 255.0
            out_a = a + dst_a * (1.0 - a)

            for ch_src, ch_idx in [(src_b, 0), (src_g, 1), (src_r, 2)]:
                dst_ch = target[:, :, ch_idx].astype(np.float32)
                target[:, :, ch_idx] = np.clip(ch_src + dst_ch * (1.0 - a), 0.0, 255.0).astype(
                    np.uint8
                )
            target[:, :, 3] = np.clip(out_a * 255.0, 0.0, 255.0).astype(np.uint8)
        else:
            # Float32 RGBA buffer (0-255 range)
            src_a = gradient[:, :, 3] / 255.0
            dst_a = target[:, :, 3] / 255.0
            out_a = src_a + dst_a * (1.0 - src_a)

            for ch in range(3):
                safe_a = np.where(out_a > 0, out_a, 1.0)
                target[:, :, ch] = np.where(
                    out_a > 0,
                    (gradient[:, :, ch] * src_a + target[:, :, ch] * dst_a * (1.0 - src_a))
                    / safe_a,
                    0.0,
                )
            target[:, :, 3] = out_a * 255.0

        self._dirty_region = DirtyRegion(offset_x, offset_y, width, height)
        if hasattr(layer, "mark_dirty"):
            with contextlib.suppress(Exception):
                layer.mark_dirty(0, 0, width, height)

        # Bump texture_version for cache invalidation
        tv = getattr(layer, "texture_version", None)
        if tv is not None:
            layer.texture_version = tv + 1

        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or self._backup is None:
            return False
        layer = _resolve_target_layer(canvas, self._layer_id)
        if layer is None:
            return False
        target = _get_target_array(layer)
        if target is None:
            return False

        target[:] = self._backup
        self._dirty_region = DirtyRegion(*_layer_offset(layer), target.shape[1], target.shape[0])
        if hasattr(layer, "mark_dirty"):
            with contextlib.suppress(Exception):
                layer.mark_dirty(0, 0, target.shape[1], target.shape[0])

        tv = getattr(layer, "texture_version", None)
        if tv is not None:
            layer.texture_version = tv + 1

        self._executed = False
        return True


def _get_target_array(layer: Any) -> np.ndarray | None:
    """Get a writable numpy array for the given layer."""
    if _is_qimage_layer(layer):
        image = _ensure_qimage_premultiplied(layer)
        if image is None:
            return None
        return _qimage_to_numpy_view(image)
    buffer = getattr(layer, "buffer", None)
    if isinstance(buffer, np.ndarray):
        return buffer
    return None
