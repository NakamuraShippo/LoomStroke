"""Fill displayed regions while retaining a placed raster's original pixels."""

from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import QRect

from ..channels.channel_pixel_policy import ChannelPixelPolicy
from ..raster_edit_geometry import RasterEditGeometry, edit_coverage
from ..raster_region_command import RasterRegionCommand, preserve_pixel_policy
from ..raster_selection_reference import image_rgba_copy, raster_selection_reference
from .flood_fill import flood_fill_spans


def prepare_placed_spans(command: Any, canvas: Any, layer: Any) -> bool:
    width, height = canvas.get_canvas_size()
    projected = raster_selection_reference(
        canvas, layer, image_rgba_copy(layer.image), width, height
    )
    if projected is None:
        return False
    reference, coverage = projected
    allowed = coverage > 0
    if command._selection_clip is not None:
        allowed &= command._selection_clip.mask > 0
    result = flood_fill_spans(
        reference,
        command.seed_x,
        command.seed_y,
        tolerance=command.color_tolerance,
        gap_close_px=command.gap_close_px,
        connectivity=command.connectivity,
        allowed=allowed,
    )
    command._spans, command._bounds = result.spans, result.bounds
    return bool(result.spans)


def apply_placed_fill(  # noqa: PLR0911 - reject distinct invalid/empty edits before committing
    command: Any, canvas: Any, layer: Any, *, pattern: bool = False
) -> bool:
    placed = getattr(command, "_placed_edit", None)
    if placed is not None:
        # Redo restores the committed component/selection result even when
        # the user has since changed the current channel or selection.
        return placed.write_pixels(layer, before=False)
    try:
        geometry = RasterEditGeometry.capture(layer)
    except ValueError:
        return False
    bounds = QRect(*command._bounds)
    local = geometry.source_rect(bounds)
    if local.isEmpty():
        return False
    # Spans live in document coordinates. Pull only their covered region back
    # into the source, with the same phase for a document-anchored pattern.
    mask = np.zeros((bounds.height(), bounds.width()), dtype=np.uint8)
    for y, left, right in command._spans:
        if bounds.top() <= y <= bounds.bottom():
            lo, hi = max(left, bounds.left()), min(right, bounds.right())
            if hi >= lo:
                mask[y - bounds.y(), lo - bounds.x() : hi - bounds.x() + 1] = 255
    x, y = geometry.document_grid(local)
    ix, iy = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    inside = (
        (ix >= bounds.left())
        & (iy >= bounds.top())
        & (ix <= bounds.right())
        & (iy <= bounds.bottom())
    )
    amount = np.zeros(x.shape, dtype=np.uint8)
    amount[inside] = mask[iy[inside] - bounds.y(), ix[inside] - bounds.x()]
    coverage = edit_coverage(canvas, layer, geometry, local)
    if command._selection_clip is not None:
        selection = command._selection_clip.mask
        selected = np.zeros(x.shape, dtype=np.uint8)
        valid = (ix >= 0) & (iy >= 0) & (ix < selection.shape[1]) & (iy < selection.shape[0])
        selected[valid] = selection[iy[valid], ix[valid]]
        coverage = ((coverage.astype(np.uint16) * selected + 127) // 255).astype(np.uint8)
    amount = (amount.astype(np.float32) / 255) * (coverage.astype(np.float32) / 255)
    if not amount.any():
        return False
    before = layer.image.copy(local)
    pixels = ChannelPixelPolicy.pixels(before).astype(np.float32)
    if pattern:
        tile = command._scaled_pattern()
        ox, oy = command._pattern_offset
        rgba = tile[(iy + oy) % tile.shape[0], (ix + ox) % tile.shape[1]]
    else:
        rgba = np.empty((*x.shape, 4), dtype=np.uint8)
        rgba[:, :] = command.fill_rgba
    alpha = rgba[:, :, 3].astype(np.float32) / 255 * amount
    pixels[:, :, :3] = rgba[:, :, [2, 1, 0]] * alpha[:, :, None] + pixels[:, :, :3] * (
        1 - alpha[:, :, None]
    )
    pixels[:, :, 3] = 255 * alpha + pixels[:, :, 3] * (1 - alpha)
    after = preserve_pixel_policy(
        canvas,
        layer,
        before,
        ChannelPixelPolicy.image(np.clip(pixels + 0.5, 0, 255).astype(np.uint8)),
    )
    if before == after:
        return False
    edit = RasterRegionCommand(
        layer, local, before, after, kind="pattern_fill" if pattern else "bucket_fill"
    )
    if not edit.write_pixels(layer, before=False):
        return False
    command._placed_edit = edit
    command._target_layer_id = layer.id
    command._dirty_region = edit.dirty_region
    return True
