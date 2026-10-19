"""Coordinator for CanvasWidget's wgpu bitmap compatibility path."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtGui import QPixmap

from .render_scene import RenderSceneSnapshot, compute_render_scene_signature

logger = logging.getLogger(__name__)


class CanvasWidgetWgpuBitmapCoordinator:
    """Own presenter lifecycle and frame reuse for the bitmap wgpu path."""

    def __init__(
        self,
        widget: Any,
        *,
        presenter_factory: Callable[[], Any],
        unavailable_error_type: type[BaseException],
        backend_error_type: type[BaseException],
        max_readback_ms: float,
        slow_frame_limit: int,
        auto_disable_on_slow_readback: bool,
    ) -> None:
        self._widget = widget
        self._presenter_factory = presenter_factory
        self._unavailable_error_type = unavailable_error_type
        self._backend_error_type = backend_error_type
        self._max_readback_ms = max_readback_ms
        self._slow_frame_limit = slow_frame_limit
        self._auto_disable_on_slow_readback = auto_disable_on_slow_readback

    def ensure_presenter(self) -> Any | None:
        if not self._widget._wgpu_bitmap_requested or self._widget._wgpu_bitmap_disabled_reason:
            return None
        if self._widget._wgpu_bitmap_presenter is not None:
            return self._widget._wgpu_bitmap_presenter
        try:
            self._widget._wgpu_bitmap_presenter = self._presenter_factory()
            enable_async = getattr(
                self._widget._wgpu_bitmap_presenter, "enable_async_display", None
            )
            if callable(enable_async):
                enable_async(self._request_prepared_frame)
        except self._unavailable_error_type as exc:
            self.disable(f"wgpu unavailable: {exc}")
            return None
        except self._backend_error_type as exc:
            self.disable(f"wgpu init failed: {exc}")
            return None
        logger.info("wgpu bitmap presenter initialized")
        return self._widget._wgpu_bitmap_presenter

    def _request_prepared_frame(self) -> None:
        self._widget._last_wgpu_bitmap_scene_signature = None
        self._widget.update()

    def disable(self, reason: str, canvas: Any | None = None) -> None:
        self._widget._wgpu_bitmap_disabled_reason = reason
        self._widget._wgpu_bitmap_fallback_count += 1
        logger.warning("Disabling wgpu bitmap presenter: %s", reason)
        self.release_presenter()
        if canvas is not None:
            invalidate = getattr(canvas, "invalidate_gpu_caches", None)
            if callable(invalidate):
                invalidate()

    def release_presenter(self) -> None:
        if self._widget._wgpu_bitmap_presenter is None:
            self._widget._last_wgpu_bitmap_scene_signature = None
            return
        try:
            self._widget._wgpu_bitmap_presenter.release()
        except Exception:
            logger.debug("Failed to release wgpu bitmap presenter", exc_info=True)
        self._widget._wgpu_bitmap_presenter = None
        self._widget._last_wgpu_bitmap_scene_signature = None

    def render_or_reuse_frame(
        self,
        presenter: Any,
        scene: RenderSceneSnapshot,
        invalidation: Any,
    ) -> tuple[QPixmap | None, bool]:
        dpr = self._widget.devicePixelRatioF()
        scene_signature = self.scene_signature(scene)
        if (
            not invalidation.has_any
            and self._widget._last_wgpu_bitmap_scene_signature == scene_signature
        ):
            cached_frame = getattr(presenter, "cached_frame", None)
            if callable(cached_frame):
                pixmap = cached_frame(device_pixel_ratio=dpr)
                if pixmap is not None:
                    return pixmap, False
        pixmap = presenter.render_frame(
            scene,
            invalidation,
            device_pixel_ratio=dpr,
        )
        if pixmap is not None and not pixmap.isNull():
            self._widget._last_wgpu_bitmap_scene_signature = scene_signature
        return pixmap, True

    @staticmethod
    def scene_signature(scene: RenderSceneSnapshot) -> tuple[Any, ...]:
        return compute_render_scene_signature(scene)

    def maybe_disable_for_slow_readback(self, presenter: Any, canvas: Any) -> bool:
        readback_ms = presenter.diagnostics.readback_ms
        if readback_ms <= self._max_readback_ms:
            self._widget._wgpu_bitmap_slow_readback_count = 0
            return False
        self._widget._wgpu_bitmap_slow_readback_count += 1
        logger.warning(
            "wgpu bitmap readback took %.1f ms (%d/%d); continuing on wgpu bitmap",
            readback_ms,
            self._widget._wgpu_bitmap_slow_readback_count,
            self._slow_frame_limit,
        )
        if not self._auto_disable_on_slow_readback:
            return False
        if self._widget._wgpu_bitmap_slow_readback_count < self._slow_frame_limit:
            return False
        self.disable(
            (
                "wgpu bitmap readback too slow: "
                f"{readback_ms:.1f} ms > {self._max_readback_ms:.1f} ms"
            ),
            canvas,
        )
        return True

    def invalidate_resources(self, reason: str) -> None:
        if not self._widget._wgpu_bitmap_requested:
            return
        if self._widget._wgpu_bitmap_presenter is not None:
            logger.debug("Invalidating wgpu bitmap presenter resources: %s", reason)
            self.release_presenter()
        canvas = self._widget._get_canvas_service()
        invalidate = getattr(canvas, "invalidate_gpu_caches", None)
        if callable(invalidate):
            invalidate()

    def pixmap_for_export(self) -> QPixmap | None:
        if (
            not self._widget._wgpu_bitmap_requested
            or self._widget._wgpu_bitmap_disabled_reason is not None
            or self._widget._wgpu_bitmap_presenter is None
        ):
            return None
        image = self._widget._wgpu_bitmap_presenter.readback_for_export()
        if image is None or image.isNull():
            return None
        return QPixmap.fromImage(image)
