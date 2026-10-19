"""Native-surface presenter for the optional wgpu canvas path."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from math import hypot
from typing import Any, cast

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.gpu.runtime import GpuRuntime

from .canvas_presenter import CanvasPresenterDiagnostics
from .channels_surface_view import ChannelsSurfaceView
from .native_cmyk_projection import NativeCmykProjection
from .render_scene import (
    RenderSceneSnapshot,
    compute_render_scene_signature,
    view_resolution_compositing_enabled,
)
from .spread_companion_pass import SpreadCompanionFrame, SpreadCompanionPass
from .wgpu_backend import WgpuDeviceManager, WgpuOffscreenRenderer
from .wgpu_backend.compositor import WgpuCompositor
from .wgpu_backend.projection_cache import WgpuProjectionCache
from .wgpu_backend.types import (
    WgpuBackendError,
    WgpuDeviceBundle,
    WgpuRenderError,
    WgpuRenderMetrics,
    WgpuRenderTargetHandle,
    WgpuUnavailableError,
    WgpuViewParams,
)
from .wgpu_backend.vector.renderer import lod_bucket_for_scale
from .wgpu_native_preview_renderer import (
    DEFAULT_PREVIEW_MAX_EXTENT,
    WgpuNativePreviewRenderer,
)

SURFACE_BLIT_WGSL = """
struct VertexIn {
    @location(0) position: vec2<f32>,
    @location(1) uv: vec2<f32>,
};

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var canvas_tex: texture_2d<f32>;

/*TARGET_COLOR_TRANSFORM*/

/*PROOF_TRANSFORM*/

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    out.position = vec4<f32>(in.position, 0.0, 1.0);
    out.uv = in.uv;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(canvas_tex, tex_sampler, in.uv);
    let dims = textureDimensions(canvas_tex, 0);
    let canvas_px = in.uv * vec2<f32>(f32(dims.x), f32(dims.y));
    let cell_x = u32(floor(canvas_px.x / 8.0));
    let cell_y = u32(floor(canvas_px.y / 8.0));
    let checker_dark = vec3<f32>(0.8, 0.8, 0.8);
    let checker_light = vec3<f32>(1.0, 1.0, 1.0);
    let checker = select(
        checker_light,
        checker_dark,
        ((cell_x + cell_y) % 2u) == 0u,
    );
    let alpha = clamp(src.a, 0.0, 1.0);
    let rgb_srgb = proof_rgb(src.rgb + checker * (1.0 - alpha));
    return vec4<f32>(surface_rgb_to_target(rgb_srgb), 1.0);
}
"""

# Soft proof final pass (CT-11 P2, spec 11 §8.2): the CPU-built proof
# LUT is applied to the display-referred sRGB color, after the checker
# flatten and before the target encode.  Display only — export and
# preview readbacks never run these shaders.
SURFACE_PROOF_DISABLED_WGSL = """
fn proof_rgb(rgb_srgb: vec3<f32>) -> vec3<f32> {
    return rgb_srgb;
}
"""

SURFACE_PROOF_ENABLED_WGSL = """
@group(0) @binding(2) var proof_sampler: sampler;
@group(0) @binding(3) var proof_lut: texture_3d<f32>;

fn proof_rgb(rgb_srgb: vec3<f32>) -> vec3<f32> {
    let n = vec3<f32>(textureDimensions(proof_lut));
    let unit = clamp(rgb_srgb, vec3<f32>(0.0), vec3<f32>(1.0));
    // Half-texel offset so grid points sample their exact table entry.
    let coord = unit * (n - 1.0) / n + 0.5 / n;
    return textureSampleLevel(proof_lut, proof_sampler, coord, 0.0).rgb;
}
"""

SURFACE_BLIT_LINEAR_TARGET_WGSL = """
fn surface_rgb_to_target(rgb_srgb: vec3<f32>) -> vec3<f32> {
    return rgb_srgb;
}
"""

SURFACE_BLIT_SRGB_TARGET_WGSL = """
fn srgb_channel_to_linear(c: f32) -> f32 {
    let v = clamp(c, 0.0, 1.0);
    if (v <= 0.04045) {
        return v / 12.92;
    }
    return pow((v + 0.055) / 1.055, 2.4);
}

fn surface_rgb_to_target(rgb_srgb: vec3<f32>) -> vec3<f32> {
    return vec3<f32>(
        srgb_channel_to_linear(rgb_srgb.r),
        srgb_channel_to_linear(rgb_srgb.g),
        srgb_channel_to_linear(rgb_srgb.b),
    );
}
"""

SURFACE_VECTOR_OVERLAY_WGSL = """
struct VertexIn {
    @location(0) position: vec2<f32>,
    @location(1) uv: vec2<f32>,
};

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var canvas_tex: texture_2d<f32>;

/*TARGET_COLOR_TRANSFORM*/

/*PROOF_TRANSFORM*/

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    out.position = vec4<f32>(in.position, 0.0, 1.0);
    out.uv = in.uv;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    // Premultiplied overlay drawn over the (opaque) blitted frame.
    let src = textureSample(canvas_tex, tex_sampler, in.uv);
    // The proof LUT is defined on straight colors: unpremultiply, proof,
    // re-premultiply (identity when proofing is off).
    var rgb = src.rgb;
    if (src.a > 0.001) {
        rgb = proof_rgb(min(rgb / src.a, vec3<f32>(1.0))) * src.a;
    }
    return vec4<f32>(surface_rgb_to_target(rgb), src.a);
}
"""

NATIVE_SMOKE_CLEAR_RGBA = (0.11, 0.13, 0.16, 1.0)

logger = logging.getLogger(__name__)


class WgpuNativeCanvasPresenter:
    """Present a wgpu canvas frame directly to a native Qt widget surface.

    Display rendering never reads pixels back to the CPU. The presenter
    keeps the existing canvas-space compositor target, then blits that
    texture to the swapchain with the current viewport transform.
    """

    backend_name = "wgpu-native"
    _supports_native_display_tiles = True

    def __init__(
        self,
        *,
        window_id_provider: Callable[[], int],
        surface_size_provider: Callable[[], tuple[int, int]],
        viewport_provider: Callable[[], Any],
        runtime: GpuRuntime | None = None,
        device_manager: WgpuDeviceManager | None = None,
        power_preference: str = "high-performance",
    ) -> None:
        if sys.platform != "win32":
            raise WgpuBackendError("wgpu native surface is currently enabled only on Windows")
        self._window_id_provider = window_id_provider
        self._surface_size_provider = surface_size_provider
        self._viewport_provider = viewport_provider
        self._power_preference = power_preference
        self._runtime = runtime or GpuRuntime()
        self.device_manager = device_manager
        self._bundle: WgpuDeviceBundle | None = None
        self._wgpu = WgpuDeviceManager.import_wgpu()
        self._device: Any | None = None
        self._queue: Any | None = None
        self._compositor: WgpuCompositor | None = None
        self._projection_cache: WgpuProjectionCache | None = None
        self._export_renderer: WgpuOffscreenRenderer | None = None
        self._preview_renderer: WgpuNativePreviewRenderer | None = None
        self._preview_diagnostics: dict[str, Any] = {}
        self._canvas_context: Any | None = None
        self._surface_format: Any | None = None
        self._surface_key: tuple[int, int, int] | None = None
        self._surface_recreate_count = 0
        self._surface_lost_count = 0
        self._last_scene: RenderSceneSnapshot | None = None
        self._last_scene_signature: tuple[Any, ...] | None = None
        self._last_surface_blit_signature: tuple[Any, ...] | None = None
        self._present_serial = 0
        self._present_durations_ms: list[float] = []
        self._last_warnings: tuple[str, ...] = ()
        self._has_presented_frame = False
        self._pipeline_cache: dict[str, Any] = {}
        self._bind_group_layout: Any | None = None
        self._sampler: Any | None = None
        self._proof_lut: Any | None = None
        self._proof_texture: Any | None = None
        self._proof_texture_key: str | None = None
        self._proof_sampler: Any | None = None
        self._proof_bind_group_layout: Any | None = None
        self._fullscreen_quad_buffer: Any | None = None
        self._screen_space_pass_disabled = False
        self._last_view_log_state: tuple[Any, ...] | None = None
        self._companion_pass: SpreadCompanionPass | None = None
        self.diagnostics = CanvasPresenterDiagnostics(
            backend_name=self.backend_name,
            adapter_name=self._adapter_name(),
            surface_status="unconfigured",
            fallback_tier="wgpu_native",
        )
        if self.device_manager is not None:
            self._adopt_bundle(self.device_manager.get_bundle())

    @property
    def last_warnings(self) -> tuple[str, ...]:
        return self._last_warnings

    @property
    def has_presented_frame(self) -> bool:
        return self._has_presented_frame

    def present_frame(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None,
        *,
        device_pixel_ratio: float,
        companion: SpreadCompanionFrame | None = None,
    ) -> bool:
        """Render and present one display frame without CPU readback.

        ``companion`` is the spread's facing page (CT-06 B4-c4).  It is
        composited to its own target and blitted in the same render pass as
        the active page; ``None`` keeps the single-page path unchanged.
        """

        self._last_scene = scene
        scene = NativeCmykProjection.prepare(self, scene)
        scene, self._channel_view_index = ChannelsSurfaceView.prepare(self, scene)
        scene_signature = compute_render_scene_signature(scene)
        invalidation = NativeCmykProjection.display_invalidation(
            scene, self._last_scene_signature, scene_signature, invalidation
        )
        surface_blit_signature = self._surface_blit_signature(
            scene,
            device_pixel_ratio=device_pixel_ratio,
            companion=companion,
        )
        dirty_rect = invalidation.dirty_rect_copy() if invalidation is not None else None
        damage_rect_size = (
            (dirty_rect.width(), dirty_rect.height()) if dirty_rect is not None else None
        )
        can_reuse_present = (
            self._has_presented_frame
            and not getattr(getattr(self, "_projection_cache", None), "pending", False)
            and invalidation is not None
            and not invalidation.has_any
            and self._last_scene_signature == scene_signature
            and self._last_surface_blit_signature == surface_blit_signature
        )
        if can_reuse_present:
            self.diagnostics = replace(
                self.diagnostics,
                render_frame_ms=0.0,
                readback_ms=0.0,
                scene_build_ms=scene.scene_build_ms,
                scene_flatten_ms=scene.scene_flatten_ms,
                present_ms=0.0,
                dirty_rect_size=None,
                damage_rect_size=None,
                full_frame_fallback_count=0,
                layers_seen=len(scene.flat_layers),
                layers_drawn=0,
                full_uploads=0,
                partial_uploads=0,
                partial_upload_fallbacks=0,
                uploaded_layer_ids=(),
                full_upload_layer_ids=(),
                partial_upload_layer_ids=(),
                partial_upload_fallback_layer_ids=(),
                surface_status="ready",
                last_present_serial=self._present_serial,
                last_scene_signature=self._format_scene_signature(scene_signature),
            )
            self._last_warnings = ()
            return True

        render_start = time.perf_counter()
        target: WgpuRenderTargetHandle | None = None
        destroy_target_after_present = False
        # View-resolution compositing: the whole composite runs at surface
        # resolution, so every direct vector layer is already sharp and the
        # topmost-only screen-space pass becomes redundant.
        view_params = self._view_params(device_pixel_ratio=device_pixel_ratio)
        self._log_view_state(scene, view_params)
        # The display-resolution vector pass renders BEFORE the projection so
        # that a failed overlay keeps the layer in the normal composite (spec
        # §19: renderer failures fall back, never crash and never hide the
        # layer).  A failure sticky-disables the pass for this presenter.
        screen_space_layer = (
            self._screen_space_vector_layer(scene)
            if view_params is None and not getattr(self, "_screen_space_pass_disabled", False)
            else None
        )
        overlay_texture: WgpuRenderTargetHandle | None = None
        if screen_space_layer is not None:
            try:
                overlay_texture = self._render_screen_space_overlay(
                    screen_space_layer,
                    device_pixel_ratio=device_pixel_ratio,
                )
            except Exception:
                logger.exception(
                    "screen-space vector overlay failed; disabling for this session "
                    "(layer %s falls back to the composite path)",
                    getattr(screen_space_layer, "id", "<unknown>"),
                )
                self._screen_space_pass_disabled = True
                overlay_texture = None
        try:
            target, warnings, metrics, destroy_target_after_present = self._render_projection(
                scene,
                invalidation,
                device_pixel_ratio=device_pixel_ratio,
                screen_space_vector_layer_id=(
                    str(screen_space_layer.id)
                    if screen_space_layer is not None and overlay_texture is not None
                    else None
                ),
                view=view_params,
            )
            companion_target = self._companion_target(companion, view=view_params)
            render_ms = (time.perf_counter() - render_start) * 1000.0
            surface_recreate_count = self._surface_recreate_count
            surface_target = self._current_surface_target()
            record_present_timing = self._surface_recreate_count == surface_recreate_count
            present_start = time.perf_counter()
            self._present_target(
                target,
                scene,
                surface_target=surface_target,
                device_pixel_ratio=device_pixel_ratio,
                **self._optional_present_kwargs(
                    overlay_texture=overlay_texture,
                    view_params=view_params,
                    companion=companion,
                    companion_target=companion_target,
                ),
            )
            present_ms = (time.perf_counter() - present_start) * 1000.0
        finally:
            if target is not None and destroy_target_after_present:
                self._destroy_target(target)

        self._has_presented_frame = True
        self._last_scene_signature = scene_signature
        self._last_surface_blit_signature = surface_blit_signature
        self._present_serial += 1
        if record_present_timing:
            self._record_present_ms(present_ms)
        self._last_warnings = warnings
        surface_size = self._surface_size_provider()
        self.diagnostics = replace(
            self.diagnostics,
            render_frame_ms=render_ms,
            readback_ms=0.0,
            scene_build_ms=scene.scene_build_ms,
            scene_flatten_ms=scene.scene_flatten_ms,
            present_ms=present_ms,
            present_p95_ms=self._present_p95_ms(),
            dirty_rect_size=damage_rect_size,
            damage_rect_size=damage_rect_size,
            full_frame_fallback_count=metrics.full_frame_fallbacks,
            layers_seen=metrics.layers_seen,
            layers_drawn=metrics.layers_drawn,
            full_uploads=metrics.full_uploads,
            partial_uploads=metrics.partial_uploads,
            partial_upload_fallbacks=metrics.partial_upload_fallbacks,
            uploaded_layer_ids=metrics.uploaded_layer_ids,
            full_upload_layer_ids=metrics.full_upload_layer_ids,
            partial_upload_layer_ids=metrics.partial_upload_layer_ids,
            partial_upload_fallback_layer_ids=metrics.partial_upload_fallback_layer_ids,
            texture_cache_estimate=self._texture_cache_estimate(),
            surface_status="ready",
            surface_size=(max(1, int(surface_size[0])), max(1, int(surface_size[1]))),
            surface_dpr=max(1.0, float(device_pixel_ratio)),
            surface_recreate_count=self._surface_recreate_count,
            surface_lost_count=self._surface_lost_count,
            fallback_tier="wgpu_native",
            last_present_serial=self._present_serial,
            last_scene_signature=self._format_scene_signature(scene_signature),
        )
        return True

    def present_smoke_frame(self) -> int:
        """Present a deterministic clear frame used by the live-surface oracle."""

        present_start = time.perf_counter()
        surface_target = self._current_surface_target()
        device = self._device
        queue = self._queue
        if device is None or queue is None:
            raise WgpuRenderError("wgpu native device is not initialized")
        encoder = device.create_command_encoder()
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": surface_target.view,
                    "resolve_target": None,
                    "clear_value": NATIVE_SMOKE_CLEAR_RGBA,
                    "load_op": self._wgpu.LoadOp.clear,
                    "store_op": self._wgpu.StoreOp.store,
                }
            ]
        )
        render_pass.end()
        queue.submit([encoder.finish()])
        try:
            cast(Any, self._canvas_context).present()
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            self._surface_lost_count += 1
            self._has_presented_frame = False
            self._unconfigure_surface()
            raise WgpuRenderError(f"wgpu native smoke present failed: {exc}") from exc
        present_ms = (time.perf_counter() - present_start) * 1000.0
        self._present_serial += 1
        width, height = self._surface_size_provider()
        self.diagnostics = replace(
            self.diagnostics,
            present_ms=present_ms,
            surface_status="ready",
            surface_size=(max(1, int(width)), max(1, int(height))),
            surface_recreate_count=self._surface_recreate_count,
            surface_lost_count=self._surface_lost_count,
            fallback_tier="wgpu_native",
            last_present_serial=self._present_serial,
        )
        return self._present_serial

    def record_visual_probe(
        self,
        *,
        status: str,
        reason: str | None,
        capture_supported: bool,
    ) -> None:
        self.diagnostics = replace(
            self.diagnostics,
            last_visual_probe_status=status,
            last_visual_probe_reason=reason,
            live_surface_capture_supported=capture_supported,
        )

    def force_next_surface_present(self) -> None:
        """Bypass same-scene reuse for one present.

        Qt overlay widgets can move over the native child HWND without
        changing the canvas scene.  Re-blitting the unchanged scene clears
        stale pixels left under the old widget rectangle while keeping layer
        texture uploads version-based.
        """

        self._last_surface_blit_signature = None

    def set_soft_proof_lut(self, lut: Any | None) -> None:
        """Enable/disable the soft-proof final pass (CT-11 P2).

        ``lut`` is a ``ProofLut`` (``io.soft_proof_lut``) or ``None``.
        Display only: export/preview readbacks bypass the surface blit
        shaders and are never proofed.  The 3D texture uploads lazily on
        the next present; a failed upload disables proofing loudly
        instead of presenting unproofed pixels as a proof (spec 11 §14).
        """

        self._proof_lut = lut
        wanted_key = None if lut is None else str(lut.key)
        if self._proof_texture_key != wanted_key:
            self._proof_texture = None
            self._proof_texture_key = None
        self.force_next_surface_present()

    @property
    def soft_proof_lut_key(self) -> str | None:
        lut = getattr(self, "_proof_lut", None)
        return None if lut is None else str(lut.key)

    def _proof_active(self) -> bool:
        return getattr(self, "_proof_lut", None) is not None

    def readback_for_preview(self, max_extent: int = DEFAULT_PREVIEW_MAX_EXTENT) -> QImage | None:
        """Low-resolution live preview readback (UI-PERF-01).

        Renders the last scene through the shared compositor into a
        transient <=``max_extent`` target.  Save/export never uses this;
        live preview never uses ``readback_for_export``.  ``None`` means
        "keep the last preview" — callers must not fall back to a
        full-resolution readback.
        """

        scene = self._last_scene
        if scene is None or self._compositor is None or self._bundle is None:
            return None
        renderer = self._preview_renderer
        if renderer is None or not renderer.matches(self._bundle, self._compositor):
            renderer = WgpuNativePreviewRenderer(self._bundle, self._compositor)
            self._preview_renderer = renderer
        try:
            result = renderer.render_preview(scene, max_extent=max_extent)
        except Exception:
            logger.exception("native preview readback failed; keeping last preview")
            return None
        diagnostics = self._preview_diagnostics
        if result is None:
            diagnostics["native_preview_skip_reason"] = renderer.last_skip_reason
            logger.debug("native preview skipped: %s", renderer.last_skip_reason)
            return None
        diagnostics["native_preview_render_count"] = (
            int(diagnostics.get("native_preview_render_count", 0)) + 1
        )
        diagnostics["native_preview_render_ms"] = result.render_ms
        diagnostics["native_preview_readback_ms"] = result.readback_ms
        diagnostics["native_preview_width"] = result.width
        diagnostics["native_preview_height"] = result.height
        diagnostics["native_preview_readback_bytes"] = result.readback_bytes
        diagnostics["native_preview_skip_reason"] = None
        return result.image

    @property
    def preview_diagnostics(self) -> dict[str, Any]:
        """Runtime-only preview readback counters (diagnostics merge)."""

        return dict(self._preview_diagnostics)

    def readback_for_export(self) -> QImage | None:
        """Render an export image on demand; normal display never calls this."""

        if self._last_scene is None:
            return None
        if self._export_renderer is None:
            self._export_renderer = WgpuOffscreenRenderer(runtime=self._runtime)
        readback_start = time.perf_counter()
        from .panel_frame_preview_export import without_frame_preview  # noqa: PLC0415

        export_scene = replace(without_frame_preview(self._last_scene), channel_view=None)
        # Export completes its own native GPU projection. A pending display worker
        # must never turn an export into an older preview or a blank image.
        result = self._export_renderer.render_scene(
            export_scene,
            RenderInvalidation(full_recompose=True),
        )
        image = self._result_to_qimage(result.rgba, result.width, result.height)
        self.diagnostics = replace(
            self.diagnostics,
            export_readback_ms=(time.perf_counter() - readback_start) * 1000.0,
            readback_ms=0.0,
        )
        return image

    def release(self) -> None:
        cache = getattr(self, "_native_channel_view_cache", None)
        if cache is not None:
            cache.release()
            self._native_channel_view_cache = None
        projection = getattr(self, "_native_cmyk_projection", None)
        if projection is not None:
            projection.release()
            self._native_cmyk_projection = None
        self._unconfigure_surface()
        companion_pass = getattr(self, "_companion_pass", None)
        if companion_pass is not None:
            # Before the compositor goes: the companion target belongs to it.
            companion_pass.release()
            self._companion_pass = None
        if self._export_renderer is not None:
            self._export_renderer.release()
            self._export_renderer = None
        # The preview renderer owns no persistent GPU resources (its target
        # is destroyed per render); dropping the reference is enough.
        self._preview_renderer = None
        projection_cache = getattr(self, "_projection_cache", None)
        if projection_cache is not None:
            projection_cache.release()
            self._projection_cache = None
            self._compositor = None
        if self._compositor is not None:
            self._compositor.release()
            self._compositor = None
        if self.device_manager is not None:
            self.device_manager.release()
        self._bundle = None
        self._device = None
        self._queue = None
        self._has_presented_frame = False
        self._last_surface_blit_signature = None
        self._fullscreen_quad_buffer = None
        # Device-owned proof resources; the LUT itself (CPU data) survives
        # so a re-adopted device can rebuild the texture (spec 11 §8.2).
        self._proof_texture = None
        self._proof_texture_key = None
        self._proof_sampler = None
        self._proof_bind_group_layout = None

    def _surface_blit_signature(
        self,
        scene: RenderSceneSnapshot,
        *,
        device_pixel_ratio: float,
        companion: SpreadCompanionFrame | None = None,
    ) -> tuple[Any, ...]:
        viewport = self._viewport_provider()
        transform = viewport.get_transform()
        surface_width, surface_height = self._surface_size_provider()
        return (
            int(scene.width),
            int(scene.height),
            max(1, int(surface_width)),
            max(1, int(surface_height)),
            round(float(device_pixel_ratio), 6),
            round(float(transform.m11()), 6),
            round(float(transform.m12()), 6),
            round(float(transform.m21()), 6),
            round(float(transform.m22()), 6),
            round(float(transform.dx()), 6),
            round(float(transform.dy()), 6),
            # Without this the frame-reuse shortcut would keep showing the
            # previous spread after a page switch or a spread toggle.
            None if companion is None else companion.signature(),
            # Proof toggle / profile change must re-present the same scene.
            self.soft_proof_lut_key,
            scene.channel_view,
        )

    @staticmethod
    def _optional_present_kwargs(
        *,
        overlay_texture: WgpuRenderTargetHandle | None,
        view_params: WgpuViewParams | None,
        companion: SpreadCompanionFrame | None,
        companion_target: WgpuRenderTargetHandle | None,
    ) -> dict[str, Any]:
        """Kwargs passed only when set, so duck-typed test doubles keep working."""

        kwargs: dict[str, Any] = {}
        if overlay_texture is not None:
            kwargs["overlay_texture"] = overlay_texture
        if view_params is not None:
            kwargs["target_is_view_space"] = True
        if companion is not None and companion_target is not None:
            kwargs["companion"] = companion
            kwargs["companion_target"] = companion_target
        return kwargs

    def _companion_target(
        self,
        companion: SpreadCompanionFrame | None,
        *,
        view: WgpuViewParams | None,
    ) -> WgpuRenderTargetHandle | None:
        """Composite the spread's facing page, or drop it when unused."""

        companion_pass = getattr(self, "_companion_pass", None)
        if companion is None:
            # Leaving the spread must not keep the facing page's textures
            # and composite target resident.
            if companion_pass is not None:
                companion_pass.release()
            return None
        if companion_pass is None:
            companion_pass = SpreadCompanionPass(
                repaint=getattr(self, "_native_cmyk_repaint", None)
            )
            self._companion_pass = companion_pass
        return companion_pass.prepare(
            companion,
            bundle=self._bundle,
            compositor=getattr(self, "_compositor", None),
            view=view,
        )

    def _present_target(
        self,
        target: WgpuRenderTargetHandle,
        scene: RenderSceneSnapshot,
        *,
        surface_target: WgpuRenderTargetHandle,
        device_pixel_ratio: float,
        overlay_texture: WgpuRenderTargetHandle | None = None,
        target_is_view_space: bool = False,
        companion: SpreadCompanionFrame | None = None,
        companion_target: WgpuRenderTargetHandle | None = None,
    ) -> None:
        if self._device is None or self._queue is None:
            raise WgpuRenderError("wgpu native device is not initialized")
        # Resolve the proof state BEFORE any pipeline/bind-group is built:
        # a mid-frame flip would pair a proofed pipeline with an unproofed
        # bind group (layout mismatch).
        self._prepare_proof_for_present()
        encoder = self._device.create_command_encoder()
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": surface_target.view,
                    "resolve_target": None,
                    "clear_value": (0.0, 0.0, 0.0, 1.0),
                    "load_op": self._wgpu.LoadOp.clear,
                    "store_op": self._wgpu.StoreOp.store,
                }
            ]
        )
        if companion is not None and companion_target is not None:
            # The spread's facing page, drawn before the active one so that a
            # zero gutter resolves in favour of the page being edited.  Its
            # quad covers only the facing page's on-screen rectangle: the
            # composite clears its whole target with that page's background,
            # so a full-screen blit would repaint the surround.
            render_pass.set_pipeline(self._blit_pipeline(str(surface_target.format)))
            render_pass.set_bind_group(0, self._blit_bind_group(companion_target), [], 0, 999999)
            render_pass.set_vertex_buffer(
                0,
                self._surface_vertex_buffer(
                    companion.scene,
                    surface_target,
                    device_pixel_ratio=device_pixel_ratio,
                    identity_uv=target_is_view_space,
                    source_origin=companion.offset,
                ),
            )
            render_pass.draw(6, 1, 0, 0)
        render_pass.set_pipeline(self._blit_pipeline(str(surface_target.format)))
        render_pass.set_bind_group(0, self._blit_bind_group(target), [], 0, 999999)
        vertex_buffer = (
            # View-resolution composites already carry the viewport transform:
            # blit the canvas quad 1:1 (uv == position) so the area outside
            # the canvas keeps the legacy surround instead of the composite
            # clear color.
            self._surface_vertex_buffer(
                scene,
                surface_target,
                device_pixel_ratio=device_pixel_ratio,
                identity_uv=True,
            )
            if target_is_view_space
            else self._surface_vertex_buffer(
                scene,
                surface_target,
                device_pixel_ratio=device_pixel_ratio,
            )
        )
        render_pass.set_vertex_buffer(0, vertex_buffer)
        render_pass.draw(6, 1, 0, 0)
        if overlay_texture is not None:
            # Display-resolution vector overlay (topmost eligible layer)
            # composited over the blitted frame.
            render_pass.set_pipeline(self._overlay_pipeline(str(surface_target.format)))
            render_pass.set_bind_group(0, self._blit_bind_group(overlay_texture), [], 0, 999999)
            render_pass.set_vertex_buffer(0, self._fullscreen_vertex_buffer())
            render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        self._queue.submit([encoder.finish()])
        try:
            cast(Any, self._canvas_context).present()
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            self._surface_lost_count += 1
            self._has_presented_frame = False
            self._unconfigure_surface()
            raise WgpuRenderError(f"wgpu native surface present failed: {exc}") from exc

    def _current_surface_target(self) -> WgpuRenderTargetHandle:
        self._ensure_surface()
        if self._canvas_context is None or self._surface_format is None:
            raise WgpuRenderError("wgpu native surface is not configured")
        try:
            surface_texture = self._canvas_context.get_current_texture()
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            self._surface_lost_count += 1
            self._has_presented_frame = False
            self._unconfigure_surface()
            raise WgpuRenderError(f"wgpu native surface current texture failed: {exc}") from exc
        width, height = self._surface_size_provider()
        return WgpuRenderTargetHandle(
            texture=surface_texture,
            view=surface_texture.create_view(),
            width=max(1, int(width)),
            height=max(1, int(height)),
            format=str(self._surface_format),
        )

    def _ensure_surface(self) -> None:
        window_id = int(self._window_id_provider())
        width, height = self._surface_size_provider()
        key = (window_id, max(1, int(width)), max(1, int(height)))
        if window_id <= 0:
            raise WgpuRenderError("wgpu native surface has no native window id")
        if self._canvas_context is not None and self._surface_key == key:
            return

        self._unconfigure_surface()
        try:
            canvas_context = self._wgpu.gpu.get_canvas_context(
                {"method": "screen", "platform": "windows", "window": window_id, "vsync": False}
            )
            set_size = getattr(canvas_context, "set_physical_size", None)
            if callable(set_size):
                set_size(key[1], key[2])
            self._ensure_device_bundle(canvas_context)
            if self._bundle is None or self._device is None:
                raise WgpuRenderError("wgpu native device is not initialized")
            surface_format = canvas_context.get_preferred_format(self._bundle.adapter)
            canvas_context.configure(
                device=self._device,
                format=surface_format,
                usage=self._wgpu.TextureUsage.RENDER_ATTACHMENT,
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            self._surface_lost_count += 1
            raise WgpuRenderError(f"wgpu native surface configure failed: {exc}") from exc

        self._canvas_context = canvas_context
        self._surface_format = surface_format
        self._surface_key = key
        self._surface_recreate_count += 1
        self.diagnostics = replace(
            self.diagnostics,
            surface_status="ready",
            surface_recreate_count=self._surface_recreate_count,
            surface_lost_count=self._surface_lost_count,
        )

    def _ensure_device_bundle(self, canvas_context: Any) -> None:
        if self._bundle is not None:
            return
        if self.device_manager is not None:
            self._adopt_bundle(self.device_manager.get_bundle())
            return
        try:
            adapter = self._request_surface_adapter(canvas_context)
            device = adapter.request_device_sync(required_features=[], required_limits={})
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuRenderError(f"wgpu native device creation failed: {exc}") from exc
        self._adopt_bundle(
            WgpuDeviceBundle(
                wgpu=self._wgpu,
                adapter=adapter,
                device=device,
                queue=device.queue,
                adapter_info=self._adapter_info(adapter),
            )
        )

    def _request_surface_adapter(self, canvas_context: Any) -> Any:
        had_backend_override = "WGPU_BACKEND_TYPE" in os.environ
        previous_backend = os.environ.get("WGPU_BACKEND_TYPE")
        force_d3d12 = sys.platform == "win32" and not (previous_backend or "").strip()
        if force_d3d12:
            os.environ["WGPU_BACKEND_TYPE"] = "D3D12"
        try:
            adapter = self._wgpu.gpu.request_adapter_sync(
                power_preference=self._power_preference,
                canvas=canvas_context,
            )
        finally:
            if force_d3d12:
                if had_backend_override and previous_backend is not None:
                    os.environ["WGPU_BACKEND_TYPE"] = previous_backend
                else:
                    os.environ.pop("WGPU_BACKEND_TYPE", None)
        if adapter is None:
            raise WgpuUnavailableError("wgpu native surface adapter is unavailable")
        return adapter

    def _adopt_bundle(self, bundle: WgpuDeviceBundle) -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self._projection_cache = WgpuProjectionCache(
            bundle, repaint=getattr(self, "_native_cmyk_repaint", None)
        )
        self._compositor = self._projection_cache.compositor
        self.diagnostics = replace(self.diagnostics, adapter_name=self._adapter_name())

    def _view_params(self, *, device_pixel_ratio: float) -> WgpuViewParams | None:
        """Build view-resolution compositing params when the mode is on."""

        if not view_resolution_compositing_enabled():
            return None
        try:
            viewport = self._viewport_provider()
            transform = viewport.get_transform()
            dpr = max(1.0, float(device_pixel_ratio))
            width, height = self._surface_size_provider()
            params = WgpuViewParams(
                affine=(
                    float(transform.m11()) * dpr,
                    float(transform.m21()) * dpr,
                    float(transform.m12()) * dpr,
                    float(transform.m22()) * dpr,
                    float(transform.dx()) * dpr,
                    float(transform.dy()) * dpr,
                ),
                width=max(1, int(width)),
                height=max(1, int(height)),
            )
        except Exception:
            logger.warning("view compositing params unavailable", exc_info=True)
            return None
        return params if params.inverse() is not None else None

    def _log_view_state(self, scene: RenderSceneSnapshot, view: WgpuViewParams | None) -> None:
        """INFO-log view compositing transitions so field reports are diagnosable."""

        direct_layers = sum(
            1 for layer in scene.flat_layers if bool(getattr(layer, "vector_direct_ready", False))
        )
        vector_layers = sum(
            1
            for layer in scene.flat_layers
            if str(getattr(layer, "layer_type", "raster")) == "vector"
        )
        raster_scale = max(
            (int(getattr(layer, "vector_raster_scale", 1) or 1) for layer in scene.flat_layers),
            default=1,
        )
        state: tuple[Any, ...] = (
            view is not None,
            None if view is None else (view.width, view.height, round(view.scale(), 3)),
            direct_layers,
            vector_layers,
            raster_scale,
        )
        if state == getattr(self, "_last_view_log_state", None):
            return
        self._last_view_log_state = state
        if view is None:
            logger.info(
                "View compositing inactive (flag=%s); direct vector layers %d/%d",
                view_resolution_compositing_enabled(),
                direct_layers,
                vector_layers,
            )
        else:
            logger.info(
                "View compositing active %dx%d scale=%.3f; direct vector layers %d/%d; "
                "raster prescale=%d%s",
                view.width,
                view.height,
                view.scale(),
                direct_layers,
                vector_layers,
                raster_scale,
                (
                    " — no direct-eligible vector layer (effects/preview fall back)"
                    if direct_layers == 0 and vector_layers > 0
                    else ""
                ),
            )

    def _render_projection(
        self,
        scene: RenderSceneSnapshot,
        invalidation: RenderInvalidation | None,
        *,
        device_pixel_ratio: float,
        screen_space_vector_layer_id: str | None = None,
        view: WgpuViewParams | None = None,
    ) -> tuple[WgpuRenderTargetHandle, tuple[str, ...], WgpuRenderMetrics, bool]:
        three_d_display_scale = self._three_d_display_scale(
            scene,
            device_pixel_ratio=device_pixel_ratio,
        )
        self._set_three_d_display_scale(three_d_display_scale)
        # Passed only when set so duck-typed test doubles keep working.
        extra_kwargs: dict[str, Any] = (
            {"screen_space_vector_layer_id": screen_space_vector_layer_id}
            if screen_space_vector_layer_id is not None
            else {}
        )
        if view is not None:
            extra_kwargs["view"] = view
        projection_cache = getattr(self, "_projection_cache", None)
        if projection_cache is not None:
            result = projection_cache.render(scene, invalidation=invalidation, **extra_kwargs)
            return result.target, result.warnings, result.metrics, False

        if self._compositor is None:
            self._ensure_surface()
            self._set_three_d_display_scale(three_d_display_scale)
        compositor = self._compositor
        if compositor is None:
            raise WgpuRenderError("wgpu native compositor is not initialized")
        target, warnings, metrics = compositor.render(
            scene, invalidation=invalidation, **extra_kwargs
        )
        return target, warnings, metrics, True

    def _set_three_d_display_scale(self, scale: float) -> None:
        projection_cache = getattr(self, "_projection_cache", None)
        compositor = (
            getattr(projection_cache, "compositor", None)
            if projection_cache is not None
            else getattr(self, "_compositor", None)
        )
        setter = getattr(compositor, "set_three_d_display_scale", None)
        if callable(setter):
            setter(scale)

    def _three_d_display_scale(
        self,
        scene: RenderSceneSnapshot,
        *,
        device_pixel_ratio: float,
    ) -> float:
        if not self._scene_has_visible_3d_layer(scene):
            return 1.0
        viewport = self._viewport_provider()
        transform = viewport.get_transform()
        dpr = max(1.0, float(device_pixel_ratio))
        scale_x = hypot(float(transform.m11()), float(transform.m12())) * dpr
        scale_y = hypot(float(transform.m21()), float(transform.m22())) * dpr
        scale = max(scale_x, scale_y)
        if scale <= 0.0:
            return 1.0
        scale = max(0.25, min(1.0, scale))
        return 1.0 if scale >= 0.95 else scale

    @staticmethod
    def _scene_has_visible_3d_layer(scene: RenderSceneSnapshot) -> bool:
        return any(
            str(getattr(layer, "layer_type", "raster")) == "3d"
            and bool(getattr(layer, "visible", True))
            and getattr(layer, "scene_3d", None) is not None
            for layer in scene.flat_layers
        )

    def _unconfigure_surface(self) -> None:
        if self._canvas_context is not None:
            unconfigure = getattr(self._canvas_context, "unconfigure", None)
            if callable(unconfigure):
                with suppress(Exception):
                    unconfigure()
        self._canvas_context = None
        self._surface_format = None
        self._surface_key = None

    def _surface_vertex_buffer(
        self,
        scene: RenderSceneSnapshot,
        target: WgpuRenderTargetHandle,
        *,
        device_pixel_ratio: float,
        identity_uv: bool = False,
        source_origin: tuple[float, float] = (0.0, 0.0),
    ) -> Any:
        """Quad mapping ``scene`` onto the surface through the viewport.

        ``source_origin`` shifts the quad in canvas space, which is how the
        spread's facing page reaches its own side of the gutter (CT-06
        B4-c4).  The active page always uses the default ``(0, 0)``.
        """

        viewport = self._viewport_provider()
        transform = viewport.get_transform()
        dpr = max(1.0, float(device_pixel_ratio))
        origin_x, origin_y = float(source_origin[0]), float(source_origin[1])
        right = origin_x + float(scene.width)
        bottom = origin_y + float(scene.height)
        points = {
            "tl": transform.map(QPointF(origin_x, origin_y)),
            "tr": transform.map(QPointF(right, origin_y)),
            "bl": transform.map(QPointF(origin_x, bottom)),
            "br": transform.map(QPointF(right, bottom)),
        }

        def ndc(point: QPointF) -> tuple[float, float]:
            x = (float(point.x()) * dpr / max(1, target.width)) * 2.0 - 1.0
            y = 1.0 - (float(point.y()) * dpr / max(1, target.height)) * 2.0
            return x, y

        def vertex(key: str, u: float, v: float) -> tuple[float, float, float, float]:
            x, y = ndc(points[key])
            if identity_uv:
                # Sample the (view-space) texture at the on-screen position.
                return (x, y, x * 0.5 + 0.5, 0.5 - y * 0.5)
            return (x, y, u, v)

        vertices = np.array(
            [
                vertex("tl", 0.0, 0.0),
                vertex("tr", 1.0, 0.0),
                vertex("bl", 0.0, 1.0),
                vertex("bl", 0.0, 1.0),
                vertex("tr", 1.0, 0.0),
                vertex("br", 1.0, 1.0),
            ],
            dtype=np.float32,
        )
        device = cast(Any, self._device)
        return device.create_buffer_with_data(
            data=vertices.tobytes(),
            usage=self._wgpu.BufferUsage.VERTEX,
        )

    def _blit_pipeline(self, target_format: str) -> Any:
        proofed = self._proof_active()
        cache_key = f"proof:{target_format}" if proofed else target_format
        channel_index = getattr(self, "_channel_view_index", -1)
        if channel_index >= 0:
            cache_key = f"channel:{channel_index}:{cache_key}"
        if cache_key in self._pipeline_cache:
            return self._pipeline_cache[cache_key]
        device = cast(Any, self._device)
        try:
            module = device.create_shader_module(
                code=ChannelsSurfaceView.shader(
                    self._surface_blit_shader(target_format, proofed=proofed), channel_index
                )
            )
            layout = device.create_pipeline_layout(
                bind_group_layouts=[self._active_blit_bind_group_layout()]
            )
            pipeline = device.create_render_pipeline(
                layout=layout,
                vertex={
                    "module": module,
                    "entry_point": "vs_main",
                    "buffers": [
                        {
                            "array_stride": 16,
                            "step_mode": self._wgpu.VertexStepMode.vertex,
                            "attributes": [
                                {
                                    "format": self._wgpu.VertexFormat.float32x2,
                                    "offset": 0,
                                    "shader_location": 0,
                                },
                                {
                                    "format": self._wgpu.VertexFormat.float32x2,
                                    "offset": 8,
                                    "shader_location": 1,
                                },
                            ],
                        }
                    ],
                },
                fragment={
                    "module": module,
                    "entry_point": "fs_main",
                    "targets": [
                        {
                            "format": target_format,
                            "write_mask": self._wgpu.ColorWrite.ALL,
                        }
                    ],
                },
                primitive={
                    "topology": self._wgpu.PrimitiveTopology.triangle_list,
                    "strip_index_format": None,
                    "front_face": self._wgpu.FrontFace.ccw,
                    "cull_mode": self._wgpu.CullMode.none,
                },
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuRenderError(f"failed to create native surface blit pipeline: {exc}") from exc
        self._pipeline_cache[cache_key] = pipeline
        return pipeline

    @staticmethod
    def _screen_space_vector_layer(scene: RenderSceneSnapshot) -> Any | None:
        """Return the topmost content layer when the display-resolution
        vector pass can own it (same constraints as the QPainter SSO:
        topmost, opacity 1, normal blend, no mask, no clipping)."""

        flat = scene.flat_layers
        parent_ids = scene.parent_ids
        visible_by_id = {
            str(getattr(layer, "id", "")): bool(getattr(layer, "visible", True)) for layer in flat
        }

        def effectively_visible(index: int) -> bool:
            layer = flat[index]
            if not bool(getattr(layer, "visible", True)):
                return False
            parent_id = parent_ids[index] if index < len(parent_ids) else None
            while parent_id is not None:
                if not visible_by_id.get(str(parent_id), True):
                    return False
                parent_index = next(
                    (
                        i
                        for i, candidate in enumerate(flat)
                        if str(getattr(candidate, "id", "")) == str(parent_id)
                    ),
                    None,
                )
                if parent_index is None:
                    break
                parent_id = parent_ids[parent_index] if parent_index < len(parent_ids) else None
            return True

        # A visible clipping layer directly above the candidate (hidden layers
        # do not break the chain — clipping-run parity) makes the candidate a
        # clipping-run base: the compositor must draw the whole run, so the
        # display-resolution pass may not claim it.
        visible_clip_above = False
        for index in range(len(flat) - 1, -1, -1):
            layer = flat[index]
            layer_type = str(getattr(layer, "layer_type", "raster"))
            if layer_type == "group":
                continue
            if not effectively_visible(index):
                continue
            has_content = (
                getattr(layer, "image", None) is not None
                or bool(getattr(layer, "vector_direct_ready", False))
                or (
                    layer_type in {"vector", "text"}
                    and bool(getattr(layer, "vector_objects", None))
                )
                or (layer_type == "3d" and getattr(layer, "scene_3d", None) is not None)
            )
            if not has_content:
                visible_clip_above = bool(getattr(layer, "clipping", False))
                continue
            if not bool(getattr(layer, "vector_direct_ready", False)):
                return None
            if getattr(layer, "vector_render_layer", None) is None:
                return None
            blend_mode = getattr(layer, "blend_mode", "normal")
            blend_name = str(getattr(blend_mode, "value", blend_mode) or "normal")
            eligible = (
                float(getattr(layer, "opacity", 1.0)) >= 1.0
                and blend_name in {"normal", "pass_through"}
                and not bool(getattr(layer, "clipping", False))
                and not visible_clip_above
                and (
                    getattr(layer, "layer_mask", None) is None
                    or not bool(getattr(layer, "mask_enabled", True))
                )
            )
            return layer if eligible else None
        return None

    def _render_screen_space_overlay(
        self,
        layer: Any,
        *,
        device_pixel_ratio: float,
    ) -> WgpuRenderTargetHandle | None:
        vector_renderer = getattr(self._compositor, "vector_renderer", None)
        payload = getattr(layer, "vector_render_layer", None)
        if vector_renderer is None or payload is None:
            return None
        if self._device is None or self._queue is None:
            return None
        surface_width, surface_height = self._surface_size_provider()

        viewport = self._viewport_provider()
        transform = viewport.get_transform()
        dpr = max(1.0, float(device_pixel_ratio))
        view = (
            float(transform.m11()) * dpr,
            float(transform.m21()) * dpr,
            float(transform.m12()) * dpr,
            float(transform.m22()) * dpr,
            float(transform.dx()) * dpr,
            float(transform.dy()) * dpr,
        )
        la, lb, lc, ld, ltx, lty = payload.layer_transform
        va, vb, vc, vd, vtx, vty = view
        combined = (
            va * la + vb * lc,
            va * lb + vb * ld,
            vc * la + vd * lc,
            vc * lb + vd * ld,
            va * ltx + vb * lty + vtx,
            vc * ltx + vd * lty + vty,
        )
        scale = max(hypot(va, vc), hypot(vb, vd))
        encoder = self._device.create_command_encoder()
        resolve = cast(
            "WgpuRenderTargetHandle | None",
            vector_renderer.render_screen_space(
                encoder,
                payload,
                view_transform=combined,
                width=max(1, int(surface_width)),
                height=max(1, int(surface_height)),
                lod_bucket=lod_bucket_for_scale(scale),
            ),
        )
        self._queue.submit([encoder.finish()])
        return resolve

    def _overlay_pipeline(self, target_format: str) -> Any:
        proofed = self._proof_active()
        cache_key = f"overlay:proof:{target_format}" if proofed else f"overlay:{target_format}"
        if cache_key in self._pipeline_cache:
            return self._pipeline_cache[cache_key]
        device = cast(Any, self._device)
        transform = (
            SURFACE_BLIT_SRGB_TARGET_WGSL
            if self._target_format_uses_srgb_conversion(target_format)
            else SURFACE_BLIT_LINEAR_TARGET_WGSL
        )
        shader = SURFACE_VECTOR_OVERLAY_WGSL.replace(
            "/*TARGET_COLOR_TRANSFORM*/", transform
        ).replace(
            "/*PROOF_TRANSFORM*/",
            SURFACE_PROOF_ENABLED_WGSL if proofed else SURFACE_PROOF_DISABLED_WGSL,
        )
        blend_component = {
            "src_factor": self._wgpu.BlendFactor.one,
            "dst_factor": self._wgpu.BlendFactor.one_minus_src_alpha,
            "operation": self._wgpu.BlendOperation.add,
        }
        try:
            module = device.create_shader_module(code=shader)
            layout = device.create_pipeline_layout(
                bind_group_layouts=[self._active_blit_bind_group_layout()]
            )
            pipeline = device.create_render_pipeline(
                layout=layout,
                vertex={
                    "module": module,
                    "entry_point": "vs_main",
                    "buffers": [
                        {
                            "array_stride": 16,
                            "step_mode": self._wgpu.VertexStepMode.vertex,
                            "attributes": [
                                {
                                    "format": self._wgpu.VertexFormat.float32x2,
                                    "offset": 0,
                                    "shader_location": 0,
                                },
                                {
                                    "format": self._wgpu.VertexFormat.float32x2,
                                    "offset": 8,
                                    "shader_location": 1,
                                },
                            ],
                        }
                    ],
                },
                fragment={
                    "module": module,
                    "entry_point": "fs_main",
                    "targets": [
                        {
                            "format": target_format,
                            "blend": {
                                "color": dict(blend_component),
                                "alpha": dict(blend_component),
                            },
                            "write_mask": self._wgpu.ColorWrite.ALL,
                        }
                    ],
                },
                primitive={
                    "topology": self._wgpu.PrimitiveTopology.triangle_list,
                    "strip_index_format": None,
                    "front_face": self._wgpu.FrontFace.ccw,
                    "cull_mode": self._wgpu.CullMode.none,
                },
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuRenderError(
                f"failed to create native surface vector overlay pipeline: {exc}"
            ) from exc
        self._pipeline_cache[cache_key] = pipeline
        return pipeline

    def _fullscreen_vertex_buffer(self) -> Any:
        if self._fullscreen_quad_buffer is not None:
            return self._fullscreen_quad_buffer
        vertices = np.array(
            [
                (-1.0, 1.0, 0.0, 0.0),
                (1.0, 1.0, 1.0, 0.0),
                (-1.0, -1.0, 0.0, 1.0),
                (-1.0, -1.0, 0.0, 1.0),
                (1.0, 1.0, 1.0, 0.0),
                (1.0, -1.0, 1.0, 1.0),
            ],
            dtype=np.float32,
        )
        device = cast(Any, self._device)
        self._fullscreen_quad_buffer = device.create_buffer_with_data(
            data=vertices.tobytes(),
            usage=self._wgpu.BufferUsage.VERTEX,
        )
        return self._fullscreen_quad_buffer

    @staticmethod
    def _surface_blit_shader(target_format: str, *, proofed: bool = False) -> str:
        transform = (
            SURFACE_BLIT_SRGB_TARGET_WGSL
            if WgpuNativeCanvasPresenter._target_format_uses_srgb_conversion(target_format)
            else SURFACE_BLIT_LINEAR_TARGET_WGSL
        )
        return SURFACE_BLIT_WGSL.replace("/*TARGET_COLOR_TRANSFORM*/", transform).replace(
            "/*PROOF_TRANSFORM*/",
            SURFACE_PROOF_ENABLED_WGSL if proofed else SURFACE_PROOF_DISABLED_WGSL,
        )

    @staticmethod
    def _target_format_uses_srgb_conversion(target_format: str) -> bool:
        return "srgb" in str(target_format).lower()

    @property
    def _blit_bind_group_layout(self) -> Any:
        if self._bind_group_layout is None:
            device = cast(Any, self._device)
            self._bind_group_layout = device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "sampler": {"type": self._wgpu.SamplerBindingType.filtering},
                    },
                    {
                        "binding": 1,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                ]
            )
        return self._bind_group_layout

    @property
    def _blit_sampler(self) -> Any:
        if self._sampler is None:
            device = cast(Any, self._device)
            self._sampler = device.create_sampler(
                mag_filter="nearest",
                min_filter="nearest",
            )
        return self._sampler

    def _active_blit_bind_group_layout(self) -> Any:
        if not self._proof_active():
            return self._blit_bind_group_layout
        if self._proof_bind_group_layout is None:
            device = cast(Any, self._device)
            self._proof_bind_group_layout = device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "sampler": {"type": self._wgpu.SamplerBindingType.filtering},
                    },
                    {
                        "binding": 1,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                    {
                        "binding": 2,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "sampler": {"type": self._wgpu.SamplerBindingType.filtering},
                    },
                    {
                        "binding": 3,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "3d",
                        },
                    },
                ]
            )
        return self._proof_bind_group_layout

    @property
    def _proof_lut_sampler(self) -> Any:
        # Trilinear interpolation between LUT cells (spec 11 §8.2); the
        # nearest-filter blit sampler must not be reused here.
        if self._proof_sampler is None:
            device = cast(Any, self._device)
            self._proof_sampler = device.create_sampler(
                mag_filter="linear",
                min_filter="linear",
                address_mode_u="clamp-to-edge",
                address_mode_v="clamp-to-edge",
                address_mode_w="clamp-to-edge",
            )
        return self._proof_sampler

    def _ensure_proof_texture(self) -> Any | None:
        """Upload the current LUT as a 3D texture (idempotent per key)."""

        lut = self._proof_lut
        if lut is None:
            return None
        key = str(lut.key)
        if self._proof_texture is not None and self._proof_texture_key == key:
            return self._proof_texture
        device = cast(Any, self._device)
        queue = cast(Any, self._queue)
        size = int(lut.size)
        # table is indexed [b, g, r]: r is the fastest axis, so texture
        # x = r, y = g, z = b — matching the (r, g, b) sample coordinate.
        rgba = np.empty((size, size, size, 4), dtype=np.uint8)
        rgba[..., :3] = lut.table
        rgba[..., 3] = 255
        texture = device.create_texture(
            size=(size, size, size),
            dimension="3d",
            format=self._wgpu.TextureFormat.rgba8unorm,
            usage=self._wgpu.TextureUsage.TEXTURE_BINDING | self._wgpu.TextureUsage.COPY_DST,
        )
        queue.write_texture(
            {"texture": texture, "origin": (0, 0, 0)},
            np.ascontiguousarray(rgba).tobytes(),
            {"bytes_per_row": size * 4, "rows_per_image": size},
            (size, size, size),
        )
        self._proof_texture = texture
        self._proof_texture_key = key
        return texture

    def _blit_bind_group(self, source: WgpuRenderTargetHandle) -> Any:
        device = cast(Any, self._device)
        entries = [
            {"binding": 0, "resource": self._blit_sampler},
            {"binding": 1, "resource": source.view},
        ]
        if self._proof_active():
            texture = self._ensure_proof_texture()
            entries.append({"binding": 2, "resource": self._proof_lut_sampler})
            entries.append({"binding": 3, "resource": texture.create_view(dimension="3d")})
            return device.create_bind_group(
                layout=self._active_blit_bind_group_layout(), entries=entries
            )
        return device.create_bind_group(
            layout=self._blit_bind_group_layout,
            entries=entries,
        )

    def _prepare_proof_for_present(self) -> None:
        """Upload the proof LUT before the frame, or disable proofing.

        Spec 11 §14: a proof that cannot run must turn itself off loudly
        rather than present unproofed pixels under a proof label.
        """

        if not self._proof_active():
            return
        try:
            self._ensure_proof_texture()
        except Exception:
            logger.warning("soft proof LUT upload failed; disabling proof", exc_info=True)
            self._proof_lut = None
            self._proof_texture = None
            self._proof_texture_key = None
            self._last_warnings = tuple(
                dict.fromkeys([*self._last_warnings, "soft_proof_disabled"])
            )

    def _adapter_name(self) -> str:
        if self._bundle is None:
            return ""
        info = self._bundle.adapter_info
        for key in ("device", "adapter", "name", "description"):
            value = info.get(key)
            if value:
                return str(value)
        return ""

    def _texture_cache_estimate(self) -> int:
        if self._compositor is None:
            return 0
        estimate = getattr(self._compositor.texture_manager, "estimated_bytes", None)
        return int(estimate()) if callable(estimate) else 0

    @staticmethod
    def _adapter_info(adapter: Any) -> dict[str, Any]:
        request_info = getattr(adapter, "request_adapter_info", None)
        if callable(request_info):
            try:
                info = request_info()
                if isinstance(info, dict):
                    return dict(info)
            except Exception:
                return {}
        info = getattr(adapter, "info", None)
        return dict(info) if isinstance(info, dict) else {}

    def _record_present_ms(self, present_ms: float) -> None:
        self._present_durations_ms.append(float(present_ms))
        if len(self._present_durations_ms) > 120:
            del self._present_durations_ms[: len(self._present_durations_ms) - 120]

    def _present_p95_ms(self) -> float:
        if not self._present_durations_ms:
            return 0.0
        ordered = sorted(self._present_durations_ms)
        index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * 0.95)))
        return float(ordered[index])

    @staticmethod
    def _format_scene_signature(signature: tuple[Any, ...] | None) -> str:
        return "" if signature is None else str(hash(signature))

    @staticmethod
    def _result_to_qimage(rgba: np.ndarray, width: int, height: int) -> QImage:
        data = np.ascontiguousarray(rgba)
        if data.shape != (height, width, 4):
            raise WgpuRenderError(
                f"wgpu export shape {data.shape!r} did not match {(height, width, 4)!r}"
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
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()
