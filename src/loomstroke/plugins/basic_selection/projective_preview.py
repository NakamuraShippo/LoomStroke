"""Temporary canvas projection for a four-corner raster selection."""

from __future__ import annotations

from typing import Any

import numpy as np

from loomstroke.core.command_image_utils import (
    _is_qimage_layer,
    _numpy_rgba_to_bgra,
    _premultiply_rgba,
)
from loomstroke.core.commands import (
    UpdateSelectionMaskCommand,
    _place_mask_on_canvas,
    _resolve_target_layer,
    _set_selection_full_bounds,
)
from loomstroke.core.selection_clipboard import SelectionPayload
from loomstroke.core.selection_projective import Quad, warp_selection_payload
from loomstroke.core.selection_projective_composite import masked_projective_image


def update_projective_preview(
    canvas: Any, payload: SelectionPayload, quad: Quad, *, mask_mode: str
) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]] | None:
    width, height = UpdateSelectionMaskCommand._canvas_size(canvas)
    if width <= 0 or height <= 0:
        return None
    premultiplied = _is_qimage_layer(_resolve_target_layer(canvas, None))
    transformed = warp_selection_payload(
        payload,
        quad,
        premultiplied=premultiplied,
        mask_mode=mask_mode,
        canvas_size=(width, height),
    )
    if transformed is None:
        return None
    return (
        transformed
        if _apply_preview(canvas, transformed, width, height, premultiplied=premultiplied)
        else None
    )


def _apply_preview(
    canvas: Any,
    transformed: tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]],
    width: int,
    height: int,
    *,
    premultiplied: bool = True,
) -> bool:
    dest_image, dest_mask, dest_bounds = transformed
    display_image = (
        dest_image if premultiplied else _numpy_rgba_to_bgra(_premultiply_rgba(dest_image))
    )
    preview_image = masked_projective_image(display_image, dest_mask)
    if preview_image is None:
        return False
    update_preview_mask(canvas, dest_mask, dest_bounds, width, height)
    canvas._selection_preview_image = preview_image
    canvas._selection_preview_bounds = dest_bounds
    return True


def update_preview_mask(
    canvas: Any,
    dest_mask: np.ndarray,
    dest_bounds: tuple[int, int, int, int],
    width: int,
    height: int,
) -> None:
    mask_full, bounds, _ = _place_mask_on_canvas(
        dest_mask,
        position=(dest_bounds[0], dest_bounds[1]),
        width=width,
        height=height,
    )
    if bounds is None:
        UpdateSelectionMaskCommand._set_selection(canvas, None, None)
        _set_selection_full_bounds(canvas, None)
    else:
        canvas_mask = UpdateSelectionMaskCommand._convert_mask_for_canvas(
            canvas, mask_full, width, height
        )
        UpdateSelectionMaskCommand._set_selection(canvas, canvas_mask, bounds)
        _set_selection_full_bounds(canvas, dest_bounds)
