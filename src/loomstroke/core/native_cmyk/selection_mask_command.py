"""Keep the unclipped selection mask alongside the visible canvas mask."""

from typing import Any

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.command_image_utils import _affine_bounds
from loomstroke.core.command_types import Command
from loomstroke.core.commands_selection_mask import (
    UpdateSelectionMaskCommand,
    _clone_mask,
    _set_selection_full_bounds,
    _set_selection_full_mask,
)
from loomstroke.core.geometry import Affine2D
from loomstroke.core.selection_clipboard import SelectionPayload

from .selection_pixels import compose, transformed_mask


class NativeSelectionMaskCommand(Command):
    def __init__(
        self, canvas: Any, payload: SelectionPayload, transform: Affine2D, *, binary: bool
    ) -> None:
        super().__init__()
        self.before = (
            *UpdateSelectionMaskCommand._read_selection(canvas),
            getattr(canvas, "_selection_bounds_full", None),
            _clone_mask(getattr(canvas, "_selection_mask_full", None)),
        )
        bounds = _affine_bounds(transform, payload.bounds[2], payload.bounds[3])
        if bounds is None or bounds[2] * bounds[3] > 128 * 1024**2:
            raise ValueError("Native selection mask exceeds the memory limit")
        x, y, w, h = bounds
        local = compose(Affine2D.translation(-x, -y), transform)
        full = transformed_mask(payload, local, w, h, binary=binary)
        visible = QImage(canvas._width, canvas._height, QImage.Format.Format_Alpha8)
        visible.fill(0)
        from PySide6.QtGui import QPainter  # noqa: PLC0415

        painter = QPainter(visible)
        painter.drawImage(x, y, full)
        painter.end()
        values = UpdateSelectionMaskCommand._mask_to_numpy(visible, canvas._width, canvas._height)
        clipped = UpdateSelectionMaskCommand._mask_bounds(values)
        self.after = (visible if np.any(values) else None, clipped, bounds, full)
        self.retained_bytes = full.sizeInBytes() + visible.sizeInBytes()

    def apply(self, canvas: Any) -> bool:
        self._write(canvas, self.after)
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        self._write(canvas, self.before)
        self._executed = False
        return True

    @staticmethod
    def _write(canvas: Any, state: Any) -> None:
        mask, bounds, full_bounds, full_mask = state
        if isinstance(bounds, QRect):
            bounds = bounds.getRect()
        UpdateSelectionMaskCommand._set_selection(canvas, _clone_mask(mask), bounds)
        _set_selection_full_bounds(canvas, full_bounds)
        _set_selection_full_mask(canvas, _clone_mask(full_mask))
