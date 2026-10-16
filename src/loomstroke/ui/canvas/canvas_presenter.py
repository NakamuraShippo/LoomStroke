"""Presenter boundary between CanvasWidget and render backends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PySide6.QtGui import QImage, QPixmap

from loomstroke.core.canvas_invalidation import RenderInvalidation

from .render_scene import RenderSceneSnapshot


@dataclass(slots=True)
class CanvasPresenterDiagnostics:
    """Small frame diagnostics shared by optional presenter implementations."""

    backend_name: str
    adapter_name: str = ""
    render_frame_ms: float = 0.0
    readback_ms: float = 0.0
    qimage_conversion_ms: float = 0.0
    qpixmap_conversion_ms: float = 0.0
    scene_build_ms: float = 0.0
    scene_flatten_ms: float = 0.0
    overlay_paint_ms: float = 0.0
    present_ms: float = 0.0
    upload_ms: float = 0.0
    export_readback_ms: float = 0.0
    dirty_rect_size: tuple[int, int] | None = None
    damage_rect_size: tuple[int, int] | None = None
    surface_status: str = ""
    surface_size: tuple[int, int] | None = None
    surface_dpr: float = 1.0
    surface_recreate_count: int = 0
    surface_lost_count: int = 0
    fallback_tier: str = ""
    fallback_reason: str | None = None
    fallback_to_bitmap_count: int = 0
    emergency_gl_count: int = 0
    full_frame_fallback_count: int = 0
    layers_seen: int = 0
    layers_drawn: int = 0
    full_uploads: int = 0
    partial_uploads: int = 0
    partial_upload_fallbacks: int = 0
    uploaded_layer_ids: tuple[str, ...] = ()
    full_upload_layer_ids: tuple[str, ...] = ()
    partial_upload_layer_ids: tuple[str, ...] = ()
    partial_upload_fallback_layer_ids: tuple[str, ...] = ()
    device_lost_count: int = 0
    fallback_to_gl_count: int = 0
    texture_cache_estimate: int = 0
    present_p95_ms: float = 0.0
    last_present_serial: int = 0
    last_scene_signature: str = ""
    last_visual_probe_status: str = ""
    last_visual_probe_reason: str | None = None
    live_surface_capture_supported: bool = False


class CanvasPresenter(Protocol):
    """Render a snapshot into a widget-ready frame."""

    backend_name: str
    diagnostics: CanvasPresenterDiagnostics

    def render_frame(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None,
        *,
        device_pixel_ratio: float,
    ) -> QPixmap | None:
        """Render one frame and return a pixmap ready for Qt painting."""
        ...

    def readback_for_export(self) -> QImage | None:
        """Return the last composited image for save/export paths."""
        ...

    def release(self) -> None:
        """Release backend-owned resources."""
        ...
