"""
Bucket fill command implementations.

These commands compute flood fill spans and apply them via the core command
pipeline, preserving undo/redo semantics.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from ..commands import (
    Command,
    DirtyRegion,
    UpdateSelectionMaskCommand,
    _clip_bounds_to_canvas,
    _ensure_layer_bounds,
    _ensure_qimage_premultiplied,
    _is_qimage_layer,
    _layer_allows_edit,
    _layer_offset,
    _qimage_to_numpy_copy,
    _qimage_to_numpy_view,
    _resolve_target_layer,
    _supports_layer_expansion,
)
from ..layer import Layer
from ..raster_edit_geometry import uses_placed_pixels
from .flood_fill import flood_fill_spans
from .placed_fill import apply_placed_fill, prepare_placed_spans


@dataclass(slots=True)
class _SpanBackup:
    y: int
    x0: int
    x1: int
    data: NDArray[Any]


@dataclass(slots=True)
class _SelectionClip:
    mask: NDArray[Any]
    bounds: tuple[int, int, int, int]


class _FloodFillBase(Command):
    def __init__(
        self,
        reference_layer_id: str | None,
        seed_x: int,
        seed_y: int,
        fill_rgba: tuple[int, int, int, int] | tuple[float, float, float, float],
        color_tolerance: int,
        gap_close_px: int,
        *,
        connectivity: int = 8,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self.reference_layer_id = reference_layer_id
        self.seed_x = int(seed_x)
        self.seed_y = int(seed_y)
        self.fill_rgba = _normalize_rgba(fill_rgba)
        self.color_tolerance = max(0, min(int(color_tolerance), 255))
        self.gap_close_px = max(0, int(gap_close_px))
        self.connectivity = int(connectivity)
        self._spans: list[tuple[int, int, int]] | None = None
        self._bounds: tuple[int, int, int, int] | None = None
        self._backup_spans: list[_SpanBackup] = []
        self._target_layer_id: str | None = None
        self._selection_clip: _SelectionClip | None = None

    @property
    def target_layer_id(self) -> str | None:
        return self._target_layer_id

    @property
    def retained_bytes(self) -> int | None:
        placed = getattr(self, "_placed_edit", None)
        if placed is None:
            return None
        selection_bytes = (
            self._selection_clip.mask.nbytes if self._selection_clip is not None else 0
        )
        return placed.retained_bytes + selection_bytes

    def _ensure_spans(self, canvas: Any) -> bool:  # noqa: PLR0911
        if self._spans is not None:
            return bool(self._spans)

        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False
        if self.seed_x < 0 or self.seed_y < 0 or self.seed_x >= width or self.seed_y >= height:
            return False
        self._selection_clip = _selection_clip_from_canvas(canvas, width, height)
        if self._selection_clip is not None and not _selection_allows_seed(
            self._selection_clip, self.seed_x, self.seed_y
        ):
            return False

        layer = _resolve_target_layer(canvas, self.reference_layer_id)
        if layer is None:
            return False

        if uses_placed_pixels(canvas, layer):
            return prepare_placed_spans(self, canvas, layer)
        ref_array = _reference_array(layer)
        if ref_array is None:
            return False

        offset_x, offset_y = _layer_offset(layer)
        local_x = self.seed_x - offset_x
        local_y = self.seed_y - offset_y
        if (
            local_x < 0
            or local_y < 0
            or local_y >= ref_array.shape[0]
            or local_x >= ref_array.shape[1]
        ):
            return False

        result = flood_fill_spans(
            ref_array,
            int(local_x),
            int(local_y),
            tolerance=self.color_tolerance,
            gap_close_px=self.gap_close_px,
            connectivity=self.connectivity,
        )
        if not result.spans or result.bounds is None:
            return False

        spans_canvas = [(y + offset_y, x0 + offset_x, x1 + offset_x) for y, x0, x1 in result.spans]
        bounds = (
            result.bounds[0] + offset_x,
            result.bounds[1] + offset_y,
            result.bounds[2],
            result.bounds[3],
        )
        clipped_bounds = _clip_bounds_to_canvas(canvas, bounds)
        if clipped_bounds is None:
            return False
        if self._selection_clip is not None:
            clipped_bounds = _intersect_bounds(clipped_bounds, self._selection_clip.bounds)
            if clipped_bounds is None:
                return False

        self._spans = spans_canvas
        self._bounds = clipped_bounds
        return True

    def _apply_to_layer(self, canvas: Any, layer: Any, *, record_backup: bool) -> bool:
        if self._spans is None or self._bounds is None:
            return False
        if not _layer_allows_edit(layer):
            return False

        if uses_placed_pixels(canvas, layer):
            return apply_placed_fill(self, canvas, layer)
        if _supports_layer_expansion(canvas, layer):
            _ensure_layer_bounds(canvas, layer, self._bounds)

        offset_x, offset_y = _layer_offset(layer)
        target = _target_array(layer)
        if target is None:
            return False

        self._target_layer_id = getattr(layer, "id", None)
        self._backup_spans = [] if record_backup else self._backup_spans

        r, g, b, a = self.fill_rgba
        src_alpha = float(a) / 255.0
        if src_alpha <= 0.0:
            return False

        if _is_qimage_layer(layer):
            src_rgb = np.array([b, g, r], dtype=np.float32)
            src_premult = src_rgb * src_alpha
            self._apply_spans_premultiplied(
                target,
                offset=(offset_x, offset_y),
                src_premult=src_premult,
                src_alpha=src_alpha,
                record_backup=record_backup,
            )
        else:
            src_rgb = np.array([r, g, b], dtype=np.float32) / 255.0
            self._apply_spans_nonpremultiplied(
                target,
                offset=(offset_x, offset_y),
                src_rgb=src_rgb,
                src_alpha=src_alpha,
                record_backup=record_backup,
            )

        self._dirty_region = DirtyRegion(*self._bounds)
        if hasattr(layer, "mark_dirty"):
            x, y, w, h = self._bounds
            with contextlib.suppress(Exception):
                layer.mark_dirty(x, y, w, h)
        return True

    def _apply_spans_premultiplied(
        self,
        target: NDArray[Any],
        *,
        offset: tuple[int, int],
        src_premult: NDArray[Any],
        src_alpha: float,
        record_backup: bool,
    ) -> None:
        height, width = target.shape[:2]
        offset_x, offset_y = offset
        for y, x0, x1 in self._spans or []:
            ty = y - offset_y
            if ty < 0 or ty >= height:
                continue
            tx0 = x0 - offset_x
            tx1 = x1 - offset_x
            if tx1 < 0 or tx0 >= width:
                continue
            tx0 = max(0, tx0)
            tx1 = min(width - 1, tx1)
            if tx1 < tx0:
                continue
            segment = target[ty, tx0 : tx1 + 1]
            mask_alpha = _selection_alpha_for_segment(
                self._selection_clip,
                y,
                tx0 + offset_x,
                tx1 + offset_x,
            )
            if mask_alpha is not None and (mask_alpha.size == 0 or not np.any(mask_alpha > 0.0)):
                continue
            if record_backup:
                self._backup_spans.append(_SpanBackup(ty, tx0, tx1, segment.copy()))
            dest_rgb = segment[:, :3].astype(np.float32)
            dest_alpha = segment[:, 3].astype(np.float32) / 255.0
            effective_alpha: NDArray[Any]
            effective_premult: NDArray[Any]
            if mask_alpha is None:
                effective_alpha = np.full(segment.shape[0], src_alpha, dtype=np.float32)
                effective_premult = src_premult[np.newaxis, :]
            else:
                effective_alpha = src_alpha * mask_alpha
                effective_premult = src_premult[np.newaxis, :] * mask_alpha[:, np.newaxis]
            out_rgb = effective_premult + dest_rgb * (1.0 - effective_alpha[:, np.newaxis])
            out_alpha = effective_alpha + dest_alpha * (1.0 - effective_alpha)
            segment[:, :3] = np.clip(out_rgb, 0.0, 255.0).astype(np.uint8, copy=False)
            segment[:, 3] = np.clip(out_alpha * 255.0, 0.0, 255.0).astype(np.uint8, copy=False)

    def _apply_spans_nonpremultiplied(
        self,
        target: NDArray[Any],
        *,
        offset: tuple[int, int],
        src_rgb: NDArray[Any],
        src_alpha: float,
        record_backup: bool,
    ) -> None:
        height, width = target.shape[:2]
        offset_x, offset_y = offset
        for y, x0, x1 in self._spans or []:
            ty = y - offset_y
            if ty < 0 or ty >= height:
                continue
            tx0 = x0 - offset_x
            tx1 = x1 - offset_x
            if tx1 < 0 or tx0 >= width:
                continue
            tx0 = max(0, tx0)
            tx1 = min(width - 1, tx1)
            if tx1 < tx0:
                continue
            segment = target[ty, tx0 : tx1 + 1]
            mask_alpha = _selection_alpha_for_segment(
                self._selection_clip,
                y,
                tx0 + offset_x,
                tx1 + offset_x,
            )
            if mask_alpha is not None and (mask_alpha.size == 0 or not np.any(mask_alpha > 0.0)):
                continue
            if record_backup:
                self._backup_spans.append(_SpanBackup(ty, tx0, tx1, segment.copy()))
            dest_rgb = segment[:, :3].astype(np.float32) / 255.0
            dest_alpha = segment[:, 3].astype(np.float32) / 255.0
            effective_alpha = (
                np.full(segment.shape[0], src_alpha, dtype=np.float32)
                if mask_alpha is None
                else src_alpha * mask_alpha
            )
            out_alpha = effective_alpha + dest_alpha * (1.0 - effective_alpha)
            out_rgb = np.where(
                out_alpha[:, None] > 0.0,
                (
                    src_rgb * effective_alpha[:, None]
                    + dest_rgb * dest_alpha[:, None] * (1.0 - effective_alpha[:, None])
                )
                / np.clip(out_alpha[:, None], 1e-6, 1.0),
                dest_rgb,
            )
            segment[:, :3] = np.clip(out_rgb * 255.0, 0.0, 255.0)
            segment[:, 3] = np.clip(out_alpha * 255.0, 0.0, 255.0)

    def _restore_to_layer(self, layer: Any) -> bool:
        placed = getattr(self, "_placed_edit", None)
        if placed is not None:
            return placed.write_pixels(layer, before=True)
        if not self._backup_spans:
            return False
        target = _target_array(layer)
        if target is None:
            return False
        height, width = target.shape[:2]
        for backup in self._backup_spans:
            if backup.y < 0 or backup.y >= height:
                continue
            if backup.x1 < 0 or backup.x0 >= width:
                continue
            x0 = max(0, backup.x0)
            x1 = min(width - 1, backup.x1)
            segment = target[backup.y, x0 : x1 + 1]
            segment[: backup.data.shape[0]] = backup.data[: segment.shape[0]]
        return True


class FloodFillCommand(_FloodFillBase):
    def apply(self, canvas: Any) -> bool:
        if not self._ensure_spans(canvas):
            return False
        if self.fill_rgba[3] <= 0:
            return False
        layer = _resolve_target_layer(canvas, self.reference_layer_id)
        if layer is None:
            return False
        self._backup_spans = []
        if not self._apply_to_layer(canvas, layer, record_backup=True):
            return False
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layer = _resolve_target_layer(canvas, self._target_layer_id)
        if layer is None:
            return False
        if not self._restore_to_layer(layer):
            return False
        self._executed = False
        return True


class FloodFillToNewLayerCommand(_FloodFillBase):
    def __init__(
        self,
        reference_layer_id: str | None,
        seed_x: int,
        seed_y: int,
        fill_rgba: tuple[int, int, int, int] | tuple[float, float, float, float],
        color_tolerance: int,
        gap_close_px: int,
        *,
        connectivity: int = 8,
        merge_key: str | None = None,
        layer_name: str | None = None,
    ) -> None:
        super().__init__(
            reference_layer_id,
            seed_x,
            seed_y,
            fill_rgba,
            color_tolerance,
            gap_close_px,
            connectivity=connectivity,
            merge_key=merge_key,
        )
        self._created_layer_id: str | None = None
        self._created_layer_index: int | None = None
        self._active_layer_id: str | None = None
        self._layer_name: str | None = layer_name

    def apply(self, canvas: Any) -> bool:
        if not self._ensure_spans(canvas):
            return False
        if self.fill_rgba[3] <= 0:
            return False
        layer, layer_id, insert_index, active_id = _create_fill_layer(
            canvas,
            name=self._layer_name or "Fill",
            insert_index=self._created_layer_index,
        )
        if layer is None or layer_id is None:
            return False
        self._created_layer_id = layer_id
        self._created_layer_index = insert_index
        self._active_layer_id = active_id
        if not self._apply_to_layer(canvas, layer, record_backup=False):
            return False
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed or not self._created_layer_id:
            return False
        removed = _remove_layer_by_id(canvas, self._created_layer_id)
        if not removed:
            return False
        if self._active_layer_id:
            _restore_active_layer(canvas, self._active_layer_id)
        self._executed = False
        return True

    @property
    def target_layer_id(self) -> str | None:
        return self._created_layer_id


def _normalize_rgba(
    value: tuple[int, int, int, int] | tuple[float, float, float, float],
) -> tuple[int, int, int, int]:
    if len(value) != 4:
        raise ValueError("fill_rgba must be a 4-tuple")
    if max(value) <= 1.0:
        r, g, b, a = (round(float(c) * 255) for c in value)
    else:
        r, g, b, a = (round(float(c)) for c in value)
    return (
        max(0, min(255, r)),
        max(0, min(255, g)),
        max(0, min(255, b)),
        max(0, min(255, a)),
    )


def _reference_array(layer: Any) -> NDArray[Any] | None:
    if _is_qimage_layer(layer):
        image = getattr(layer, "image", None)
        if image is None or image.isNull():
            return None
        try:
            from PySide6.QtGui import QImage  # noqa: PLC0415
        except Exception:
            return None
        if image.format() == QImage.Format.Format_ARGB32_Premultiplied:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)
        return _qimage_to_numpy_copy(image)
    buffer = getattr(layer, "buffer", None)
    if isinstance(buffer, np.ndarray):
        return np.clip(buffer, 0.0, 255.0).astype(np.uint8)
    return None


def _target_array(layer: Any) -> NDArray[Any] | None:
    if _is_qimage_layer(layer):
        image = _ensure_qimage_premultiplied(layer)
        if image is None:
            return None
        return _qimage_to_numpy_view(image)
    buffer = getattr(layer, "buffer", None)
    if isinstance(buffer, np.ndarray):
        return buffer
    return None


def _selection_clip_from_canvas(canvas: Any, width: int, height: int) -> _SelectionClip | None:
    mask, _bounds = UpdateSelectionMaskCommand._read_selection(canvas)
    if mask is None:
        return None
    mask_np = UpdateSelectionMaskCommand._mask_to_numpy(mask, width, height)
    if mask_np is None:
        return None
    mask_np = _fit_selection_mask(mask_np, width, height)
    if mask_np.size == 0 or not np.any(mask_np > 0):
        return None
    bounds = UpdateSelectionMaskCommand._mask_bounds(mask_np)
    if bounds is None:
        return None
    return _SelectionClip(mask=np.ascontiguousarray(mask_np, dtype=np.uint8), bounds=bounds)


def _fit_selection_mask(mask: NDArray[Any], width: int, height: int) -> NDArray[Any]:
    mask = np.asarray(mask, dtype=np.uint8)
    if mask.ndim > 2:
        mask = mask[:, :, 0]
    if mask.shape[:2] == (height, width):
        return mask
    fitted = np.zeros((height, width), dtype=np.uint8)
    copy_h = min(height, int(mask.shape[0]))
    copy_w = min(width, int(mask.shape[1]))
    if copy_h > 0 and copy_w > 0:
        fitted[:copy_h, :copy_w] = mask[:copy_h, :copy_w]
    return fitted


def _selection_allows_seed(clip: _SelectionClip, seed_x: int, seed_y: int) -> bool:
    if seed_y < 0 or seed_y >= clip.mask.shape[0]:
        return False
    if seed_x < 0 or seed_x >= clip.mask.shape[1]:
        return False
    return int(clip.mask[seed_y, seed_x]) > 0


def _intersect_bounds(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0 = max(ax, bx)
    y0 = max(ay, by)
    x1 = min(ax + aw, bx + bw)
    y1 = min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _selection_alpha_for_segment(
    clip: _SelectionClip | None,
    canvas_y: int,
    canvas_x0: int,
    canvas_x1: int,
) -> NDArray[Any] | None:
    if clip is None:
        return None
    if canvas_y < clip.bounds[1] or canvas_y >= clip.bounds[1] + clip.bounds[3]:
        return np.zeros(max(0, canvas_x1 - canvas_x0 + 1), dtype=np.float32)
    if canvas_x1 < clip.bounds[0] or canvas_x0 >= clip.bounds[0] + clip.bounds[2]:
        return np.zeros(max(0, canvas_x1 - canvas_x0 + 1), dtype=np.float32)
    if canvas_y < 0 or canvas_y >= clip.mask.shape[0]:
        return np.zeros(max(0, canvas_x1 - canvas_x0 + 1), dtype=np.float32)
    x0 = max(0, canvas_x0)
    x1 = min(clip.mask.shape[1] - 1, canvas_x1)
    length = max(0, canvas_x1 - canvas_x0 + 1)
    if length <= 0 or x1 < x0:
        return np.zeros(length, dtype=np.float32)
    alpha = np.zeros(length, dtype=np.float32)
    dest_x0 = x0 - canvas_x0
    dest_x1 = dest_x0 + (x1 - x0 + 1)
    alpha[dest_x0:dest_x1] = clip.mask[canvas_y, x0 : x1 + 1].astype(np.float32) / 255.0
    return alpha


def _create_fill_layer(
    canvas: Any,
    *,
    name: str,
    insert_index: int | None,
) -> tuple[Any | None, str | None, int | None, str | None]:
    layer = None
    layer_id = None
    active_id = _active_layer_id(canvas)
    used_index: int | None = None

    if hasattr(canvas, "_layers") and hasattr(canvas, "add_layer"):
        active_index = getattr(canvas, "_active_index", 0)
        layer_count = len(getattr(canvas, "_layers", []))
        target_index = _compute_insert_index(active_index, insert_index)
        target_index = min(max(0, target_index), layer_count)
        created = canvas.add_layer(name)
        layer_id = getattr(created, "id", None) or getattr(created, "layer_id", None)
        layer = _find_layer_by_id(canvas, layer_id)
        if layer is None:
            return None, None, None, active_id
        new_index = _index_for_layer(canvas, layer_id)
        if new_index is not None and new_index != target_index:
            move = getattr(canvas, "move_layer", None)
            if callable(move):
                move(new_index, target_index)
        if active_id:
            _restore_active_layer(canvas, active_id)
        used_index = target_index
        return layer, layer_id, used_index, active_id

    if hasattr(canvas, "layers"):
        layers = getattr(canvas, "layers", None)
        if not isinstance(layers, list):
            return None, None, None, active_id
        width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
        layer = Layer(name=name, width=width, height=height)
        layer_id = layer.id
        active_index = getattr(canvas, "_active_index", 0)
        target_index = _compute_insert_index(active_index, insert_index)
        target_index = min(max(0, target_index), len(layers))
        layers.insert(target_index, layer)
        if target_index <= active_index:
            with contextlib.suppress(Exception):
                canvas._active_index = active_index + 1
        used_index = target_index
        return layer, layer_id, used_index, active_id

    return None, None, None, active_id


def _compute_insert_index(active_index: int, requested: int | None) -> int:
    if requested is not None:
        return max(0, int(requested))
    if active_index > 0:
        return int(active_index)
    return 1


def _active_layer_id(canvas: Any) -> str | None:
    current = getattr(canvas, "current_layer", None)
    if callable(current):
        try:
            layer_ref = current()
            return getattr(layer_ref, "id", None)
        except Exception:
            return None
    # Prefer the id-resolving accessor: for a nested active layer
    # ``_active_index`` addresses the ancestor group, not the layer itself.
    resolve = getattr(canvas, "_get_active_layer", None)
    try:
        if callable(resolve):
            return cast("str | None", getattr(resolve(), "id", None))
        if hasattr(canvas, "_layers") and hasattr(canvas, "_active_index"):
            return cast("str | None", canvas._layers[canvas._active_index].id)
    except Exception:
        return None
    return None


def _restore_active_layer(canvas: Any, layer_id: str) -> None:
    setter = getattr(canvas, "set_active_layer", None)
    if callable(setter):
        try:
            setter(layer_id)
        except Exception:
            return


def _remove_layer_by_id(canvas: Any, layer_id: str) -> bool:
    remover = getattr(canvas, "remove_layer", None)
    if callable(remover):
        try:
            return bool(remover(layer_id))
        except Exception:
            return False
    layers = getattr(canvas, "layers", None)
    if isinstance(layers, list):
        for idx, layer in enumerate(list(layers)):
            if getattr(layer, "id", None) == layer_id:
                layers.pop(idx)
                return True
    if hasattr(canvas, "_layers"):
        layers = getattr(canvas, "_layers", None)
        if isinstance(layers, list):
            for idx, layer in enumerate(list(layers)):
                if getattr(layer, "id", None) == layer_id:
                    layers.pop(idx)
                    return True
    return False


def _find_layer_by_id(canvas: Any, layer_id: str | None) -> Any | None:
    if layer_id is None:
        return None
    getter = getattr(canvas, "_get_layer_by_id", None)
    if callable(getter):
        try:
            return getter(layer_id)
        except Exception:
            return None
    getter = getattr(canvas, "get_layer_by_id", None)
    if callable(getter):
        try:
            return getter(layer_id)
        except Exception:
            return None
    return None


def _index_for_layer(canvas: Any, layer_id: str | None) -> int | None:
    if layer_id is None:
        return None
    layers = getattr(canvas, "_layers", None)
    if isinstance(layers, list):
        for idx, layer in enumerate(layers):
            if getattr(layer, "id", None) == layer_id:
                return idx
    return None


class PatternFillCommand(_FloodFillBase):
    """Flood fill with a tiled pattern image instead of a solid colour."""

    def __init__(
        self,
        reference_layer_id: str | None,
        seed_x: int,
        seed_y: int,
        pattern: NDArray[Any],
        color_tolerance: int,
        gap_close_px: int,
        *,
        pattern_scale: float = 1.0,
        pattern_offset: tuple[int, int] = (0, 0),
        connectivity: int = 8,
        merge_key: str | None = None,
    ) -> None:
        # Pass a dummy fill_rgba — we override the fill logic entirely.
        super().__init__(
            reference_layer_id,
            seed_x,
            seed_y,
            (255, 255, 255, 255),
            color_tolerance,
            gap_close_px,
            connectivity=connectivity,
            merge_key=merge_key,
        )
        self._pattern = _ensure_pattern_rgba(pattern)
        self._pattern_scale = max(0.1, float(pattern_scale))
        self._pattern_offset = (int(pattern_offset[0]), int(pattern_offset[1]))

    def apply(self, canvas: Any) -> bool:
        if not self._ensure_spans(canvas):
            return False
        layer = _resolve_target_layer(canvas, self.reference_layer_id)
        if layer is None:
            return False
        self._backup_spans = []
        if not self._apply_pattern_to_layer(canvas, layer, record_backup=True):
            return False
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        layer = _resolve_target_layer(canvas, self._target_layer_id)
        if layer is None:
            return False
        if not self._restore_to_layer(layer):
            return False
        self._executed = False
        return True

    def _apply_pattern_to_layer(  # noqa: PLR0912, PLR0915
        self, canvas: Any, layer: Any, *, record_backup: bool
    ) -> bool:
        if self._spans is None or self._bounds is None:
            return False
        if not _layer_allows_edit(layer):
            return False

        if uses_placed_pixels(canvas, layer):
            return apply_placed_fill(self, canvas, layer, pattern=True)
        if _supports_layer_expansion(canvas, layer):
            _ensure_layer_bounds(canvas, layer, self._bounds)

        offset_x, offset_y = _layer_offset(layer)
        target = _target_array(layer)
        if target is None:
            return False

        self._target_layer_id = getattr(layer, "id", None)
        if record_backup:
            self._backup_spans = []

        pat = self._scaled_pattern()
        ph, pw = pat.shape[:2]
        ox, oy = self._pattern_offset
        is_premult = _is_qimage_layer(layer)

        height, width = target.shape[:2]
        for y, x0, x1 in self._spans:
            ty = y - offset_y
            if ty < 0 or ty >= height:
                continue
            tx0, tx1 = max(0, x0 - offset_x), min(width - 1, x1 - offset_x)
            if tx1 < 0 or tx0 >= width or tx1 < tx0:
                continue

            segment = target[ty, tx0 : tx1 + 1]
            mask_alpha = _selection_alpha_for_segment(
                self._selection_clip,
                y,
                tx0 + offset_x,
                tx1 + offset_x,
            )
            if mask_alpha is not None and (mask_alpha.size == 0 or not np.any(mask_alpha > 0.0)):
                continue
            if record_backup:
                self._backup_spans.append(_SpanBackup(ty, tx0, tx1, segment.copy()))

            pat_row_y = (y + oy) % ph
            pat_xs = (np.arange(tx0, tx1 + 1) + ox + offset_x) % pw
            pat_pixels = pat[pat_row_y, pat_xs]
            _blend_pattern_span(segment, pat_pixels, is_premult, mask_alpha=mask_alpha)

        self._dirty_region = DirtyRegion(*self._bounds)
        if hasattr(layer, "mark_dirty"):
            x, y, w, h = self._bounds
            with contextlib.suppress(Exception):
                layer.mark_dirty(x, y, w, h)
        return True

    def _scaled_pattern(self) -> NDArray[Any]:
        if self._pattern_scale == 1.0:
            return self._pattern
        import cv2  # noqa: PLC0415

        h, w = self._pattern.shape[:2]
        new_w = max(1, int(w * self._pattern_scale))
        new_h = max(1, int(h * self._pattern_scale))
        return cv2.resize(self._pattern, (new_w, new_h), interpolation=cv2.INTER_LINEAR)


def _blend_pattern_span(
    segment: NDArray[Any],
    pat_pixels: NDArray[Any],
    is_premult: bool,
    *,
    mask_alpha: NDArray[Any] | None = None,
) -> None:
    """Alpha-blend pattern pixels onto a target segment in-place."""
    src_alpha_f = pat_pixels[:, 3].astype(np.float32) / 255.0
    if mask_alpha is not None:
        src_alpha_f = src_alpha_f * mask_alpha
    dest_alpha = segment[:, 3].astype(np.float32) / 255.0
    out_alpha = src_alpha_f + dest_alpha * (1.0 - src_alpha_f)

    if is_premult:
        src_bgr = pat_pixels[:, [2, 1, 0]].astype(np.float32) * src_alpha_f[:, None]
        out_rgb = src_bgr + segment[:, :3].astype(np.float32) * (1.0 - src_alpha_f[:, None])
        segment[:, :3] = np.clip(out_rgb, 0.0, 255.0).astype(np.uint8, copy=False)
    else:
        src_rgb = pat_pixels[:, :3].astype(np.float32) / 255.0
        dest_rgb = segment[:, :3].astype(np.float32) / 255.0
        out_rgb = np.where(
            out_alpha[:, None] > 0.0,
            (
                src_rgb * src_alpha_f[:, None]
                + dest_rgb * dest_alpha[:, None] * (1.0 - src_alpha_f[:, None])
            )
            / np.clip(out_alpha[:, None], 1e-6, 1.0),
            dest_rgb,
        )
        segment[:, :3] = np.clip(out_rgb * 255.0, 0.0, 255.0)
    segment[:, 3] = np.clip(out_alpha * 255.0, 0.0, 255.0).astype(np.uint8, copy=False)


def _ensure_pattern_rgba(pattern: NDArray[Any]) -> NDArray[Any]:
    """Ensure pattern is uint8 RGBA (H, W, 4)."""
    if pattern.ndim == 2:
        # Grayscale → RGBA
        gray = pattern.astype(np.uint8) if pattern.dtype != np.uint8 else pattern
        rgba = np.zeros((*gray.shape, 4), dtype=np.uint8)
        rgba[:, :, :3] = gray[:, :, None]
        rgba[:, :, 3] = 255
        return rgba
    if pattern.ndim == 3:
        if pattern.shape[2] == 3:
            alpha = np.full((*pattern.shape[:2], 1), 255, dtype=np.uint8)
            return np.concatenate([pattern.astype(np.uint8), alpha], axis=2)
        if pattern.shape[2] == 4:
            return pattern.astype(np.uint8) if pattern.dtype != np.uint8 else pattern
    raise ValueError(f"Unsupported pattern shape: {pattern.shape}")


__all__ = ["FloodFillCommand", "FloodFillToNewLayerCommand", "PatternFillCommand"]
