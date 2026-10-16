"""Mosaic (pixelate) live-filter filter."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from . import BaseFilter, FilterParamSchema
from .gaussian_blur import _bgra_to_qimage, _qimage_to_bgra

# Cells larger than this stop reading as "mosaic" and the GPU loop cost grows
# linearly, so both paths clamp here.  Keep in sync with the param schema.
MAX_CELL_SIZE = 256


def mosaic_cell_size(params: dict[str, Any]) -> int:
    """Return the clamped integer cell size for the mosaic kernel.

    Shared with ``WgpuComputeProcessor.mosaic`` so the canvas, merge and
    export agree on the block grid.  A cell of 1 (or less) is a no-op.
    """

    return max(1, min(MAX_CELL_SIZE, int(float(params.get("cell_size", 8.0)))))


def mosaic_block_average(premultiplied: np.ndarray, cell: int) -> np.ndarray:
    """Average *premultiplied* RGBA float32 pixels over a ``cell``-sized grid.

    The grid is anchored at the image origin and edge blocks average only the
    pixels that exist — the same boundary rule as the WGSL ``mosaic_axis``
    pass (``end = min(start + cell, dims)``).  Averaging premultiplied values
    is what keeps transparent texels from dragging black into a block.
    """

    height, width = premultiplied.shape[:2]
    col_starts = np.arange(0, width, cell)
    row_starts = np.arange(0, height, cell)
    col_counts = np.minimum(col_starts + cell, width) - col_starts
    row_counts = np.minimum(row_starts + cell, height) - row_starts

    sums = np.add.reduceat(premultiplied, col_starts, axis=1)
    sums = np.add.reduceat(sums, row_starts, axis=0)
    counts = np.outer(row_counts, col_counts).astype(np.float32)
    means = sums / counts[..., None]

    expanded = np.repeat(means, row_counts, axis=0)
    return np.repeat(expanded, col_counts, axis=1)


class MosaicFilter(BaseFilter):
    """Classic censorship-style pixelation on a fixed square grid."""

    def name(self) -> str:
        return "Mosaic"

    def category(self) -> str:
        return "stylize"

    def param_schema(self) -> list[FilterParamSchema]:
        return [
            FilterParamSchema(
                key="cell_size",
                label="Cell Size",
                param_type="int",
                default=8,
                min_val=2,
                max_val=MAX_CELL_SIZE,
                step=1,
            ),
        ]

    def apply(
        self,
        source: QImage,
        params: dict[str, Any],
        mask: QImage | None = None,
    ) -> QImage:
        cell = mosaic_cell_size(params)
        if cell <= 1:
            return source.copy()

        # ``_qimage_to_bgra`` yields premultiplied BGRA, which is exactly the
        # space the block average wants; channel order does not matter to it.
        arr = _qimage_to_bgra(source)
        averaged = mosaic_block_average(arr.astype(np.float32), cell)
        result_arr = np.clip(averaged + 0.5, 0, 255).astype(np.uint8)
        result = _bgra_to_qimage(result_arr)

        if mask is not None:
            result = self._apply_mask(source, result, mask)
        return result

    @staticmethod
    def _apply_mask(original: QImage, effected: QImage, mask: QImage) -> QImage:
        orig_arr = _qimage_to_bgra(original)
        eff_arr = _qimage_to_bgra(effected)
        w, h = original.width(), original.height()

        scaled_mask = mask.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        bc = scaled_mask.bytesPerLine() * scaled_mask.height()
        mbits = scaled_mask.bits()
        if hasattr(mbits, "setsize"):
            mbits.setsize(bc)
        mask_flat = np.frombuffer(mbits, dtype=np.uint8, count=bc).reshape(
            scaled_mask.height(), scaled_mask.bytesPerLine()
        )
        mask_arr = mask_flat[:, : scaled_mask.width()].reshape(h, w, 1).astype(np.float32) / 255.0

        result_arr = (
            orig_arr.astype(np.float32)
            + (eff_arr.astype(np.float32) - orig_arr.astype(np.float32)) * mask_arr
        )
        return _bgra_to_qimage(np.clip(result_arr, 0, 255).astype(np.uint8))


MosaicEffect = MosaicFilter  # Legacy class import compatibility.
BaseEffect = BaseFilter
EffectParamSchema = FilterParamSchema
