"""Immutable native selection preview state, shared with canvas snapshots."""

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect

from loomstroke.core.command_image_utils import _affine_bounds
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.geometry import Affine2D
from loomstroke.core.selection_clipboard import SelectionPayload

from .affine import placement
from .selection_mask_command import NativeSelectionMaskCommand
from .selection_pixels import compose, payload_pixels
from .selection_source import NativeSelectionSource, expanded_bounds, rebase


@dataclass(frozen=True)
class NativeCmykSelectionPreview:
    payload: object
    layer_id: str
    native: NativeSelectionSource
    transform: LayerTransform
    binary: bool


def update(
    canvas: Any, payload: SelectionPayload, transform: Affine2D, *, binary: bool = False
) -> bool:
    layer = canvas._get_active_layer()
    if layer is None or getattr(layer, "native_cmyk", None) is None:
        canvas._native_selection_preview = None
        return False
    previous: NativeCmykSelectionPreview | None = getattr(canvas, "_native_selection_preview", None)
    reusable = previous is not None and previous.payload is payload and previous.binary == binary
    pixels = (
        previous.native.pixels
        if previous is not None and reusable
        else payload_pixels(payload, layer.native_cmyk, binary=binary)
    )
    inverse = placement(layer).inverse()
    if inverse is None:
        return False
    local = compose(inverse, transform)
    destination = _affine_bounds(local, payload.bounds[2], payload.bounds[3])
    if destination is None:
        return False
    bounds = expanded_bounds(layer.native_cmyk, QRect(*destination))
    mask_edit = NativeSelectionMaskCommand(canvas, payload, transform, binary=binary)
    selected = canvas.channels().selected
    if (
        previous is not None
        and reusable
        and previous.native.base is layer.native_cmyk
        and previous.native.bounds == bounds
        and previous.native.selected == selected
        and previous.native.alpha_lock == layer.alpha_lock
    ):
        native = previous.native
        native.move(local)
    else:
        native = NativeSelectionSource(
            layer.native_cmyk, pixels, local, bounds, selected, layer.alpha_lock
        )
    canvas._native_selection_preview = NativeCmykSelectionPreview(
        payload, layer.id, native, rebase(layer, bounds.x(), bounds.y()), binary
    )
    mask_edit.apply(canvas)
    return True
