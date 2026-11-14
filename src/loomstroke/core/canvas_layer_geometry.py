"""Placement and mask updates for ordinary page flips, turns and crops."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from PySide6.QtGui import QImage

from .comic_panels.canvas_transform_basis import page_matrix, reversed_winding
from .command_layer_access import _set_layer_transform
from .geometry import Affine2D


def update_layer_masks(canvas: Any, layer: Any, transform: Callable[[QImage], QImage]) -> None:
    changed = False
    for name in ("layer_mask", "effect_mask"):
        mask = getattr(layer, name, None)
        if mask is None or mask.isNull():
            continue
        setattr(layer, name, transform(mask))
        if name == "effect_mask":
            layer.effect_mask_version += 1
        changed = True
    if changed:
        canvas._clear_filter_cache(layer)
        canvas._gpu_mask_dirty_layers.add(layer.id)


def rebase_layer_placement(
    layer: Any, page: Affine2D, mode: str, old_image_size: tuple[int, int] | None
) -> None:
    transform = layer.transform
    if transform.to_affine() == Affine2D.identity():
        if old_image_size is None and layer.vector_objects:
            ox, oy = layer.offset_x, layer.offset_y
            layer.offset_x = round(page.a * ox + page.b * oy)
            layer.offset_y = round(page.c * ox + page.d * oy)
        return

    local = page if old_image_size is None else page_matrix(mode, *old_image_size)
    # The page and the local pixels turn together. Map the old pivot in
    # each space, then re-express the same placement for the new local pixels.
    origin_x, origin_y = local.apply(transform.origin_x, transform.origin_y)
    world_x, world_y = page.apply(
        transform.origin_x + transform.tx, transform.origin_y + transform.ty
    )
    quarter_turn = local.a == 0
    _set_layer_transform(
        layer,
        replace(
            transform,
            tx=world_x - origin_x,
            ty=world_y - origin_y,
            origin_x=origin_x,
            origin_y=origin_y,
            scale_x=transform.scale_y if quarter_turn else transform.scale_x,
            scale_y=transform.scale_x if quarter_turn else transform.scale_y,
            rotation=-transform.rotation if reversed_winding(page) else transform.rotation,
        ),
    )


def translate_layer_placement(layer: Any, dx: int, dy: int) -> bool:
    """Return whether the original raster buffer is kept behind its transform."""
    transform = layer.transform
    if transform.to_affine() == Affine2D.identity():
        return False
    if layer.image is not None:
        _set_layer_transform(
            layer, transform.with_translation(transform.tx + dx, transform.ty + dy)
        )
        return True
    # Objects without a backing image move in page coordinates as well.
    _set_layer_transform(
        layer,
        replace(transform, origin_x=transform.origin_x + dx, origin_y=transform.origin_y + dy),
    )
    return False
