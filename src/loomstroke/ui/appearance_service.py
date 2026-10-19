"""Fill layers, layer style stacks and Rasterize Appearance through canvas commands (CT-21).

A fill layer is an ordinary raster layer whose pixels are regenerated from its
``FillSpec`` (metadata) — no new layer type, so old readers keep the pixels.
Every operation is one history transaction.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.appearance import (
    AppearanceLayerMetadataCodec,
    AppearanceRegistry,
    FillRenderer,
    FillSpec,
    LayerStyleRasterizer,
    LayerStyleStack,
)
from loomstroke.core.appearance.add_appearance_layer_command import AddAppearanceLayerCommand
from loomstroke.core.appearance.layer_source_rasterizer import appearance_source_image
from loomstroke.core.commands_layer_image_replace import ReplaceLayerImageCommand
from loomstroke.core.commands_layer_lookup import find_layer
from loomstroke.core.commands_layer_metadata_key import SetLayerMetadataKeyCommand
from loomstroke.core.commands_layer_transform import LayerVisibilityCommand

__all__ = ["AppearanceService"]


class AppearanceService:
    def __init__(self, host: Any, *, registry: AppearanceRegistry | None = None) -> None:
        self._host = host
        self._registry = registry or AppearanceRegistry()
        self._codec = AppearanceLayerMetadataCodec()
        self._fills = FillRenderer(self._registry)
        self._styles = LayerStyleRasterizer(self._registry)

    @property
    def registry(self) -> AppearanceRegistry:
        return self._registry

    @property
    def canvas(self) -> Any:
        return getattr(self._host, "canvas", None)

    # -- queries -----------------------------------------------------------------------

    def canvas_size(self) -> tuple[int, int]:
        canvas = self.canvas
        width = int(getattr(canvas, "_width", 0) or 0)
        height = int(getattr(canvas, "_height", 0) or 0)
        return (width or 1, height or 1)

    def layer(self, layer_id: str) -> Any | None:
        return find_layer(self.canvas, layer_id)

    def fill_for(self, layer_id: str) -> FillSpec | None:
        layer = self.layer(layer_id)
        return self._codec.read_fill(getattr(layer, "metadata", None)) if layer else None

    def styles_for(self, layer_id: str) -> LayerStyleStack:
        layer = self.layer(layer_id)
        return (
            self._codec.read_styles(getattr(layer, "metadata", None))
            if layer
            else LayerStyleStack()
        )

    @staticmethod
    def new_fill_spec(kind: str, params: dict[str, Any]) -> FillSpec:
        return FillSpec(f"fill-{uuid.uuid4().hex[:8]}", kind, dict(params))

    # -- fills -------------------------------------------------------------------------

    def add_fill_layer(self, spec: FillSpec, *, name: str | None = None) -> str | None:
        canvas = self.canvas
        if canvas is None:
            return None
        width, height = self.canvas_size()
        image = self._qimage_from_rgba(self._fills.render(spec, width, height))
        self._begin("Add Fill Layer")
        try:
            add = AddAppearanceLayerCommand(name or f"Fill ({spec.kind})")
            if not canvas.submit(add):
                return None
            layer_id = getattr(add, "_layer_id", None)
            if not layer_id:
                return None
            canvas.submit(ReplaceLayerImageCommand(str(layer_id), image))
            self._write_fill(str(layer_id), spec)
        finally:
            self._end()
        return str(layer_id)

    def update_fill(self, layer_id: str, spec: FillSpec) -> bool:
        canvas = self.canvas
        if canvas is None or self.layer(layer_id) is None:
            return False
        width, height = self.canvas_size()
        image = self._qimage_from_rgba(self._fills.render(spec, width, height))
        self._begin("Edit Fill")
        try:
            ok = bool(canvas.submit(ReplaceLayerImageCommand(layer_id, image)))
            ok = self._write_fill(layer_id, spec) and ok
        finally:
            self._end()
        return ok

    def _write_fill(self, layer_id: str, spec: FillSpec | None) -> bool:
        layer = self.layer(layer_id)
        metadata = getattr(layer, "metadata", None) if layer else None
        payload = self._codec.payload_with_fill(metadata, spec)
        return bool(
            self.canvas.submit(SetLayerMetadataKeyCommand(layer_id, self._codec.NAMESPACE, payload))
        )

    # -- styles ------------------------------------------------------------------------

    def set_styles(
        self, layer_id: str, stack: LayerStyleStack, *, transaction: bool = True
    ) -> bool:
        canvas = self.canvas
        layer = self.layer(layer_id)
        if canvas is None or layer is None:
            return False
        payload = self._codec.payload_with_styles(getattr(layer, "metadata", None), stack)
        if transaction:
            self._begin("Layer Styles")
        try:
            return bool(
                canvas.submit(SetLayerMetadataKeyCommand(layer_id, self._codec.NAMESPACE, payload))
            )
        finally:
            if transaction:
                self._end()
            else:
                # A modal live editor owns the history group. Publish each new
                # stack without bumping source texture versions or GPU resources.
                recompose = getattr(canvas, "_recompose", None)
                if callable(recompose):
                    recompose()

    def rasterize_appearance(self, layer_id: str, *, keep_source: bool = True) -> str | None:
        canvas = self.canvas
        layer = self.layer(layer_id)
        if canvas is None or layer is None:
            return None
        from .canvas.wgpu_backend.style_backdrop import needs_backdrop  # noqa: PLC0415

        if needs_backdrop(layer):
            from .backdrop_style_bake import bake_backdrop_styles  # noqa: PLC0415

            return bake_backdrop_styles(self, layer, keep_source=keep_source)
        flatten = getattr(canvas, "_flatten_layers", None)
        nodes = flatten() if callable(flatten) else getattr(canvas, "_layers", ())
        if any(getattr(n, "native_cmyk", None) is not None for n in nodes):
            from .native_layer_style_bake import bake_native_styles  # noqa: PLC0415

            return bake_native_styles(self, layer, keep_source=keep_source)
        return self._rasterize_rgb_appearance(layer_id, layer, keep_source=keep_source)

    def _rasterize_rgb_appearance(
        self, layer_id: str, layer: Any, *, keep_source: bool
    ) -> str | None:
        canvas = self.canvas
        source = appearance_source_image(layer, *self.canvas_size())
        if not isinstance(source, QImage) or source.isNull():
            return None
        rendered = self._styles.render(self._rgba_from_qimage(source), self.styles_for(layer_id))
        image = self._qimage_from_rgba(rendered)
        name = str(getattr(layer, "name", "") or "Layer")
        self._begin("Rasterize Appearance")
        try:
            add = AddAppearanceLayerCommand(f"{name} (rasterized)")
            if not canvas.submit(add):
                return None
            new_id = getattr(add, "_layer_id", None)
            if not new_id:
                return None
            canvas.submit(ReplaceLayerImageCommand(str(new_id), image))
            if not keep_source:
                canvas.submit(LayerVisibilityCommand(layer_id, False))
        finally:
            self._end()
        return str(new_id)

    # -- helpers -----------------------------------------------------------------------

    def _begin(self, label: str) -> None:
        begin = getattr(self.canvas, "begin_transaction", None)
        if callable(begin):
            begin(label)

    def _end(self) -> None:
        end = getattr(self.canvas, "end_transaction", None)
        if callable(end):
            end()

    @staticmethod
    def _qimage_from_rgba(rgba: np.ndarray) -> QImage:
        array = np.ascontiguousarray(rgba.astype(np.uint8, copy=False))
        height, width = array.shape[:2]
        image = QImage(array.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _rgba_from_qimage(image: QImage) -> np.ndarray:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = converted.width(), converted.height()
        stride = converted.bytesPerLine()
        buffer = np.frombuffer(converted.constBits(), dtype=np.uint8, count=stride * height)
        return buffer.reshape(height, stride // 4, 4)[:, :width].copy()
