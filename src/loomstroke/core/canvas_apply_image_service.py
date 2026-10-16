"""Apply-image orchestration helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_layer_bounds_service import CanvasLayerBoundsService
from loomstroke.core.layer_tree_service import LayerTreeQueryService
from loomstroke.core.selection_state import SelectionStateService

logger = logging.getLogger("loomstroke.core.canvas_service_simple")

ApplyImageContext: TypeAlias = tuple[
    Any,
    QImage,
    QRect,
    QRect,
    QRect,
    QRect,
    QImage | None,
    QPainter.CompositionMode,
    float,
    dict[str, Any],
]


class CanvasApplyImageService:
    """Coordinate ``apply_image`` without depending on ``SimpleCanvasService`` types."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def apply_image_command(self, cmd: Any) -> bool:
        owner = self._owner
        metadata = dict(cmd.metadata) if cmd.metadata else {}
        success = self.apply_image(
            cmd.image,
            position=cmd.position,
            blend_mode=cmd.blend_mode,
            opacity=cmd.opacity,
            metadata=metadata,
        )
        if not success:
            target_layer_id = metadata.get("target_layer_id")
            if target_layer_id is None and owner._layers:
                target_layer_id = owner.current_layer().id
            logger.warning("Failed to apply image command to layer %s", target_layer_id)
        return success

    def apply_image(
        self,
        image: NDArray[Any],
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = "normal",
        opacity: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        owner = self._owner
        context = self.prepare_apply_image_context(
            image=image,
            position=position,
            blend_mode=blend_mode,
            opacity=opacity,
            metadata=metadata,
        )
        if context is None:
            return False

        (
            layer,
            qimage,
            dest_rect,
            clip_rect,
            local_clip,
            local_dest,
            selection_mask,
            composition_mode,
            draw_opacity,
            metadata_copy,
        ) = context
        assert layer.image is not None
        previous_region = layer.image.copy(local_clip)
        channels = owner.channels() if hasattr(owner, "channels") else None
        channel_before = channels.capture(layer, local_clip) if channels else None

        saved_alpha = owner._save_alpha(layer, local_clip)

        if selection_mask is not None:
            self.paint_selected_image(
                layer=layer,
                qimage=qimage,
                dest_rect=dest_rect,
                clip_rect=clip_rect,
                local_clip=local_clip,
                composition_mode=composition_mode,
                opacity=draw_opacity,
                selection_mask=selection_mask,
            )
        else:
            self.paint_image_direct(
                layer=layer,
                qimage=qimage,
                local_dest=local_dest,
                composition_mode=composition_mode,
                opacity=draw_opacity,
            )

        owner._restore_alpha(layer, local_clip, saved_alpha)
        if channels:
            channels.finish(layer, local_clip, channel_before)
        layer.texture_version += 1
        owner._recompose()
        assert layer.image is not None
        after_patch = layer.image.copy(local_clip)
        owner._record_history_patch(
            "Apply Image",
            owner._history_patch_from_images(
                layer.id,
                QRect(clip_rect),
                previous_region,
                after_patch,
                dict(metadata_copy),
            ),
        )
        owner._emit_layer_content_changed(layer, clip_rect, metadata_copy)
        self.log_image_applied(layer=layer, clip_rect=clip_rect)
        return True

    def prepare_apply_image_context(
        self,
        *,
        image: NDArray[Any],
        position: tuple[int, int],
        blend_mode: str,
        opacity: float,
        metadata: dict[str, Any] | None,
    ) -> ApplyImageContext | None:
        owner = self._owner
        prepared_context: ApplyImageContext | None = None
        layer = self.resolve_target_layer(metadata)
        if layer is None:
            logger.error("No target layer available for ApplyImageCommand")
        elif getattr(layer, "layer_type", "raster") == "vector":
            logger.info("Layer %s is vector; apply_image rejected", layer.name)
        elif layer.locked:
            logger.info("Layer %s is locked; apply_image ignored", layer.name)
        elif layer.image is None:
            logger.error("Layer %s has no image buffer", layer.name)
        else:
            prepared = self.prepare_apply_image(
                image=image,
                position=position,
                opacity=opacity,
            )
            if prepared is not None:
                qimage, dest_rect, clip_rect = prepared

                selection_mask, selection_bounds = SelectionStateService(
                    owner
                ).selection_clip_state()
                final_clip: QRect | None
                if selection_mask is not None and selection_bounds is not None:
                    final_clip = self.clip_rect_with_selection(clip_rect, selection_bounds)
                else:
                    final_clip = dest_rect

                if final_clip is not None:
                    layer_data = self.image_apply_layer_context(layer, final_clip)
                    if layer_data is not None:
                        local_clip, local_dest = layer_data
                        composition_mode, draw_opacity = self.resolve_draw_parameters(
                            blend_mode=blend_mode,
                            opacity=opacity,
                        )
                        metadata_copy = dict(metadata) if metadata else {}

                        prepared_context = (
                            layer,
                            qimage,
                            dest_rect,
                            final_clip,
                            local_clip,
                            local_dest,
                            selection_mask,
                            composition_mode,
                            draw_opacity,
                            metadata_copy,
                        )

        return prepared_context

    def resolve_draw_parameters(
        self,
        *,
        blend_mode: str,
        opacity: float,
    ) -> tuple[QPainter.CompositionMode, float]:
        normalized = CanvasBlendService.normalize_blend_mode(blend_mode)
        composition_mode = CanvasBlendService.get_composition_mode(normalized)
        draw_opacity = max(0.0, min(float(opacity), 1.0))
        return composition_mode, draw_opacity

    def resolve_target_layer(self, metadata: dict[str, Any] | None) -> Any | None:
        owner = self._owner
        layer_id = None
        if metadata and metadata.get("target_layer_id"):
            layer_id = str(metadata["target_layer_id"])
        if layer_id:
            layer = LayerTreeQueryService(owner).find_layer_by_id_tree(layer_id)
            if layer:
                return layer
            logger.warning("Target layer %s not found; using active layer", layer_id)
        if not owner._layers:
            return None
        # Resolve through the active-layer id: for a nested active layer
        # _active_index addresses its ancestor group, not the layer itself.
        return owner._get_active_layer()

    def prepare_apply_image(
        self,
        *,
        image: NDArray[Any],
        position: tuple[int, int],
        opacity: float,
    ) -> tuple[QImage, QRect, QRect] | None:
        owner = self._owner
        return self.prepare_apply_image_for_canvas_rect(
            image=image,
            position=position,
            opacity=opacity,
            canvas_rect=QRect(0, 0, int(owner._width), int(owner._height)),
        )

    def prepare_apply_image_for_canvas_rect(
        self,
        *,
        image: NDArray[Any],
        position: tuple[int, int],
        opacity: float,
        canvas_rect: QRect,
    ) -> tuple[QImage, QRect, QRect] | None:
        qimage = self.numpy_to_qimage(image, opacity)
        if qimage is None:
            logger.error("Failed to convert numpy image for ApplyImageCommand")
            return None

        dest_rect = QRect(int(position[0]), int(position[1]), qimage.width(), qimage.height())
        clip_rect = dest_rect.intersected(canvas_rect)
        if clip_rect.isEmpty():
            logger.warning("ApplyImageCommand area outside canvas bounds; skipping")
            return None

        if clip_rect != dest_rect:
            crop_rect = QRect(
                clip_rect.x() - dest_rect.x(),
                clip_rect.y() - dest_rect.y(),
                clip_rect.width(),
                clip_rect.height(),
            )
            qimage = qimage.copy(crop_rect)
            dest_rect = clip_rect

        return qimage, dest_rect, clip_rect

    def numpy_to_qimage(self, image: NDArray[Any], opacity: float) -> QImage | None:
        if image is None:
            return None
        if image.ndim != 3:
            logger.error("Expected image with 3 dimensions, got %s", image.ndim)
            return None
        if image.shape[2] not in (3, 4):
            logger.error("Unsupported channel count %s for ApplyImageCommand", image.shape[2])
            return None
        arr = np.asarray(image)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        if arr.shape[2] == 3:
            alpha = np.full((*arr.shape[:2], 1), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=2)
        if opacity < 1.0:
            alpha = arr[:, :, 3].astype(np.float32)
            alpha = np.clip(alpha * max(0.0, min(opacity, 1.0)), 0, 255)
            arr = arr.copy()
            arr[:, :, 3] = alpha.astype(np.uint8)
        arr = np.require(arr, requirements=["C"])
        height, width = arr.shape[:2]
        bytes_per_line = arr.strides[0]
        qimage = QImage(arr.data, width, height, bytes_per_line, QImage.Format.Format_RGBA8888)
        qimage = qimage.copy()
        return qimage.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def clip_rect_with_selection(
        clip_rect: QRect,
        selection_bounds: QRect | None,
    ) -> QRect | None:
        if selection_bounds is None:
            return clip_rect
        selected = clip_rect.intersected(selection_bounds)
        if selected.isEmpty():
            logger.info("ApplyImageCommand skipped due to empty selection clip")
            return None
        return selected

    def image_apply_layer_context(
        self,
        layer: Any,
        clip_rect: QRect,
    ) -> tuple[QRect, QRect] | None:
        owner = self._owner
        CanvasLayerBoundsService(owner).ensure_layer_image_bounds(layer, clip_rect)
        offset_x = int(layer.offset_x)
        offset_y = int(layer.offset_y)
        local_clip = QRect(clip_rect)
        local_clip.translate(-offset_x, -offset_y)
        local_dest = QRect(clip_rect)
        local_dest.translate(-offset_x, -offset_y)
        if local_clip.isEmpty():
            logger.error("Local clip is empty for image apply")
            return None
        return local_clip, local_dest

    def paint_selected_image(
        self,
        *,
        layer: Any,
        qimage: QImage,
        dest_rect: QRect,
        clip_rect: QRect,
        local_clip: QRect,
        composition_mode: QPainter.CompositionMode,
        opacity: float,
        selection_mask: QImage | None = None,
    ) -> None:
        source_rect = QRect(
            clip_rect.x() - dest_rect.x(),
            clip_rect.y() - dest_rect.y(),
            clip_rect.width(),
            clip_rect.height(),
        )
        clipped = qimage.copy(source_rect)
        if selection_mask is None:
            return
        mask_patch = selection_mask.copy(clip_rect)
        SelectionStateService.apply_selection_mask_to_image(clipped, mask_patch)

        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(composition_mode)
        painter.setOpacity(opacity)
        painter.drawImage(local_clip.topLeft(), clipped)
        painter.end()

    @staticmethod
    def paint_image_direct(
        *,
        layer: Any,
        qimage: QImage,
        local_dest: QRect,
        composition_mode: QPainter.CompositionMode,
        opacity: float,
    ) -> None:
        painter = QPainter(cast("QImage", layer.image))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setCompositionMode(composition_mode)
        painter.setOpacity(opacity)
        painter.drawImage(local_dest.topLeft(), qimage)
        painter.end()

    @staticmethod
    def log_image_applied(*, layer: Any, clip_rect: QRect) -> None:
        logger.info(
            "Applied image to layer %s at (%s, %s) size %sx%s",
            layer.id,
            clip_rect.x(),
            clip_rect.y(),
            clip_rect.width(),
            clip_rect.height(),
        )
