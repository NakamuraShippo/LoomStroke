"""Cache sibling-filter inputs in document space before projecting a view."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from PySide6.QtGui import QColor

from loomstroke.core.canvas_invalidation import GpuResourceInvalidation, RenderInvalidation
from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot, _stable_signature

from .filter_processor import WgpuFilterProcessor


class WgpuFilterPrefixRenderer:
    """Own document-space GPU projections only for visible sibling filters.

    A viewport omits offscreen blur input and moves mask/pattern coordinates.
    Evaluate the affected prefix with the existing document compositor, then
    project its cached GPU texture. Layers above it retain view rendering.
    """

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._caches: dict[str, Any] = {}
        self._used: set[str] = set()

    def begin_frame(self) -> None:
        self._used.clear()

    def render(
        self,
        scene: RenderSceneSnapshot,
        layers: tuple[Any, ...],
        *,
        depth: int,
        max_depth: int,
        display_scale: float,
        invalidation: RenderInvalidation | None,
    ) -> tuple[int, Any] | None:
        end = 0
        for index, layer in enumerate(layers):
            if (
                layer.layer_type == "effect"
                and layer.visible
                and WgpuFilterProcessor.can_apply(
                    getattr(layer, "filter_type", getattr(layer, "effect_type", None))
                )
            ):
                end = index + 1
        if not end:
            return None
        # A clipped filter can be followed by further members of the same
        # run. Keep that run intact when dividing the draw list.
        while end < len(layers) and (layers[end].clipping or not layers[end].visible):
            end += 1
        key = str(layers[end - 1].id)
        self._used.add(key)
        cache = self._caches.get(key)
        if cache is None:
            # ProjectionCache imports the compositor; defer to avoid a cycle.
            from .projection_cache import WgpuProjectionCache  # noqa: PLC0415

            cache = WgpuProjectionCache(self._bundle)
            self._caches[key] = cache
        cache.compositor.MAX_WGPU_GROUP_DEPTH = max(0, max_depth - depth)
        cache.compositor.set_three_d_display_scale(display_scale)
        prefix = self._scene(scene, layers[:end], depth=depth)
        ids = {layer.id for layer in prefix.flat_layers}
        local_invalidation = self._invalidation(invalidation, scene.active_layer_id, ids)
        return end, cache.render(prefix, invalidation=local_invalidation)

    @staticmethod
    def _invalidation(
        invalidation: RenderInvalidation | None, active_id: str | None, ids: set[str]
    ) -> RenderInvalidation | None:
        if invalidation is None:
            return None
        resources = invalidation.gpu_resources
        return RenderInvalidation(
            dirty_rect=invalidation.dirty_rect if active_id is None or active_id in ids else None,
            full_recompose=invalidation.full_recompose,
            mask_dirty_layer_ids=invalidation.mask_dirty_layer_ids & ids,
            gpu_resources=GpuResourceInvalidation(
                layer_ids=resources.layer_ids & ids,
                all_resources=resources.all_resources,
                reason=resources.reason,
            ),
        )

    @staticmethod
    def project(
        compositor: Any, encoder: Any, initial: Any, source: Any, scene: RenderSceneSnapshot
    ) -> Any:
        target = compositor.create_target(initial.width, initial.height)
        try:
            bind_group = compositor.shader_library.create_blend_bind_group(
                initial,
                source,
                canvas_size=(float(initial.width), float(initial.height)),
                dest_origin=(0.0, 0.0),
                dest_size=(float(scene.width), float(scene.height)),
                src_size=(float(scene.width), float(scene.height)),
                uv_origin=(0.0, 0.0),
                uv_size=(1.0, 1.0),
                opacity=1.0,
                blend_mode="normal",
                src_premultiplied=True,
                src_replace=True,
                **compositor._view_blend_kwargs(scene),
            )
            compositor._draw_blend_pass(
                encoder, compositor.shader_library.get_layer_blend_pipeline(), target, bind_group
            )
        except Exception:
            compositor._destroy_target(target)
            raise
        return target

    @staticmethod
    def _scene(
        scene: RenderSceneSnapshot, layers: tuple[Any, ...], *, depth: int
    ) -> RenderSceneSnapshot:
        flat: list[Any] = []
        parents: list[str | None] = []

        def visit(layer: Any, parent: str | None) -> None:
            flat.append(layer)
            parents.append(parent)
            for child in layer.children:
                visit(child, layer.id)

        for layer in layers:
            visit(layer, None)
        ids = {layer.id for layer in flat}
        active_id = scene.active_layer_id if scene.active_layer_id in ids else None
        active_flat_index = next((i for i, layer in enumerate(flat) if layer.id == active_id), -1)
        parent_by_id = {layer.id: parent for layer, parent in zip(flat, parents, strict=True)}
        active_root = active_id
        while active_root is not None and parent_by_id[active_root] is not None:
            active_root = parent_by_id[active_root]
        active_index = next((i for i, layer in enumerate(layers) if layer.id == active_root), -1)
        # Cover filter parameters and raster buckets even if no global layer
        # version changes. Unrelated upper-layer edits do not affect this key.
        filter_key = tuple(
            (
                getattr(layer, "filter_type", getattr(layer, "effect_type", None)),
                _stable_signature(
                    getattr(layer, "filter_params", getattr(layer, "effect_params", {}))
                ),
                layer.vector_raster_scale,
                layer.vector_raster_origin,
                (
                    getattr(layer, "filter_mask", getattr(layer, "effect_mask", None)).cacheKey()
                    if getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
                    is not None
                    else 0
                ),
                layer.layer_mask.cacheKey() if layer.layer_mask is not None else 0,
            )
            for layer in flat
        )
        vector = replace(
            scene.vector,
            preview_layer_id=(
                scene.vector.preview_layer_id if scene.vector.preview_layer_id in ids else None
            ),
            preview_object=(
                scene.vector.preview_object if scene.vector.preview_layer_id in ids else None
            ),
        )
        state = replace(
            scene.canvas_state,
            background=scene.background if depth == 0 else QColor(0, 0, 0, 0),
            active_layer_id=active_id,
            active_index=active_index,
            layer_cache_version=hash(filter_key),
        )
        return replace(
            scene,
            canvas_state=state,
            layers=layers,
            flat_layers=tuple(flat),
            parent_ids=tuple(parents),
            active_layer_id=active_id,
            active_flat_index=active_flat_index,
            vector=vector,
        )

    def finish_frame(self) -> None:
        for key in set(self._caches) - self._used:
            self._caches.pop(key).release()

    def release(self) -> None:
        for cache in self._caches.values():
            cache.release()
        self._caches.clear()
        self._used.clear()


WgpuEffectPrefixRenderer = WgpuFilterPrefixRenderer
WgpuEffectProcessor = WgpuFilterProcessor
