"""Qt presenter for the optional wgpu bitmap canvas path."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QRect, QThread
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from loomstroke.core.canvas_invalidation import RenderInvalidation

from .canvas_presenter import CanvasPresenterDiagnostics
from .channels_bitmap_renderer import ChannelsBitmapRenderer
from .native_cmyk_projection import NativeCmykProjection
from .render_scene import RenderSceneSnapshot
from .wgpu_backend import WgpuOffscreenRenderer
from .wgpu_backend.types import WgpuRenderError


class WgpuBitmapPresenter:
    """Render wgpu offscreen output into a QPixmap for CanvasWidget."""

    backend_name = "wgpu-bitmap"

    def __init__(self, renderer: Any | None = None) -> None:
        self._renderer = renderer or WgpuOffscreenRenderer()
        self._last_image: QImage | None = None
        self._last_pixmap: QPixmap | None = None
        self._last_warnings: tuple[str, ...] = ()
        self._last_document_scene: RenderSceneSnapshot | None = None
        self._frame_preview_export_scene: RenderSceneSnapshot | None = None
        self._channels_renderer: ChannelsBitmapRenderer | None = None
        self.diagnostics = CanvasPresenterDiagnostics(
            backend_name=self.backend_name,
            adapter_name=self._adapter_name(),
            fallback_tier="wgpu_bitmap",
        )

    @property
    def last_warnings(self) -> tuple[str, ...]:
        return self._last_warnings

    def render_frame(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None,
        *,
        device_pixel_ratio: float,
    ) -> QPixmap | None:
        self._frame_preview_export_scene = (
            scene if scene.frame_preview_source_layers is not None else None
        )
        readback_rect = self._partial_readback_rect(scene, invalidation)
        previous_view = getattr(self._last_document_scene, "channel_view", None)
        if previous_view != scene.channel_view:
            readback_rect = None
        self._last_document_scene = scene
        projection = getattr(self, "_native_cmyk_projection", None)
        if projection is None and any(layer.native_cmyk is not None for layer in scene.flat_layers):
            projection = NativeCmykProjection(getattr(self._renderer, "device_manager", None))
            self._native_cmyk_projection = projection
        if projection is not None:
            scene = projection.scene(scene)
        if scene.channel_view is not None:
            if self._channels_renderer is None:
                self._channels_renderer = ChannelsBitmapRenderer(self._renderer)
            result = self._channels_renderer.render(scene, invalidation)
        else:
            result = self._renderer.render_scene(scene, invalidation, readback_rect=readback_rect)
        self._last_warnings = result.warnings
        dirty_rect = invalidation.dirty_rect_copy() if invalidation is not None else None
        dirty_rect_size = (
            (dirty_rect.width(), dirty_rect.height()) if dirty_rect is not None else None
        )

        qimage_start = time.perf_counter()
        qimage, patch_image, patch_rect = self._result_to_qimage_with_patch(result)
        qimage_ms = (time.perf_counter() - qimage_start) * 1000.0

        qpixmap_start = time.perf_counter()
        pixmap = self._pixmap_from_image_with_patch(qimage, patch_image, patch_rect)
        pixmap.setDevicePixelRatio(max(1.0, float(device_pixel_ratio)))
        qpixmap_ms = (time.perf_counter() - qpixmap_start) * 1000.0

        self._last_image = qimage
        self._last_pixmap = pixmap
        texture_cache = self._texture_cache_estimate()
        self.diagnostics = replace(
            self.diagnostics,
            render_frame_ms=result.metrics.render_frame_ms,
            readback_ms=result.metrics.readback_ms,
            qimage_conversion_ms=qimage_ms,
            qpixmap_conversion_ms=qpixmap_ms,
            scene_build_ms=scene.scene_build_ms,
            scene_flatten_ms=scene.scene_flatten_ms,
            dirty_rect_size=dirty_rect_size,
            full_frame_fallback_count=result.metrics.full_frame_fallbacks,
            layers_seen=result.metrics.layers_seen,
            layers_drawn=result.metrics.layers_drawn,
            full_uploads=result.metrics.full_uploads,
            partial_uploads=result.metrics.partial_uploads,
            partial_upload_fallbacks=result.metrics.partial_upload_fallbacks,
            uploaded_layer_ids=result.metrics.uploaded_layer_ids,
            full_upload_layer_ids=result.metrics.full_upload_layer_ids,
            partial_upload_layer_ids=result.metrics.partial_upload_layer_ids,
            partial_upload_fallback_layer_ids=result.metrics.partial_upload_fallback_layer_ids,
            texture_cache_estimate=texture_cache,
            fallback_tier="wgpu_bitmap",
        )
        return pixmap

    def _partial_readback_rect(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None,
    ) -> QRect | None:
        if self._last_image is None or invalidation is None:
            return None
        if self._last_image.width() != scene.width or self._last_image.height() != scene.height:
            return None
        if (
            invalidation.full_recompose
            or invalidation.mask_dirty_layer_ids
            or not invalidation.gpu_resources.is_empty
        ):
            return None
        dirty_rect = invalidation.dirty_rect_copy()
        if dirty_rect is None or dirty_rect.isEmpty():
            return None
        clipped = dirty_rect.intersected(QRect(0, 0, int(scene.width), int(scene.height)))
        if clipped.isEmpty():
            return None
        return clipped

    def cached_frame(self, *, device_pixel_ratio: float) -> QPixmap | None:
        """Return the previous pixmap when it still matches the widget DPR."""

        if self._last_pixmap is None or self._last_pixmap.isNull():
            return None
        dpr = max(1.0, float(device_pixel_ratio))
        if abs(float(self._last_pixmap.devicePixelRatio()) - dpr) > 0.001:
            return None
        self._last_warnings = ()
        self.diagnostics = replace(
            self.diagnostics,
            render_frame_ms=0.0,
            readback_ms=0.0,
            qimage_conversion_ms=0.0,
            qpixmap_conversion_ms=0.0,
            scene_build_ms=0.0,
            scene_flatten_ms=0.0,
            dirty_rect_size=None,
            full_frame_fallback_count=0,
            layers_seen=0,
            layers_drawn=0,
            full_uploads=0,
            partial_uploads=0,
            partial_upload_fallbacks=0,
            uploaded_layer_ids=(),
            full_upload_layer_ids=(),
            partial_upload_layer_ids=(),
            partial_upload_fallback_layer_ids=(),
        )
        return self._last_pixmap

    def readback_for_export(self) -> QImage | None:
        if self._frame_preview_export_scene is not None:
            from .panel_frame_preview_export import without_frame_preview  # noqa: PLC0415

            scene = replace(
                without_frame_preview(self._frame_preview_export_scene), channel_view=None
            )
            projection = getattr(self, "_native_cmyk_projection", None)
            if projection is not None:
                scene = projection.scene(scene)
            result = self._renderer.render_scene(scene)
            return self._result_to_qimage(result.rgba, result.width, result.height)
        if (
            self._last_document_scene is not None
            and self._last_document_scene.channel_view is not None
        ):
            scene = replace(self._last_document_scene, channel_view=None)
            projection = getattr(self, "_native_cmyk_projection", None)
            if projection is not None:
                scene = projection.scene(scene)
            result = self._renderer.render_scene(scene)
            return self._result_to_qimage(result.rgba, result.width, result.height)
        return self._last_image.copy() if self._last_image is not None else None

    def release(self) -> None:
        projection = getattr(self, "_native_cmyk_projection", None)
        if projection is not None:
            projection.release()
            self._native_cmyk_projection = None
        if self._channels_renderer is not None:
            self._channels_renderer.release()
            self._channels_renderer = None
        self._last_document_scene = None
        release = getattr(self._renderer, "release", None)
        if callable(release):
            release()
        self._last_image = None
        self._last_pixmap = None

    def _adapter_name(self) -> str:
        manager = getattr(self._renderer, "device_manager", None)
        bundle = getattr(manager, "_bundle", None)
        info = getattr(bundle, "adapter_info", {}) if bundle is not None else {}
        if not isinstance(info, dict):
            return ""
        for key in ("device", "adapter", "name", "description"):
            value = info.get(key)
            if value:
                return str(value)
        return ""

    def _texture_cache_estimate(self) -> int:
        compositor = getattr(self._renderer, "_compositor", None)
        manager = getattr(compositor, "texture_manager", None)
        estimate = getattr(manager, "estimated_bytes", None)
        if callable(estimate):
            return int(estimate())
        return 0

    def _result_to_qimage_with_patch(
        self,
        result: Any,
    ) -> tuple[QImage, QImage | None, tuple[int, int, int, int] | None]:
        if result.readback_rect is None:
            image = self._result_to_qimage(result.rgba, result.width, result.height)
            return image, None, None
        x, y, width, height = result.readback_rect
        if self._last_image is None:
            raise WgpuRenderError("partial wgpu readback requires a previous full frame")
        if self._last_image.width() != result.width or self._last_image.height() != result.height:
            raise WgpuRenderError(
                "partial wgpu readback size did not match the previous full frame"
            )
        patch = self._result_to_qimage(result.rgba, width, height)
        image = self._last_image.copy()
        painter = QPainter(image)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(int(x), int(y), patch)
        finally:
            painter.end()
        return image, patch, result.readback_rect

    def _pixmap_from_image_with_patch(
        self,
        image: QImage,
        patch: QImage | None,
        patch_rect: tuple[int, int, int, int] | None,
    ) -> QPixmap:
        if patch is None or patch_rect is None:
            return self._pixmap_from_image(image)
        if (
            self._last_pixmap is None
            or self._last_pixmap.isNull()
            or self._last_pixmap.width() != image.width()
            or self._last_pixmap.height() != image.height()
        ):
            return self._pixmap_from_image(image)
        self._ensure_qpixmap_main_thread()
        x, y, _width, _height = patch_rect
        pixmap = QPixmap(self._last_pixmap)
        pixmap.setDevicePixelRatio(1.0)
        painter = QPainter(pixmap)
        if not painter.isActive():
            return self._pixmap_from_image(image)
        try:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.drawImage(int(x), int(y), patch)
        finally:
            painter.end()
        return pixmap

    @staticmethod
    def _result_to_qimage(rgba: np.ndarray, width: int, height: int) -> QImage:
        data = np.ascontiguousarray(rgba)
        if data.shape != (height, width, 4):
            raise WgpuRenderError(
                f"wgpu result shape {data.shape!r} did not match {(height, width, 4)!r}"
            )
        image = QImage(
            data.data,
            int(width),
            int(height),
            int(width) * 4,
            QImage.Format.Format_RGBA8888,
        )
        return image.copy()

    @staticmethod
    def _pixmap_from_image(image: QImage) -> QPixmap:
        WgpuBitmapPresenter._ensure_qpixmap_main_thread()
        return QPixmap.fromImage(image)

    @staticmethod
    def _ensure_qpixmap_main_thread() -> None:
        app = QApplication.instance()
        if app is not None and app.thread() != QThread.currentThread():
            raise WgpuRenderError("QPixmap.fromImage must run on the Qt main thread")
