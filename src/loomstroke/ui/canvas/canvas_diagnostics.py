"""Diagnostics dictionary builders for canvas presenters."""

from __future__ import annotations

from typing import Any


def build_canvas_widget_render_diagnostics(
    widget: Any,
    *,
    selected_backend: str,
    native_requested: bool,
) -> dict[str, Any]:
    """Return render backend diagnostics for ``CanvasWidget``."""
    diagnostics = widget._canvas_presenter.diagnostics
    if widget._wgpu_bitmap_presenter is not None and widget._wgpu_bitmap_disabled_reason is None:
        diagnostics = widget._wgpu_bitmap_presenter.diagnostics
    get_sso_diagnostics = getattr(widget._overlay_manager, "get_diagnostics", None)
    sso_diagnostics = get_sso_diagnostics() if callable(get_sso_diagnostics) else {}
    fallback_tier = diagnostics.fallback_tier
    if not fallback_tier:
        fallback_tier = (
            "wgpu_bitmap"
            if widget._wgpu_bitmap_requested and widget._wgpu_bitmap_disabled_reason is None
            else "wgpu_unavailable"
        )

    return {
        "backend_name": diagnostics.backend_name,
        "adapter_name": diagnostics.adapter_name,
        "render_frame_ms": diagnostics.render_frame_ms,
        "readback_ms": diagnostics.readback_ms,
        "qimage_conversion_ms": diagnostics.qimage_conversion_ms,
        "qpixmap_conversion_ms": diagnostics.qpixmap_conversion_ms,
        "scene_build_ms": diagnostics.scene_build_ms,
        "scene_flatten_ms": diagnostics.scene_flatten_ms,
        "overlay_paint_ms": diagnostics.overlay_paint_ms,
        "present_ms": diagnostics.present_ms,
        "upload_ms": diagnostics.upload_ms,
        "export_readback_ms": diagnostics.export_readback_ms,
        "dirty_rect_size": diagnostics.dirty_rect_size,
        "damage_rect_size": diagnostics.damage_rect_size,
        "surface_status": diagnostics.surface_status,
        "surface_size": diagnostics.surface_size,
        "surface_dpr": diagnostics.surface_dpr,
        "surface_recreate_count": diagnostics.surface_recreate_count,
        "surface_lost_count": diagnostics.surface_lost_count,
        "fallback_tier": fallback_tier,
        "fallback_reason": diagnostics.fallback_reason or widget._wgpu_bitmap_disabled_reason,
        "fallback_to_bitmap_count": diagnostics.fallback_to_bitmap_count,
        "emergency_gl_count": 0,
        "full_frame_fallback_count": diagnostics.full_frame_fallback_count,
        "layers_seen": diagnostics.layers_seen,
        "layers_drawn": diagnostics.layers_drawn,
        "full_uploads": diagnostics.full_uploads,
        "partial_uploads": diagnostics.partial_uploads,
        "partial_upload_fallbacks": diagnostics.partial_upload_fallbacks,
        "uploaded_layer_ids": diagnostics.uploaded_layer_ids,
        "full_upload_layer_ids": diagnostics.full_upload_layer_ids,
        "partial_upload_layer_ids": diagnostics.partial_upload_layer_ids,
        "partial_upload_fallback_layer_ids": diagnostics.partial_upload_fallback_layer_ids,
        "device_lost_count": max(
            diagnostics.device_lost_count,
            widget._wgpu_bitmap_device_lost_count,
        ),
        "fallback_to_gl_count": 0,
        "texture_cache_estimate": diagnostics.texture_cache_estimate,
        "present_p95_ms": diagnostics.present_p95_ms,
        "last_present_serial": diagnostics.last_present_serial,
        "last_scene_signature": diagnostics.last_scene_signature,
        "last_visual_probe_status": diagnostics.last_visual_probe_status,
        "last_visual_probe_reason": diagnostics.last_visual_probe_reason,
        "live_surface_capture_supported": diagnostics.live_surface_capture_supported,
        "sso_eligible_layer_count": sso_diagnostics.get("eligible_layer_count", 0),
        "sso_eligible_render_count": sso_diagnostics.get("eligible_render_count", 0),
        "sso_eligible_reuse_count": sso_diagnostics.get("eligible_reuse_count", 0),
        "sso_eligible_pan_reuse_count": sso_diagnostics.get("eligible_pan_reuse_count", 0),
        "sso_preview_render_count": sso_diagnostics.get("preview_render_count", 0),
        "sso_eligible_p95_ms": sso_diagnostics.get("eligible_p95_ms", 0.0),
        "sso_preview_p95_ms": sso_diagnostics.get("preview_p95_ms", 0.0),
        "selected_backend": selected_backend,
        "wgpu_native_requested": native_requested,
        "wgpu_native_disabled_reason": None,
        "wgpu_bitmap_requested": widget._wgpu_bitmap_requested,
        "wgpu_bitmap_disabled_reason": widget._wgpu_bitmap_disabled_reason,
    }
