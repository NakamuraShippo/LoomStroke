"""Capture a filter stroke's raster target and selection on the UI thread."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.comic_panels.panel_gate import panel_stroke_reason
from loomstroke.core.command_types import LayerTransform
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.raster_edit_geometry import (
    RasterEditGeometry,
    clip_signature,
    raster_ancestors,
)
from loomstroke.core.selection_state import SelectionStateService


@dataclass
class FilterPenSnapshot:
    layer: Any
    image: QImage
    origin: tuple[int, int]
    canvas_size: tuple[int, int]
    document_dpi: float
    texture_version: int
    transform: Any
    alpha_lock: bool
    selection: QImage | None
    selection_bounds: QRect | None
    selected_channels: tuple[int, ...] = (0, 1, 2)
    geometry: RasterEditGeometry | None = None
    ancestor_signature: tuple = ()
    clip_masks: tuple[QImage, ...] = ()
    native: Any = None
    native_identity: int | None = None
    native_version: int | None = None

    @classmethod
    def capture(cls, canvas: Any) -> FilterPenSnapshot:
        layer = canvas._get_active_layer()
        if layer is None or layer.layer_type != "raster" or layer.image is None:
            raise ValueError("Filter Pen requires a raster layer.")
        native = getattr(layer, "native_cmyk", None)
        if layer.locked or layer.mask_edit_mode:
            raise ValueError("Filter Pen cannot paint a locked layer or a layer mask.")
        transform = getattr(layer, "transform", LayerTransform.identity())
        ancestors = tuple(raster_ancestors(canvas, layer))
        placed = transform.scale_x != 1.0 or transform.scale_y != 1.0 or transform.rotation != 0.0
        if native is not None:
            placed = (
                placed or transform.tx != round(transform.tx) or transform.ty != round(transform.ty)
            )
        content = any(
            (node.metadata or {}).get(PART_KEY, {}).get("role") == "content" for node in ancestors
        )
        if placed and not content and native is None:
            raise ValueError("Rasterize the layer transform before using Filter Pen.")
        if panel_stroke_reason(canvas):
            raise ValueError("The panel drawing target is not editable.")
        geometry = RasterEditGeometry.capture(layer) if placed else None
        mask, bounds = SelectionStateService(canvas).selection_clip_state()
        return cls(
            layer=layer,
            image=layer.image.copy(),
            origin=(int(layer.offset_x), int(layer.offset_y)),
            canvas_size=(int(canvas._width), int(canvas._height)),
            document_dpi=float(canvas._dpi),
            texture_version=int(layer.texture_version),
            transform=transform,
            alpha_lock=bool(layer.alpha_lock),
            selection=(
                mask.convertToFormat(QImage.Format.Format_Alpha8).copy()
                if mask is not None
                else None
            ),
            selection_bounds=QRect(bounds) if bounds is not None else None,
            selected_channels=(
                canvas.channels().selected if hasattr(canvas, "channels") else (0, 1, 2)
            ),
            geometry=geometry,
            ancestor_signature=clip_signature(canvas, layer),
            clip_masks=tuple(
                QImage(node.layer_mask)
                for node in ancestors
                if node.mask_enabled and node.layer_mask is not None
            ),
            native=native.snapshot() if native is not None else None,
            native_identity=id(native) if native is not None else None,
            native_version=native.version if native is not None else None,
        )

    def valid(self, canvas: Any, version: int, *, native_version: int | None = None) -> bool:
        layer = canvas._get_active_layer()
        try:
            ancestors_match = (
                layer is not None and clip_signature(canvas, layer) == self.ancestor_signature
            )
        except ValueError:
            return False
        return bool(
            layer is self.layer
            and layer.image is not None
            and layer.image.size() == self.image.size()
            and (layer.offset_x, layer.offset_y) == self.origin
            and layer.transform == self.transform
            and not layer.locked
            and not layer.mask_edit_mode
            and layer.alpha_lock == self.alpha_lock
            and layer.texture_version == version
            and (
                (id(layer.native_cmyk) if getattr(layer, "native_cmyk", None) is not None else None)
                == self.native_identity
            )
            and (
                self.native is None
                or layer.native_cmyk.version
                == (self.native_version if native_version is None else native_version)
            )
            and getattr(layer, "_preview_transform", None) is None
            and ancestors_match
            and (
                not hasattr(canvas, "channels")
                or canvas.channels().selected == self.selected_channels
            )
        )


# Compatibility aliases for existing imports.
EffectPenSnapshot = FilterPenSnapshot


_bind_legacy_names(__name__, {"EffectPenSnapshot": "FilterPenSnapshot"})
