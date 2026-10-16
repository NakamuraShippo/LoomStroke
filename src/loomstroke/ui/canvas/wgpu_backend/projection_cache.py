"""wgpu projection-cache target ownership for GL-retirement parity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.ui.canvas.projection_state import ProjectionStateTracker
from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot, compute_render_scene_signature

from .compositor import WgpuCompositor
from .types import WgpuRenderMetrics, WgpuRenderTargetHandle


@dataclass(frozen=True, slots=True)
class WgpuProjectionCacheResult:
    """Result of a projection-cache update."""

    target: WgpuRenderTargetHandle
    warnings: tuple[str, ...]
    metrics: WgpuRenderMetrics
    reused: bool = False
    rebuild_reason: str | None = None


class WgpuProjectionCache:
    """Own a reusable wgpu render target for projection-cache parity.

    This is the wgpu-side counterpart to the GL projection FBO ownership. It
    deliberately does not perform CPU readback; callers present or read back
    the returned target explicitly.
    """

    def __init__(self, bundle: Any, *, compositor: WgpuCompositor | None = None) -> None:
        self._compositor = compositor or WgpuCompositor(bundle)
        self._owns_compositor = compositor is None
        self._tracker = ProjectionStateTracker()
        self._target: WgpuRenderTargetHandle | None = None
        self._last_scene_signature: tuple[Any, ...] | None = None
        self._cache_hits = 0
        self._full_rebuilds = 0

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def full_rebuilds(self) -> int:
        return self._full_rebuilds

    @property
    def compositor(self) -> WgpuCompositor:
        return self._compositor

    def render(
        self,
        scene: RenderSceneSnapshot,
        *,
        invalidation: RenderInvalidation | None = None,
        screen_space_vector_layer_id: str | None = None,
        view: Any | None = None,
    ) -> WgpuProjectionCacheResult:
        signature = self._projection_signature(scene, screen_space_vector_layer_id, view)
        flags = self._tracker.detect_changes(
            list(scene.flat_layers),
            list(scene.parent_ids),
            scene.active_flat_index,
            scene.width,
            scene.height,
            scene.canvas_state.background_rgba,
        )
        rebuild_reason = self._rebuild_reason(signature, invalidation, flags.any_changed)
        if self._target is not None and rebuild_reason is None:
            self._cache_hits += 1
            return WgpuProjectionCacheResult(
                target=self._target,
                warnings=(),
                metrics=WgpuRenderMetrics(layers_seen=len(scene.flat_layers), layers_drawn=0),
                reused=True,
            )

        try:
            target, warnings, metrics = self._compositor.render(
                scene,
                invalidation=invalidation,
                screen_space_vector_layer_id=screen_space_vector_layer_id,
                view=view,
            )
        except Exception:
            self._tracker.reset()
            self._last_scene_signature = None
            raise

        old_target = self._target
        self._target = target
        self._last_scene_signature = signature
        self._full_rebuilds += 1
        if old_target is not None:
            self._destroy_target(old_target)
        return WgpuProjectionCacheResult(
            target=target,
            warnings=warnings,
            metrics=metrics,
            reused=False,
            rebuild_reason=rebuild_reason or "no_cache",
        )

    def release(self) -> None:
        if self._target is not None:
            self._destroy_target(self._target)
            self._target = None
        self._tracker.reset()
        self._last_scene_signature = None
        if self._owns_compositor:
            self._compositor.release()

    def _rebuild_reason(
        self,
        signature: tuple[Any, ...],
        invalidation: RenderInvalidation | None,
        tracker_changed: bool,
    ) -> str | None:
        if self._target is None:
            return "no_cache"
        if invalidation is not None and invalidation.has_any:
            return "invalidation"
        if self._last_scene_signature != signature:
            return "scene_signature_changed"
        if tracker_changed:
            return "projection_state_changed"
        return None

    def _projection_signature(
        self,
        scene: RenderSceneSnapshot,
        screen_space_vector_layer_id: str | None = None,
        view: Any | None = None,
    ) -> tuple[Any, ...]:
        view_part: tuple[Any, ...] | None = None
        if view is not None:
            view_part = (
                tuple(round(float(value), 6) for value in view.affine),
                int(view.width),
                int(view.height),
            )
        return (
            compute_render_scene_signature(scene),
            "three_d_display_scale",
            round(float(getattr(self._compositor, "three_d_display_scale", 1.0)), 6),
            screen_space_vector_layer_id,
            view_part,
        )

    @staticmethod
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()
