"""Atomic native backing expansion with exact source and placement history."""

from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.command_layer_access import _set_layer_transform
from loomstroke.core.command_types import Command, DirtyRegion
from loomstroke.core.geometry import Affine2D

from .affine import document_rect, placement, sample
from .buffer import NativeCmykBuffer
from .pixels import NativeCmykPixels
from .selection_source import rebase


class NativeSelectionLayerCommand(Command):
    def __init__(self, layer: Any, bounds: QRect, rect: QRect, after: NativeCmykPixels) -> None:
        super().__init__()
        self.layer_id = layer.id
        self.before = (
            layer.native_cmyk,
            QImage(layer.image),
            layer.transform,
            layer.offset_x,
            layer.offset_y,
        )
        pixels = sample(layer.native_cmyk, bounds, Affine2D.identity())
        inks, alpha = pixels.inks.copy(), pixels.alpha.copy()
        x, y = rect.x() - bounds.x(), rect.y() - bounds.y()
        inks[y : y + rect.height(), x : x + rect.width()] = after.inks
        alpha[y : y + rect.height(), x : x + rect.width()] = after.alpha
        pixels = NativeCmykPixels(inks, alpha, pixels.profile)
        transform = rebase(layer, bounds.x(), bounds.y())
        self.after = (
            NativeCmykBuffer(pixels),
            ChannelPixelPolicy.image(pixels.preview(), straight=True),
            transform,
            round(transform.tx),
            round(transform.ty),
        )
        changed = document_rect(bounds, placement(layer))
        self._dirty_region = DirtyRegion(
            changed.x(), changed.y(), changed.width(), changed.height()
        )
        self.retained_bytes = sum(
            state[0].inks.nbytes + state[0].alpha.nbytes + state[1].sizeInBytes()
            for state in (self.before, self.after)
        )

    def apply(self, canvas: Any) -> bool:
        return self._write(canvas, self.after, True)

    def revert(self, canvas: Any) -> bool:
        return self._executed and self._write(canvas, self.before, False)

    def _write(self, canvas: Any, state: Any, executed: bool) -> bool:
        layer = canvas._get_layer_by_id(self.layer_id)
        if layer is None:
            return False
        native, image, transform, x, y = state
        layer.native_cmyk, layer.image = native, QImage(image)
        _set_layer_transform(layer, transform)
        layer.offset_x, layer.offset_y = x, y
        layer.texture_version += 1
        changed = self._dirty_region
        assert changed is not None
        rect = QRect(changed.x, changed.y, changed.width, changed.height)
        canvas._emit_layer_content_changed(layer, rect, {"command": "native_selection_expand"})
        canvas._recompose_region(rect)
        self._executed = executed
        return True
