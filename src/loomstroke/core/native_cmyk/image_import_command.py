"""Prepare imported RGB pixels as native inks before publishing an undoable edit."""

from __future__ import annotations

import uuid
from io import BytesIO

import numpy as np
from PIL import ImageCms
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from ..canvas_layer_model import _Layer
from ..channels.channel_pixel_policy import ChannelPixelPolicy
from ..command_types import Command
from ..events import Events
from .buffer import NativeCmykBuffer
from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


class NativeCmykImageImportCommand(Command):
    def __init__(self, canvas, image, name, *, insert_above=True, as_new=True, source_profile=None):
        super().__init__()
        if canvas._in_transaction:
            raise ValueError("Finish the current edit before importing an image")
        self._active_before = canvas.current_layer().id if canvas._layers else None
        self._new = as_new or self._active_before is None
        if self._new and len(canvas._layers) >= canvas._max_layers:
            raise ValueError("Maximum layer count reached")
        reference = next(
            layer.native_cmyk
            for layer in canvas._layer_tree().flatten_layers()
            if layer.native_cmyk is not None
        )
        target = None if self._new else canvas._get_layer_by_id(self._active_before)
        if not self._new and (
            target is None or target.layer_type != "raster" or target.native_cmyk is None
        ):
            raise ValueError("A native CMYK raster layer is required for image import")
        if target is not None:
            reference = target.native_cmyk
        old_height, old_width = (
            (canvas._height, canvas._width) if target is None else reference.alpha.shape
        )
        height, width = max(old_height, image.shape[0]), max(old_width, image.shape[1])
        dtype = reference.inks.dtype
        if width * height * 5 * dtype.itemsize > 256 * 1024 * 1024:
            raise ValueError("CMYK import exceeds the decoded layer budget")
        if source_profile is not None:
            # FileIO already converts non-RGB input to RGBA. Its original CMYK/Lab
            # profile cannot describe those decoded RGB samples.
            parsed = ImageCms.ImageCmsProfile(BytesIO(source_profile))
            if parsed.profile.xcolor_space.strip() != "RGB":
                source_profile = None
        inks, alpha = CmykColorTransform.from_rgb(
            image, reference.profile, source_profile=source_profile
        )
        if dtype == np.uint16:
            inks, alpha = inks.astype(np.uint16) * 257, alpha.astype(np.uint16) * 257
        output = np.zeros((height, width, 4), dtype)
        coverage = np.zeros((height, width), dtype)
        if target is not None:
            output[:old_height, :old_width] = reference.inks
            coverage[:old_height, :old_width] = reference.alpha
        output[: image.shape[0], : image.shape[1]] = inks
        coverage[: image.shape[0], : image.shape[1]] = alpha
        pixels = NativeCmykPixels(output, coverage, reference.profile)
        self._after = NativeCmykBuffer(pixels)
        patch = ChannelPixelPolicy.image(
            CmykColorTransform.to_rgb(inks, alpha, reference.profile), straight=True
        )
        self._preview = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        if self._preview.isNull():
            raise MemoryError("Failed to allocate the imported layer preview")
        self._preview.fill(Qt.GlobalColor.transparent)
        painter = QPainter(self._preview)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        if target is not None:
            painter.drawImage(0, 0, target.image)
        painter.drawImage(0, 0, patch)
        painter.end()
        self._before = None if target is None else (target.native_cmyk, target.image.copy())
        self.layer_id = f"layer-{uuid.uuid4().hex}" if target is None else target.id
        self._layer = (
            _Layer(id=self.layer_id, name=name, image=self._preview.copy(), native_cmyk=self._after)
            if target is None
            else None
        )
        # Preserve the existing root-level insertion policy, including background protection.
        active_index = canvas._active_index
        self._index = min(len(canvas._layers), max(1, active_index + (1 if insert_above else 0)))

    def apply(self, canvas) -> bool:
        if self._new:
            if canvas._get_layer_by_id(self.layer_id) is not None:
                return False
            layer = self._layer
            canvas._layers.insert(min(self._index, len(canvas._layers)), layer)
        else:
            layer = canvas._get_layer_by_id(self.layer_id)
            if layer is None:
                return False
        layer.native_cmyk = self._after
        layer.image = self._preview.copy()
        canvas.set_active_layer(self.layer_id)
        if self._new:
            canvas._publish_layer_event(Events.LAYER_ADDED, layer=layer, change_type="added")
        self._notify(canvas, layer)
        self._executed = True
        return True

    def revert(self, canvas) -> bool:
        if not self._executed:
            return False
        if self._new:
            if not canvas.remove_layer(self.layer_id):
                return False
        else:
            layer = canvas._get_layer_by_id(self.layer_id)
            if layer is None:
                return False
            layer.native_cmyk, before_image = self._before
            layer.image = before_image.copy()
            self._notify(canvas, layer)
        if self._active_before is not None:
            canvas.set_active_layer(self._active_before)
        self._executed = False
        return True

    @staticmethod
    def _notify(canvas, layer):
        layer.texture_version += 1
        canvas._invalidate_layer_cache()
        rect = QRect(layer.offset_x, layer.offset_y, layer.image.width(), layer.image.height())
        canvas._emit_layer_content_changed(layer, rect, {"command": "native_cmyk_image_import"})
        canvas._recompose()
