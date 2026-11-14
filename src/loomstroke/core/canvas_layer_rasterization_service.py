"""Layer rasterization helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .appearance.appearance_codec import AppearanceLayerMetadataCodec
from .canvas_history_clear_continuation import _HistoryClearContinuation
from .canvas_layer_rasterize_command import RasterizeLayerCommand
from .channels.channel_pixel_policy import ChannelPixelPolicy
from .command_types import BlendMode, LayerTransform
from .document_capture_guard import is_document_capture_blocked
from .native_cmyk.pixels import NativeCmykPixels
from .raster_history_commit import record_raster_history

logger = logging.getLogger("loomstroke.core.canvas_service_simple")


class CanvasLayerRasterizationService:
    """Coordinate vector layer rasterization without owning canvas state."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def _find_vector_layer(self, layer_id: str) -> Any | None:
        target_layer = self._owner._get_layer_by_id(layer_id)
        if target_layer is None:
            logger.warning("Layer not found for rasterize: %s", layer_id)
            return None
        if target_layer.locked:
            logger.info("Layer %s is locked; rasterize skipped", target_layer.name)
            return None
        if getattr(target_layer, "layer_type", "raster") != "vector":
            logger.debug("Layer %s is not vector; rasterize ignored", target_layer.name)
            return None
        return target_layer

    def rasterize_layer(self, layer_id: str) -> bool:
        """Convert in-place for callers that own their own history."""
        target_layer = self._find_vector_layer(layer_id)
        if target_layer is None:
            return False

        target_layer.image = self._owner._rasterize_vector_layer(target_layer)
        target_layer.layer_type = "raster"
        target_layer.vector_objects.clear()
        target_layer.offset_x = 0
        target_layer.offset_y = 0
        target_layer.texture_version += 1

        if getattr(self._owner, "_vector_selection", None):
            try:
                if self._owner._vector_selection.get("layer_id") in (None, target_layer.id):
                    self._owner._vector_selection = None
            except Exception:
                self._owner._vector_selection = None
        if getattr(self._owner, "_vector_preview_layer_id", None) == target_layer.id:
            self._owner._vector_preview = None
            self._owner._vector_preview_layer_id = None

        self._owner._recompose()
        rect = QRect(0, 0, self._owner._width, self._owner._height)
        self._owner._emit_layer_content_changed(
            target_layer,
            rect,
            {"command": "rasterize"},
        )
        logger.info("Rasterized vector layer: %s", target_layer.name)
        return True

    def rasterize_layer_undoable(self, layer_id: str) -> bool:
        canvas = self._owner
        layer = self._find_vector_layer(layer_id)
        if layer is None or self._edit_pending(layer):
            return False
        owner = _HistoryClearContinuation(canvas)
        canvas_size = canvas._width, canvas._height
        objects, version = layer.vector_objects, layer.texture_version
        try:
            image, native = self._prepare_pixels(layer)
        except Exception:
            logger.exception("Could not prepare raster pixels for layer %s", layer_id)
            return False
        if (
            not isinstance(image, QImage)
            or image.isNull()
            or not owner.is_current()
            or (canvas._width, canvas._height) != canvas_size
            or canvas._get_layer_by_id(layer_id) is not layer
            or layer.layer_type != "vector"
            or layer.locked
            or layer.vector_objects is not objects
            or layer.texture_version != version
            or self._edit_pending(layer)
        ):
            return False

        command = RasterizeLayerCommand(canvas, layer, image, native)

        def rollback() -> None:
            if command._executed and command.restore_vector(canvas) is None:
                raise RuntimeError("Could not restore the rejected rasterization")

        committed = record_raster_history(
            canvas,
            lambda: canvas._record_history_command("Rasterize Layer", command),
            rollback,
            apply=lambda: command.install_raster(canvas) is not None,
        )
        if committed and owner.is_current():
            command.notify(canvas, layer)
        return committed

    def _edit_pending(self, layer: Any) -> bool:
        canvas = self._owner
        return (
            canvas._in_transaction
            or getattr(canvas, "_vector_preview", None) is not None
            or layer._preview_transform is not None
            or is_document_capture_blocked(canvas)
        )

    def _prepare_pixels(self, layer: Any) -> tuple[QImage | None, NativeCmykPixels | None]:
        from .snapshot_vector_rasterizer import SnapshotVectorRasterizer  # noqa: PLC0415

        canvas = self._owner
        source = replace(
            layer, vector_objects=deepcopy(layer.vector_objects), metadata=deepcopy(layer.metadata)
        )
        reference = next(
            (item for item in canvas._flatten_layers() if item.native_cmyk is not None), None
        )
        if reference is None:
            rasterizer = SnapshotVectorRasterizer(width=canvas._width, height=canvas._height)
            return rasterizer.rasterize(source), None

        renderer = getattr(
            getattr(canvas, "_canvas_widget", None), "render_native_cmyk_layers", None
        )
        if not callable(renderer):
            return None, None
        # Prepare raw ink. The original layer retains its visual properties.
        metadata = source.metadata or {}
        metadata.pop(AppearanceLayerMetadataCodec.NAMESPACE, None)
        neutral = replace(
            source,
            visible=True,
            opacity=1.0,
            blend_mode=BlendMode.NORMAL,
            clipping=False,
            children=[],
            layer_mask=None,
            offset_x=0,
            offset_y=0,
            transform=LayerTransform.identity(),
            metadata=metadata,
        )
        pixels = renderer(
            [replace(reference, visible=False, children=[]), neutral], committed_vectors=True
        )
        if not isinstance(pixels, NativeCmykPixels) or (
            pixels.profile != reference.native_cmyk.profile
            or pixels.inks.dtype != reference.native_cmyk.inks.dtype
            or pixels.alpha.shape != (canvas._height, canvas._width)
        ):
            return None, None
        return ChannelPixelPolicy.image(pixels.preview(), straight=True), pixels
