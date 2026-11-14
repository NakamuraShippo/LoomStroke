"""Prepare and record Merge Down for ordinary RGB rasters with layer masks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from .canvas_history_clear_continuation import _HistoryClearContinuation
from .command_types import BlendMode, Command, LayerTransform
from .events import Events
from .image_size.image_size_planner import ImageSizePlanner
from .layer_history_memory import estimate_retained_layer_bytes
from .raster_history_commit import record_raster_history


def supports_masked_raster_merge(source: Any, target: Any) -> bool:
    return (source.layer_mask is not None or target.layer_mask is not None) and all(
        layer.layer_type == "raster"
        and layer.visible
        and layer.blend_mode == BlendMode.NORMAL
        and not layer.clipping
        and not layer.children
        and not layer.vector_objects
        for layer in (source, target)
    )


@dataclass(frozen=True)
class _RasterState:
    image: QImage
    offset: tuple[int, int] = (0, 0)
    transform: LayerTransform = field(default_factory=LayerTransform.identity)
    opacity: float = 1.0
    mask: QImage | None = None
    mask_enabled: bool = True
    mask_edit_mode: bool = False

    @classmethod
    def capture(cls, layer: Any) -> _RasterState:
        return cls(
            QImage(layer.image),
            (layer.offset_x, layer.offset_y),
            layer.transform,
            layer.opacity,
            QImage(layer.layer_mask) if layer.layer_mask is not None else None,
            layer.mask_enabled,
            layer.mask_edit_mode,
        )

    def install(self, layer: Any) -> None:
        # A new QImage handle keeps subsequent edits from mutating the history.
        layer.image = QImage(self.image)
        layer.offset_x, layer.offset_y = self.offset
        layer.transform, layer.opacity = self.transform, self.opacity
        layer.layer_mask = QImage(self.mask) if self.mask is not None else None
        layer.mask_enabled, layer.mask_edit_mode = self.mask_enabled, self.mask_edit_mode
        layer.texture_version += 1
        layer.transform_version += 1


class _MaskedRasterMerge(Command):
    def __init__(
        self, canvas: Any, source: Any, target: Any, result: QImage, region: QRect
    ) -> None:
        super().__init__()
        self.source = source
        self.target_id = target.id
        self.before = _RasterState.capture(target)
        self.after = _RasterState(result, offset=(region.x(), region.y()))
        self.region = region
        self.active_before = canvas._active_index, canvas._active_layer_id

    @property
    def retained_bytes(self) -> int:
        images = (self.before.image, self.before.mask, self.after.image)
        return estimate_retained_layer_bytes(self.source) + sum(
            image.sizeInBytes() for image in images if image is not None
        )

    def merge(self, canvas: Any) -> Any | None:
        location = canvas._find_parent(self.target_id)
        if location is None or location[0] is not canvas._layers:
            return None
        layers, index = location
        if index + 1 >= len(layers) or layers[index + 1].id != self.source.id:
            return None
        target = layers[index]
        self.after.install(target)
        # Keep the original source object and all its edit data for Undo.
        self.source = layers.pop(index + 1)
        canvas._active_index, canvas._active_layer_id = index, target.id
        self._executed = True
        canvas._invalidate_layer_cache()
        return target

    def unmerge(self, canvas: Any) -> Any | None:
        location = canvas._find_parent(self.target_id)
        if not self._executed or location is None or location[0] is not canvas._layers:
            return None
        if canvas._get_layer_by_id(self.source.id) is not None:
            return None
        layers, index = location
        target = layers[index]
        self.before.install(target)
        layers.insert(index + 1, self.source)
        canvas._active_index, canvas._active_layer_id = self.active_before
        self._executed = False
        canvas._invalidate_layer_cache()
        return target

    def notify(self, canvas: Any, target: Any, previous: Any, *, merged: bool) -> None:
        event = Events.LAYER_REMOVED if merged else Events.LAYER_ADDED
        canvas._publish_layer_event(
            event, layer=self.source, change_type="removed" if merged else "added"
        )
        active = canvas._get_active_layer()
        if active is not None and active is not previous:
            canvas._emit_layer_selected(previous, active)
        canvas._emit_layer_content_changed(target, self.region, {"source": "merge_down"})
        canvas._recompose()

    def apply(self, canvas: Any) -> bool:
        previous = canvas._get_active_layer()
        target = self.merge(canvas)
        if target is None:
            return False
        self.notify(canvas, target, previous, merged=True)
        return True

    def revert(self, canvas: Any) -> bool:
        previous = canvas._get_active_layer()
        target = self.unmerge(canvas)
        if target is None:
            return False
        self.notify(canvas, target, previous, merged=False)
        return True


def submit_masked_raster_merge(
    canvas: Any,
    source: Any,
    target: Any,
    canvas_image: Callable[[Any, QRect], QImage | None],
    layer_bounds: Callable[[Any], tuple[int, int, int, int] | None],
) -> bool:
    # Use committed images and placement. A missing image must not delete a layer.
    layers = (target, source)
    if any(layer.image.isNull() or layer._preview_transform is not None for layer in layers):
        return False
    target_bounds, source_bounds = (layer_bounds(layer) for layer in layers)
    if target_bounds is None or source_bounds is None:
        return False
    bounds = (target_bounds, source_bounds)
    left = min(0, target_bounds[0], source_bounds[0])
    top = min(0, target_bounds[1], source_bounds[1])
    right = max(canvas._width, *(x + width for x, _, width, _ in bounds))
    bottom = max(canvas._height, *(y + height for _, y, _, height in bounds))
    width, height = right - left, bottom - top
    limit = ImageSizePlanner.MAX_DIMENSION
    if width > max(canvas._width, limit) or height > max(canvas._height, limit):
        return False
    region = QRect(left, top, width, height)
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    if result.isNull():
        return False
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    try:
        for layer, (x, y, w, h) in zip(layers, bounds, strict=True):
            image = canvas_image(layer, QRect(x, y, w, h))
            if image is None or image.isNull():
                return False
            painter.setOpacity(layer.opacity)
            painter.drawImage(x - left, y - top, image)
    finally:
        painter.end()

    command = _MaskedRasterMerge(canvas, source, target, result, region)
    previous = canvas._get_active_layer()
    owner = _HistoryClearContinuation(canvas)

    def rollback() -> None:
        if command._executed and command.unmerge(canvas) is None:
            raise RuntimeError("Could not restore the rejected masked merge")

    committed = record_raster_history(
        canvas,
        lambda: canvas._record_history_command("Merge Down", command),
        rollback,
        apply=lambda: command.merge(canvas) is not None,
    )
    if committed and owner.is_current():
        command.notify(canvas, target, previous, merged=True)
    return committed
