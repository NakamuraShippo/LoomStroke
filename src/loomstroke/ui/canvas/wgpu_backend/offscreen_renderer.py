"""Headless wgpu renderer for ``RenderSceneSnapshot`` inputs."""

from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import QRect

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.gpu.runtime import GpuRuntime
from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot

from .compositor import WgpuCompositor
from .device_manager import WgpuDeviceManager
from .readback import WgpuReadback
from .types import WgpuRenderResult, WgpuRenderTargetHandle


class WgpuOffscreenRenderer:
    """Render simple raster scenes offscreen with wgpu."""

    backend_name = "wgpu-offscreen"

    def __init__(
        self,
        device_manager: WgpuDeviceManager | None = None,
        *,
        runtime: GpuRuntime | None = None,
    ) -> None:
        self.device_manager = device_manager or WgpuDeviceManager(runtime=runtime)
        self._bundle = self.device_manager.get_bundle()
        self._compositor = WgpuCompositor(self._bundle)
        self._readback = WgpuReadback(self._bundle)

    def render_scene(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None = None,
        *,
        readback_rect: QRect | None = None,
        three_d_display_scale: float = 1.0,
    ) -> WgpuRenderResult:
        render_start = time.perf_counter()
        from loomstroke.ui.canvas.native_cmyk_projection import (  # noqa: PLC0415
            NativeCmykProjection,
        )

        scene = NativeCmykProjection.prepare(self, scene)
        previous_three_d_display_scale = self._compositor.three_d_display_scale
        self._compositor.set_three_d_display_scale(three_d_display_scale)
        try:
            target, warnings, metrics = self._compositor.render(
                scene,
                invalidation=invalidation,
            )
        finally:
            self._compositor.set_three_d_display_scale(previous_three_d_display_scale)
        render_frame_ms = (time.perf_counter() - render_start) * 1000.0
        readback_start = time.perf_counter()
        readback_rect_tuple: tuple[int, int, int, int] | None = None
        try:
            if readback_rect is not None:
                partial = self._readback.read_rgba_region(target, readback_rect)
            else:
                partial = None
            if partial is not None:
                rgba, clipped = partial
                readback_rect_tuple = (
                    int(clipped.x()),
                    int(clipped.y()),
                    int(clipped.width()),
                    int(clipped.height()),
                )
            else:
                rgba = self._readback.read_rgba(target)
        finally:
            self._destroy_target(target)
        readback_ms = (time.perf_counter() - readback_start) * 1000.0
        metrics = replace(
            metrics,
            render_frame_ms=render_frame_ms,
            readback_ms=readback_ms,
        )
        return WgpuRenderResult(
            width=target.width,
            height=target.height,
            rgba=rgba,
            backend_name=self.backend_name,
            warnings=warnings,
            metrics=metrics,
            readback_rect=readback_rect_tuple,
        )

    def release(self) -> None:
        projection = getattr(self, "_native_cmyk_projection", None)
        if projection is not None:
            projection.release()
            self._native_cmyk_projection = None
        self._compositor.release()
        self.device_manager.release()

    @staticmethod
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()
