"""Sample patterns and commit prepared fill spans as native CMYK patches."""

from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from .affine import placement
from .color_transform import CmykColorTransform
from .patch_command import NativeCmykPatchCommand
from .pixel_operations import NativeCmykPixelOperations
from .tool_space import clip_placed_pattern_coverage, is_placed, local_paint, selection_local


def _sample_pattern(command, native, x, y, coverage):
    pattern = command._scaled_pattern()
    ox, oy = command._pattern_offset
    rgba = pattern[(y + oy) % pattern.shape[0], (x + ox) % pattern.shape[1]]
    inks, alpha = CmykColorTransform.from_rgb(rgba, native.profile)
    color = inks.astype(native.inks.dtype) * (257 if native.inks.dtype == np.uint16 else 1)
    coverage = ((coverage.astype(np.uint16) * alpha + 127) // 255).astype(np.uint8)
    return color, coverage


def pattern_source_paint(command, canvas, layer, bounds, coverage):
    """Sample the document-anchored tile at native source pixel centers."""
    y, x = np.mgrid[
        bounds.y() : bounds.y() + bounds.height(), bounds.x() : bounds.x() + bounds.width()
    ]
    placed = is_placed(layer)
    if placed:
        mapping = placement(layer)
        x, y = (
            mapping.a * (x + 0.5) + mapping.b * (y + 0.5) + mapping.tx,
            mapping.c * (x + 0.5) + mapping.d * (y + 0.5) + mapping.ty,
        )
        x, y = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    else:
        x, y = x + layer.offset_x, y + layer.offset_y
    color, coverage = _sample_pattern(command, layer.native_cmyk, x, y, coverage)
    coverage = selection_local(canvas, layer, bounds, coverage)
    if placed:
        coverage = clip_placed_pattern_coverage(canvas, layer, bounds, coverage)
        if coverage is None:
            return None
    return color, coverage


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
    pattern_fill = hasattr(command, "_scaled_pattern")
    if pattern_fill:
        y, x = np.mgrid[
            bounds.y() : bounds.y() + bounds.height(), bounds.x() : bounds.x() + bounds.width()
        ]
        color, coverage = _sample_pattern(command, native, x, y, coverage)
    else:
        color = getattr(command, "_native_fill_color", None)
        if color is None:
            color = canvas.native_cmyk().color(command.fill_rgba, layer)
            command._native_fill_color = color
    local, color, coverage = local_paint(layer, bounds, color, coverage)
    if local.isEmpty():
        return False
    if pattern_fill and is_placed(layer):
        clipped = clip_placed_pattern_coverage(canvas, layer, local, coverage)
        if clipped is None:
            return False
        coverage = clipped
    if not np.any(coverage):
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
