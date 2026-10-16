"""Main-thread capture and stale-result checks for reference-based fills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.commands import UpdateSelectionMaskCommand, _layer_allows_edit
from loomstroke.core.layer_tree_service import LayerTreeQueryService
from loomstroke.core.raster_edit_geometry import uses_placed_pixels
from loomstroke.core.raster_selection_reference import image_rgba_copy, raster_selection_reference


@dataclass(frozen=True)
class FillContextSnapshot:
    target_id: str
    size: tuple[int, int]
    signature: tuple[Any, ...]
    reference: np.ndarray
    selection: np.ndarray | None
    canvas_identity: int
    allowed: np.ndarray | None = None

    @classmethod
    def capture(cls, canvas: Any, reference_mode: str) -> FillContextSnapshot:
        query = LayerTreeQueryService(canvas)
        target = query.get_active_layer()
        if target is None or not _layer_allows_edit(target) or target.layer_type != "raster":
            raise ValueError("Fill requires an unlocked raster layer.")
        width, height = canvas.get_canvas_size()
        projected = None
        if uses_placed_pixels(canvas, target):
            projected = raster_selection_reference(
                canvas, target, image_rgba_copy(target.image), width, height
            )
            if projected is None:
                raise ValueError("The fill placement could not be read.")
        if reference_mode == "Visible Layers":
            pixmap = canvas.get_composited_pixmap()
            if pixmap is None or pixmap.isNull():
                raise ValueError("The fill reference could not be read.")
            image = pixmap.toImage()
        elif projected is None:
            image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            from PySide6.QtGui import QPainter  # noqa: PLC0415

            painter = QPainter(image)
            painter.drawImage(target.offset_x, target.offset_y, target.image)
            painter.end()
        if projected is not None and reference_mode != "Visible Layers":
            reference = projected[0]
        else:
            reference = image_rgba_copy(image)
        allowed = projected[1] if projected is not None else None
        mask, _ = UpdateSelectionMaskCommand._read_selection(canvas)
        selection = (
            UpdateSelectionMaskCommand._mask_to_numpy(mask, width, height).copy()
            if mask is not None
            else None
        )
        reference.setflags(write=False)
        if selection is not None:
            selection.setflags(write=False)
            if allowed is not None:
                allowed = np.where(selection > 0, allowed, np.uint8(0))
        if allowed is not None:
            allowed.setflags(write=False)
        return cls(
            target.id, (width, height), cls.state(canvas), reference, selection, id(canvas), allowed
        )

    def matches(self, canvas: Any) -> bool:
        if id(canvas) != self.canvas_identity or canvas.get_canvas_size() != self.size:
            return False
        if self.state(canvas) != self.signature:
            return False
        query = LayerTreeQueryService(canvas)
        target = query.get_active_layer()
        if target is None or target.id != self.target_id or not _layer_allows_edit(target):
            return False
        mask, _ = UpdateSelectionMaskCommand._read_selection(canvas)
        if mask is None:
            return self.selection is None
        return self.selection is not None and np.array_equal(
            UpdateSelectionMaskCommand._mask_to_numpy(mask, *self.size), self.selection
        )

    @staticmethod
    def state(canvas: Any) -> tuple[Any, ...]:
        return tuple(
            (
                layer.id,
                layer.texture_version,
                layer.visible,
                layer.opacity,
                layer.blend_mode,
                layer.offset_x,
                layer.offset_y,
                layer.locked,
                layer.alpha_lock,
                layer.image.cacheKey() if isinstance(layer.image, QImage) else None,
                layer.transform_version,
                repr(layer.transform),
                repr(layer._preview_transform),
                layer.parent_id,
                tuple(child.id for child in layer.children),
                layer.clipping,
                layer.mask_enabled,
                layer.layer_mask.cacheKey() if isinstance(layer.layer_mask, QImage) else None,
                layer.effect_mask_version,
                repr(layer.effect_params),
                layer.effect_cache_output_ver,
            )
            for layer in LayerTreeQueryService(canvas).flatten_layers()
        )
