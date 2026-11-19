"""Prepare rectangular drag masks from known geometry without scanning the page."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor, QImage, QPainter

from .command_types import DirtyRegion, SelectionSnapshot
from .commands_selection_mask import (
    _get_selection_full_bounds,
    _get_selection_full_mask,
    _set_selection_full_bounds,
)


def apply_rect_preview(
    command: Any,
    canvas: Any,
    width: int,
    height: int,
    old_mask: QImage | None,
    old_bounds: tuple[int, int, int, int] | None,
) -> bool:
    """Use detached Qt snapshots and keep ordinary selection commands for commits."""
    bounds = command._normalize_rect(command.rect, width, height)
    mask = None
    if bounds is not None:
        mask = QImage(width, height, QImage.Format.Format_Alpha8)
        if mask.isNull():
            return False
        mask.fill(0)
        painter = QPainter(mask)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(*bounds, QColor(255, 255, 255, 255))
        painter.end()
    if mask == old_mask:
        return False
    command._before = SelectionSnapshot(
        mask=QImage(old_mask) if old_mask is not None else None, bounds=old_bounds
    )
    command._before_full_bounds = _get_selection_full_bounds(canvas)
    command._before_full_mask = _get_selection_full_mask(canvas)
    command._set_selection(canvas, mask, bounds)
    _set_selection_full_bounds(
        canvas, command.full_bounds if command.full_bounds is not None else bounds
    )
    command._after = SelectionSnapshot(
        mask=QImage(mask) if mask is not None else None, bounds=bounds
    )
    command._dirty_region = DirtyRegion(*(bounds or (0, 0, width, height)))
    command._executed = True
    return True
