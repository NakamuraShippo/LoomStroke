"""Low-resolution wgpu preview renderer for live UI updates (UI-PERF-01).

Live preview / page-thumbnail updates previously flowed through
``WgpuNativeCanvasPresenter.readback_for_export()`` — a full-canvas
offscreen render plus full-resolution readback (~1 s per update on 4K
documents).  This renderer composites the same ``RenderSceneSnapshot``
through the presenter's *shared* compositor into a transient render target
whose longest side never exceeds ``max_extent`` (256 px by default), reads
back at most ``max_extent**2 * 4`` bytes, and destroys the preview target
immediately after readback.

Ownership contract:

- The compositor, device bundle, and texture cache stay owned by the native
  presenter; this class never releases them and never creates its own
  ``WgpuCompositor`` / ``WgpuTextureManager`` (a second cache would double
  4K/8K layer textures).
- Only the transient preview target returned by ``compositor.render`` is
  owned here, and it is destroyed in a ``finally`` block.
- Scenes that would take the compositor's full-frame CPU flatten fallback
  are skipped (``None`` + ``last_skip_reason``): live preview must never
  materialize a full-canvas QImage.  Save/export keeps its own path.
- Serial main-thread use only; no ``QApplication.processEvents()``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot

from .wgpu_backend.readback import WgpuReadback
from .wgpu_backend.types import WgpuRenderTargetHandle, WgpuViewParams

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_MAX_EXTENT = 256


@dataclass(frozen=True, slots=True)
class WgpuPreviewRenderResult:
    """One completed low-resolution preview render + readback."""

    image: QImage
    width: int
    height: int
    render_ms: float
    readback_ms: float
    readback_bytes: int


class WgpuNativePreviewRenderer:
    """Composite a scene into a <=``max_extent`` preview target and read it back."""

    def __init__(self, bundle: Any, compositor: Any) -> None:
        self._bundle = bundle
        self._compositor = compositor
        self._readback = WgpuReadback(bundle)
        self.last_skip_reason: str | None = None

    def matches(self, bundle: Any, compositor: Any) -> bool:
        """True when this renderer still targets the presenter's resources."""

        return self._bundle is bundle and self._compositor is compositor

    @staticmethod
    def preview_size(
        canvas_width: int,
        canvas_height: int,
        max_extent: int = DEFAULT_PREVIEW_MAX_EXTENT,
    ) -> tuple[int, int] | None:
        """Aspect-preserving fit of the canvas into ``max_extent`` (no upscale)."""

        width = int(canvas_width)
        height = int(canvas_height)
        extent = int(max_extent)
        if width <= 0 or height <= 0 or extent <= 0:
            return None
        scale = min(1.0, extent / float(max(width, height)))
        preview_width = max(1, min(extent, round(width * scale)))
        preview_height = max(1, min(extent, round(height * scale)))
        return preview_width, preview_height

    def render_preview(
        self,
        scene: RenderSceneSnapshot,
        *,
        max_extent: int = DEFAULT_PREVIEW_MAX_EXTENT,
    ) -> WgpuPreviewRenderResult | None:
        """Render ``scene`` into a preview target; ``None`` keeps the last preview.

        Returning ``None`` (with ``last_skip_reason`` set) is a deliberate
        "keep the current preview" signal — callers must not fall back to a
        full-resolution readback.
        """

        self.last_skip_reason = None
        size = self.preview_size(scene.width, scene.height, max_extent)
        if size is None:
            self.last_skip_reason = "invalid_canvas_size"
            return None
        requires_flatten = getattr(self._compositor, "requires_bitmap_flatten", None)
        if callable(requires_flatten) and requires_flatten(scene):
            # The compositor would flatten the whole canvas on the CPU for
            # this scene; that cost is reserved for save/export.
            self.last_skip_reason = "bitmap_flatten_required"
            return None
        preview_width, preview_height = size
        # Map canvas pixels straight onto the preview target — no
        # canvas-resolution intermediate target exists on this path.
        view = WgpuViewParams(
            affine=(
                preview_width / float(scene.width),
                0.0,
                0.0,
                preview_height / float(scene.height),
                0.0,
                0.0,
            ),
            width=preview_width,
            height=preview_height,
        )
        if view.inverse() is None:
            self.last_skip_reason = "degenerate_view"
            return None

        previous_3d_scale = float(getattr(self._compositor, "three_d_display_scale", 1.0))
        set_3d_scale = getattr(self._compositor, "set_three_d_display_scale", None)
        target: WgpuRenderTargetHandle | None = None
        render_start = time.perf_counter()
        try:
            if callable(set_3d_scale):
                set_3d_scale(1.0)
            target, _warnings, _metrics = self._compositor.render(
                scene,
                invalidation=RenderInvalidation(full_recompose=True),
                view=view,
            )
            render_ms = (time.perf_counter() - render_start) * 1000.0
            readback_start = time.perf_counter()
            rgba = self._readback.read_rgba(target)
            readback_ms = (time.perf_counter() - readback_start) * 1000.0
            result_width = int(target.width)
            result_height = int(target.height)
        finally:
            if callable(set_3d_scale):
                set_3d_scale(previous_3d_scale)
            if target is not None:
                self._destroy_target(target)

        data = np.ascontiguousarray(rgba)
        image = QImage(
            data.data,
            result_width,
            result_height,
            result_width * 4,
            QImage.Format.Format_RGBA8888,
        ).copy()
        return WgpuPreviewRenderResult(
            image=image,
            width=result_width,
            height=result_height,
            render_ms=render_ms,
            readback_ms=readback_ms,
            readback_bytes=int(data.nbytes),
        )

    @staticmethod
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()


__all__ = (
    "DEFAULT_PREVIEW_MAX_EXTENT",
    "WgpuNativePreviewRenderer",
    "WgpuPreviewRenderResult",
)
