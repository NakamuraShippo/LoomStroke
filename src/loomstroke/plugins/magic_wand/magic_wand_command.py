"""Magic Wand selection command for undo/redo support."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from ...core.commands import (
    Command,
    DirtyRegion,
    SelectionOp,
    _is_qimage_layer,
    _resolve_target_layer,
)
from ...core.fill.flood_fill import _scanline_fill
from ...core.mask_coverage import mask_coverage_alpha8
from ...core.raster_selection_reference import (
    image_rgba_copy,
    raster_selection_reference,
)


class MagicWandCommand(Command):
    """Select contiguous colour region using flood fill algorithm."""

    def __init__(
        self,
        layer_id: str | None,
        seed_x: int,
        seed_y: int,
        tolerance: int,
        operation: SelectionOp | str = SelectionOp.REPLACE,
        *,
        contiguous: bool = True,
        merge_key: str | None = None,
    ) -> None:
        super().__init__(merge_key=merge_key)
        self._layer_id = layer_id
        self._seed_x = seed_x
        self._seed_y = seed_y
        self._tolerance = tolerance
        self._contiguous = contiguous
        self._operation = (
            operation if isinstance(operation, SelectionOp) else SelectionOp(operation)
        )
        self._before_mask: QImage | None = None
        self._before_bounds: tuple[int, int, int, int] | None = None

    def apply(self, canvas: Any) -> bool:
        width, height = self._canvas_size(canvas)
        if width <= 0 or height <= 0:
            return False

        layer = _resolve_target_layer(canvas, self._layer_id)
        if layer is None:
            return False

        source = self._get_layer_rgba(layer)
        if source is None or not (0 <= self._seed_x < width and 0 <= self._seed_y < height):
            return False
        projected = raster_selection_reference(canvas, layer, source, width, height)
        if projected is None:
            return False
        reference, coverage = projected
        if not coverage[self._seed_y, self._seed_x]:
            return False

        # Save current selection for undo
        self._before_mask, self._before_bounds = self._read_selection(canvas)

        # Compare placed pixels and constrain connectivity to visible coverage.
        tolerance = max(0, min(int(self._tolerance), 255))
        matching = (self._global_color_select(reference, tolerance) > 0) & (coverage > 0)
        if self._contiguous:
            new_mask = np.zeros((height, width), dtype=np.uint8)
            for sy, sx0, sx1 in _scanline_fill(matching, self._seed_x, self._seed_y):
                new_mask[sy, sx0 : sx1 + 1] = coverage[sy, sx0 : sx1 + 1]
        else:
            new_mask = np.where(matching, coverage, np.uint8(0))

        # Compose with existing selection
        old_mask = self._selection_to_numpy(self._before_mask, width, height)
        composed = self._compose(old_mask, new_mask, self._operation)

        if not np.any(composed):
            # No selection
            self._set_selection(canvas, None, None)
        else:
            bounds = self._mask_bounds(composed)
            qmask = self._numpy_to_qimage(composed)
            self._set_selection(canvas, qmask, bounds)

        self._dirty_region = DirtyRegion(0, 0, width, height)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        width, height = self._canvas_size(canvas)
        self._set_selection(canvas, self._before_mask, self._before_bounds)
        self._dirty_region = DirtyRegion(0, 0, width, height)
        self._executed = False
        return True

    # --- helpers ---

    @staticmethod
    def _canvas_size(canvas: Any) -> tuple[int, int]:
        w = getattr(canvas, "_width", 0) or getattr(canvas, "width", 0)
        h = getattr(canvas, "_height", 0) or getattr(canvas, "height", 0)
        if callable(w):
            w = w()
        if callable(h):
            h = h()
        return int(w), int(h)

    @staticmethod
    def _get_layer_rgba(layer: Any) -> np.ndarray | None:
        native = getattr(layer, "native_cmyk", None)
        if native is not None:
            return native.snapshot().preview()
        if _is_qimage_layer(layer):
            img = getattr(layer, "image", None) or getattr(layer, "qimage", None)
            if img is None:
                return None
            if img.isNull():
                return None
            return image_rgba_copy(img)
        buf = getattr(layer, "buffer", None)
        if isinstance(buf, np.ndarray):
            return np.clip(buf, 0, 255).astype(np.uint8)
        return None

    @staticmethod
    def _read_selection(canvas: Any) -> tuple[QImage | None, tuple[int, int, int, int] | None]:
        getter = getattr(canvas, "get_selection_mask", None)
        mask = getter() if callable(getter) else None
        bg = getattr(canvas, "get_selection_bounds", None)
        bounds = bg() if callable(bg) else None
        if bounds is not None:
            bounds = (bounds.x(), bounds.y(), bounds.width(), bounds.height())
        return mask, bounds

    @staticmethod
    def _set_selection(
        canvas: Any,
        mask: QImage | None,
        bounds: tuple[int, int, int, int] | None,
    ) -> None:
        setter = getattr(canvas, "set_selection_mask", None)
        if callable(setter):
            setter(mask, bounds=bounds)

    @staticmethod
    def _selection_to_numpy(mask: QImage | None, width: int, height: int) -> np.ndarray:
        if mask is None:
            return np.zeros((height, width), dtype=np.uint8)
        gray = mask_coverage_alpha8(mask)
        bits = gray.constBits()
        if hasattr(bits, "setsize"):
            bits.setsize(gray.bytesPerLine() * gray.height())
        arr = np.frombuffer(bits, dtype=np.uint8).reshape(gray.height(), gray.bytesPerLine())
        return arr[:, :width].copy()

    @staticmethod
    def _compose(old: np.ndarray, new: np.ndarray, op: SelectionOp) -> np.ndarray:
        if op == SelectionOp.REPLACE:
            return new
        if op == SelectionOp.ADD:
            return np.maximum(old, new)
        if op == SelectionOp.SUBTRACT:
            return np.clip(old.astype(np.int16) - new.astype(np.int16), 0, 255).astype(np.uint8)
        if op == SelectionOp.INTERSECT:
            return np.minimum(old, new)
        return new

    @staticmethod
    def _mask_bounds(mask: np.ndarray) -> tuple[int, int, int, int] | None:
        ys, xs = np.where(mask > 0)
        if len(ys) == 0:
            return None
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)

    @staticmethod
    def _numpy_to_qimage(arr: np.ndarray) -> QImage:
        h, w = arr.shape[:2]
        img = QImage(w, h, QImage.Format.Format_Alpha8)
        for y in range(h):
            bits = img.scanLine(y)
            if hasattr(bits, "setsize"):
                bits.setsize(img.bytesPerLine())
            row = np.frombuffer(bits, dtype=np.uint8, count=img.bytesPerLine())
            row[:w] = arr[y, :w]
        return img

    def _global_color_select(self, reference: np.ndarray, tolerance: int) -> np.ndarray:
        """Select all pixels within tolerance of the seed color (non-contiguous)."""
        seed_color = reference[self._seed_y, self._seed_x].astype(np.int16)
        diff = np.abs(reference.astype(np.int16) - seed_color)
        max_diff = diff.max(axis=2)
        mask = np.where(max_diff <= tolerance, np.uint8(255), np.uint8(0))
        return mask
