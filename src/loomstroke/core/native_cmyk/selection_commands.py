"""Native implementations behind the existing selection commands and history."""

from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.command_image_utils import _affine_bounds
from loomstroke.core.command_layer_access import _resolve_target_layer
from loomstroke.core.command_types import Command, CompositeCommand
from loomstroke.core.geometry import Affine2D

from .affine import placement, sample
from .patch_command import NativeCmykPatchCommand
from .pixel_operations import NativeCmykPixelOperations
from .pixels import NativeCmykPixels
from .selection_layer_command import NativeSelectionLayerCommand
from .selection_mask_command import NativeSelectionMaskCommand
from .selection_pixels import (
    capture,
    compose,
    mask_pixels,
    over,
    payload_pixels,
)
from .selection_source import expanded_bounds


def _build(command: Any, canvas: Any, layer: Any) -> CompositeCommand | None:
    from loomstroke.core.commands_selection_content import (  # noqa: PLC0415
        CutSelectionCommand,
        PasteSelectionCommand,
    )

    cut = isinstance(command, CutSelectionCommand)
    if cut:
        command.payload = capture(canvas, layer)
        mask, bounds = canvas._selection_clip_state()
        full_mask = getattr(canvas, "_selection_mask_full", None)
        command._native_selection_before = (
            mask.copy() if mask is not None else None,
            bounds.getRect() if bounds is not None else None,
            getattr(canvas, "_selection_bounds_full", None),
            full_mask.copy() if full_mask is not None else None,
        )
    payload = command.payload
    if payload is None:
        return None
    if isinstance(command, PasteSelectionCommand):
        transform = Affine2D.translation(*(command.position or payload.bounds[:2]))
        duplicate = True
    else:
        transform = Affine2D.translation(*payload.bounds[:2]) if cut else command.transform
        duplicate = False if cut else command.duplicate
    inverse = placement(layer).inverse()
    if inverse is None or transform.inverse() is None:
        return None
    binary = getattr(command, "mask_mode", "soft") == "binary"
    local_transform = compose(inverse, transform)
    native = layer.native_cmyk
    height, width = native.inks.shape[:2]
    bounds = _affine_bounds(local_transform, payload.bounds[2], payload.bounds[3])
    if bounds is None:
        return None
    rect = QRect(*bounds)
    source_transform = compose(inverse, Affine2D.translation(*payload.bounds[:2]))
    original_bounds = _affine_bounds(source_transform, payload.bounds[2], payload.bounds[3])
    if not duplicate and original_bounds is not None:
        rect = rect.united(QRect(*original_bounds))
    backing = QRect(0, 0, width, height)
    rect = rect.intersected(backing) if cut else rect
    if rect.isEmpty():
        return None
    expanded = expanded_bounds(native, rect)
    before = sample(native, rect, Affine2D.identity())
    after = before
    selected = canvas.channels().selected
    if not duplicate:
        mask, _ = mask_pixels(payload, binary=binary)
        mask_source = NativeCmykPixels(np.zeros((*mask.shape, 4), np.uint8), mask, native.profile)
        coverage = sample(mask_source, rect, source_transform).alpha
        after = NativeCmykPixelOperations.paint(
            after, (0, 0, 0, 0), coverage, selected, erase=True, alpha_lock=layer.alpha_lock
        )
    if not cut:
        source = payload_pixels(payload, native, binary=binary)
        source = sample(source, rect, local_transform)
        after = over(after, source, selected, alpha_lock=layer.alpha_lock)
    bounds_tuple = rect.x(), rect.y(), rect.width(), rect.height()
    pixel_edit = (
        NativeCmykPatchCommand(layer.id, bounds_tuple, before, after)
        if expanded == backing
        else NativeSelectionLayerCommand(layer, expanded, rect, after)
    )
    edits: list[Command] = [pixel_edit]
    if not cut:
        edits.append(NativeSelectionMaskCommand(canvas, payload, transform, binary=binary))
    return CompositeCommand(edits, "CMYK Selection")


def apply(command: Any, canvas: Any) -> bool | None:
    layer = _resolve_target_layer(canvas, getattr(command, "layer_id", None))
    if (
        layer is None
        or getattr(layer, "native_cmyk", None) is None
        or getattr(canvas, "_mask_edit_flag", False)
    ):
        return None
    if layer.locked or canvas.channels().is_alpha(layer):
        return False
    edit = getattr(command, "_native_edit", None)
    if edit is None:
        edit = _build(command, canvas, layer)
        if edit is None:
            return False
        command._native_edit = edit
        command.retained_bytes = sum(getattr(part, "retained_bytes", 0) for part in edit.commands)
    result = bool(edit.apply(canvas))
    command._dirty_region = edit.dirty_region
    command._executed = result
    return result


def revert(command: Any, canvas: Any) -> bool | None:
    edit = getattr(command, "_native_edit", None)
    if edit is None:
        return None
    result = bool(edit.revert(canvas))
    state = getattr(command, "_native_selection_before", None)
    if result and state is not None:
        from loomstroke.core.commands_selection_mask import (  # noqa: PLC0415
            UpdateSelectionMaskCommand,
            _set_selection_full_bounds,
            _set_selection_full_mask,
        )

        mask, bounds, full_bounds, full_mask = state
        UpdateSelectionMaskCommand._set_selection(canvas, mask, bounds)
        _set_selection_full_bounds(canvas, full_bounds)
        _set_selection_full_mask(canvas, full_mask)
    command._dirty_region = edit.dirty_region
    command._executed = not result
    return result
