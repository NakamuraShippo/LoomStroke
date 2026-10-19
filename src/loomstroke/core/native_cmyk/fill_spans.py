"""Commit prepared bucket/pattern spans as native patches through existing commands."""

from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from .color_transform import CmykColorTransform
from .patch_command import NativeCmykPatchCommand
from .pixel_operations import NativeCmykPixelOperations
from .tool_space import local_paint


def apply_spans(command: Any, canvas: Any, layer: Any) -> bool:
    from loomstroke.core.fill.commands_fill import (  # noqa: PLC0415
        _selection_alpha_for_segment,
    )

    bounds = QRect(*command._bounds)
    if bounds.isEmpty() or bounds.width() * bounds.height() * 4 > 128 * 1024 * 1024:
        return False
    coverage = np.zeros((bounds.height(), bounds.width()), np.uint8)
    for y, x0, x1 in command._spans:
        alpha = _selection_alpha_for_segment(command._selection_clip, y, x0, x1)
        values = (
            command.fill_rgba[3]
            if alpha is None
            else np.rint(command.fill_rgba[3] * alpha).astype(np.uint8)
        )
        coverage[y - bounds.y(), x0 - bounds.x() : x1 - bounds.x() + 1] = values
    native = layer.native_cmyk
    color: Any
    if hasattr(command, "_scaled_pattern"):
        pattern = command._scaled_pattern()
        yy, xx = np.mgrid[
            bounds.y() : bounds.y() + bounds.height(), bounds.x() : bounds.x() + bounds.width()
        ]
        ox, oy = command._pattern_offset
        rgba = pattern[(yy + oy) % pattern.shape[0], (xx + ox) % pattern.shape[1]]
        inks, alpha = CmykColorTransform.from_rgb(rgba, native.profile)
        color = inks.astype(native.inks.dtype) * (257 if native.inks.dtype == np.uint16 else 1)
        coverage = ((coverage.astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
    else:
        color = getattr(command, "_native_fill_color", None)
        if color is None:
            color = canvas.native_cmyk().color(command.fill_rgba, layer)
            command._native_fill_color = color
    local, color, coverage = local_paint(layer, bounds, color, coverage)
    if local.isEmpty() or not np.any(coverage):
        return False
    rect = local.x(), local.y(), local.width(), local.height()
    before = native.region(rect)
    plates = getattr(command, "_native_fill_plates", None)
    if plates is None:
        plates = (
            (0, 1, 2, 3)
            if getattr(command, "_created_layer_id", None) == layer.id
            else canvas.channels().selected
        )
        command._native_fill_plates = plates
    after = NativeCmykPixelOperations.paint(
        before, color, coverage, plates, alpha_lock=layer.alpha_lock
    )
    patch = NativeCmykPatchCommand(layer.id, rect, before, after)
    if not patch.apply(canvas):
        return False
    command._native_fill_patch = patch
    command._target_layer_id = layer.id
    command._dirty_region = patch.dirty_region
    return True
