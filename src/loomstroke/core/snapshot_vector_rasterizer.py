"""Committed vector rasterization without a live canvas or GUI pixmap."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QImage, QPainterPath

from .canvas_layer_model import _Layer
from .canvas_service_simple_accessors import CanvasServiceAccessorsMixin
from .canvas_service_simple_render_api import CanvasServiceRenderApiMixin
from .canvas_service_simple_text_api import CanvasServiceTextApiMixin
from .comic_panels.raster_region import is_comic_record_carrier
from .vector_text_render_service import (
    VectorRasterResult,
    VectorTextRenderService,
    clamp_raster_scale_to_max_dim,
)


class SnapshotVectorRasterizer(
    CanvasServiceRenderApiMixin, CanvasServiceTextApiMixin, CanvasServiceAccessorsMixin
):
    """Reuse native vector/text primitives with only document-local state.

    Shapes render into QImage without a GUI application. Text still needs
    Qt's GUI font services; callers must check that requirement before use.
    No pixmap, widget, EventBus, history, or editing preview is created.
    """

    def __init__(self, *, width: int, height: int) -> None:
        self._width = int(width)
        self._height = int(height)
        self._layers: list[_Layer] = []
        self._qpath_cache: dict[int, list[QPainterPath]] = {}
        self._vector_text_render_service = VectorTextRenderService(self)

    def rasterize(self, layer: Any) -> QImage:
        """Capture committed objects through the normal core rasterizer."""
        return self._rasterize_vector_layer(layer)

    def rasterize_for_preview(self, layer: Any, *, scale: int = 1) -> VectorRasterResult | None:
        """Render only the committed primitive's bounds, without a canvas cache."""
        if is_comic_record_carrier(layer):
            return None
        from .comic_panels.vector_page_basis import rasterize_in_page_basis  # noqa: PLC0415

        basis = rasterize_in_page_basis(self, layer, scale=scale, for_render=True)
        if basis is not None:
            return basis
        region = self._vector_text_render_service._layer_raster_region(layer)
        width, height = (region[2], region[3]) if region else (self._width, self._height)
        scale = clamp_raster_scale_to_max_dim(scale, width, height)
        image = self._rasterize_vector_layer(layer, scale=scale, region=region)
        if image is None:
            return None
        return VectorRasterResult(
            image, scale, region[0] if region else 0, region[1] if region else 0
        )
