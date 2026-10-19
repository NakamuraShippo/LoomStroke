"""Minimal wgpu offscreen compositor for render-scene snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter

from loomstroke.core.appearance.live_styles import style_signature
from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_invalidation import RenderInvalidation
from loomstroke.core.commands import BlendMode
from loomstroke.core.mask_coverage import mask_coverage_alpha8
from loomstroke.ui.canvas.render_scene import RenderSceneSnapshot

from .encoder_scratch import EncoderScratchTargets
from .filter_prefix_renderer import WgpuFilterPrefixRenderer
from .filter_processor import WgpuFilterProcessor, WgpuFilterResult
from .filter_raster_context import DEFAULT_FILTER_RASTER_CONTEXT, FilterRasterContext
from .group_composite_cache import GroupCompositeCache, group_composite_key
from .group_mask_upload import group_mask_upload_scope
from .group_region import document_group_view, group_region_view, group_target_region
from .layer_style_processor import WgpuLayerStyleProcessor, finish_render_caches
from .mask_texture_manager import WgpuMaskTextureManager
from .mask_upload_region import nearest_mask_upload_region
from .shader_library import WgpuShaderLibrary
from .style_backdrop import backdrop_keys, backdrop_region, clip_backdrop_scope, style_backdrop
from .target_reuse import (
    allocate_render_target,
    release_targets_on_error,
    take_retired_target,
)
from .texture_manager import WgpuTextureManager
from .three_d_renderer import Wgpu3DLayerRenderer
from .transform_processor import WgpuTransformProcessor
from .types import (
    WgpuRenderMetrics,
    WgpuRenderTargetHandle,
    WgpuTextureUploadReport,
    WgpuViewParams,
)
from .vector.renderer import WgpuVectorRenderer, lod_bucket_for_scale

# AI Effect layers keep a distinct layer_type for state/persistence, but their
# committed preview is an image-backed layer and must follow normal z-order.
_RASTER_LIKE_LAYER_TYPES = frozenset({"raster", "text", "vector", "ai_effect"})


def is_cpu_only_filter_layer(layer: Any) -> bool:
    """Return whether *layer* is an filter layer wgpu cannot render.

    Every built-in filter has a WGSL implementation; a plugin filter without
    one still needs the CPU flatten, so its subtree keeps taking that path.
    """

    if str(getattr(layer, "layer_type", "raster")) != "effect":
        return False
    if not getattr(layer, "visible", True):
        return False
    filter_type = getattr(layer, "filter_type", getattr(layer, "effect_type", None))
    if not filter_type:
        return False
    return not WgpuFilterProcessor.can_apply(filter_type)


@dataclass(slots=True)
class _LayerPassResult:
    current_target: WgpuRenderTargetHandle
    owned_targets: list[WgpuRenderTargetHandle]
    current_ids: set[str]
    reports: list[WgpuTextureUploadReport]
    warnings: list[str]
    layers_drawn: int


@dataclass(slots=True)
class _TargetDrawResult:
    current_target: WgpuRenderTargetHandle
    spare_target: WgpuRenderTargetHandle | None
    owned_targets: list[WgpuRenderTargetHandle]
    current_ids: set[str]
    reports: list[WgpuTextureUploadReport]
    warnings: list[str]
    layers_drawn: int


class _ClipRunMember:
    """Delegating view of a layer while it is composited inside a clipping run.

    The clipping run draws its members into an offscreen group target through
    the ordinary layer-draw passes; the overrides neutralize the attributes
    that only make sense against the final canvas (``clipping``, the base's
    ``blend_mode``) and tag clip members for source-atop compositing.
    """

    __slots__ = ("_layer", "_overrides")

    def __init__(self, layer: Any, **overrides: Any) -> None:
        object.__setattr__(self, "_layer", layer)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_layer"), name)


class WgpuCompositor:
    """Compose simple top-level raster scenes into an offscreen target."""

    MAX_WGPU_GROUP_DEPTH = 8

    def __init__(
        self,
        bundle: Any,
        *,
        texture_manager: WgpuTextureManager | None = None,
        shader_library: WgpuShaderLibrary | None = None,
        filter_processor: WgpuFilterProcessor | None = None,
        effect_processor: WgpuFilterProcessor | None = None,
        transform_processor: WgpuTransformProcessor | None = None,
        three_d_renderer: Wgpu3DLayerRenderer | None = None,
        vector_renderer: WgpuVectorRenderer | None = None,
        style_pipeline_store: Any = None,
    ) -> None:
        if (
            filter_processor is not None
            and effect_processor is not None
            and filter_processor is not effect_processor
        ):
            raise ValueError("filter_processor and effect_processor must be the same object.")
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self.texture_manager = texture_manager or WgpuTextureManager(bundle)
        self._mask_texture_manager = WgpuMaskTextureManager(bundle)
        self._group_composite_cache = GroupCompositeCache()
        self.shader_library = shader_library or WgpuShaderLibrary(bundle)
        selected_processor = filter_processor if filter_processor is not None else effect_processor
        self.filter_processor = (
            selected_processor if selected_processor is not None else WgpuFilterProcessor(bundle)
        )
        self.transform_processor = transform_processor or WgpuTransformProcessor()
        self.three_d_renderer = three_d_renderer or Wgpu3DLayerRenderer(bundle)
        self.vector_renderer = vector_renderer or WgpuVectorRenderer(bundle)
        self._retired_target_batches: list[list[WgpuRenderTargetHandle]] = []
        self._encoder_scratch: EncoderScratchTargets | None = None
        self._alpha8_masks: dict[str, tuple[int, QImage]] = {}
        self.three_d_display_scale = 1.0
        self._screen_space_vector_layer_id: str | None = None
        self._view: WgpuViewParams | None = None
        self._view_inverse: tuple[float, float, float, float, float, float] | None = None
        self.layer_style_processor = WgpuLayerStyleProcessor(
            bundle, pipeline_store=style_pipeline_store
        )
        self._filter_prefix_renderer = WgpuFilterPrefixRenderer(
            bundle, style_pipeline_store=self.layer_style_processor.pipeline_store
        )

    @property
    def effect_processor(self) -> WgpuFilterProcessor:
        """Compatibility property for integrations using the Effect name."""
        return self.filter_processor

    @effect_processor.setter
    def effect_processor(self, value: WgpuFilterProcessor) -> None:
        self.filter_processor = value

    @property
    def _effect_prefix_renderer(self) -> WgpuFilterPrefixRenderer:
        return self._filter_prefix_renderer

    @_effect_prefix_renderer.setter
    def _effect_prefix_renderer(self, value: WgpuFilterPrefixRenderer) -> None:
        self._filter_prefix_renderer = value

    def set_three_d_display_scale(self, scale: float) -> None:
        self.three_d_display_scale = self._clamped_three_d_display_scale(scale)

    def render(
        self,
        scene: RenderSceneSnapshot,
        *,
        invalidation: RenderInvalidation | None = None,
        screen_space_vector_layer_id: str | None = None,
        view: WgpuViewParams | None = None,
    ) -> tuple[WgpuRenderTargetHandle, tuple[str, ...], WgpuRenderMetrics]:
        # A layer claimed by the presenter's display-resolution vector pass
        # is skipped here and drawn over the projected frame instead.
        self._screen_space_vector_layer_id = screen_space_vector_layer_id
        # View-resolution compositing: composite at target (viewport) pixels
        # instead of canvas pixels.  Degenerate views fall back to canvas.
        self._view = view if view is not None and view.inverse() is not None else None
        self._view_inverse = self._view.inverse() if self._view is not None else None
        self._style_backdrop_keys = backdrop_keys(scene)
        self._encoder_scratch = EncoderScratchTargets()
        self._filter_prefix_renderer.begin_frame()
        composite_width, composite_height = self._composite_size(scene)
        target = self.create_target(composite_width, composite_height)
        warnings: list[str] = []
        reports: list[WgpuTextureUploadReport] = []
        layers_drawn = 0
        full_frame_fallbacks = 0
        draw_layers: tuple[Any, ...] = scene.layers
        clear_rgba = scene.canvas_state.background_rgba

        if self._requires_bitmap_flatten(scene):
            flattened, fallback_warnings = self._flatten_scene_to_qimage(scene)
            warnings.extend(fallback_warnings)
            draw_layers = (
                SimpleNamespace(
                    id="__wgpu_flattened_scene__",
                    image=flattened,
                    visible=True,
                    opacity=1.0,
                    blend_mode="normal",
                    layer_type="raster",
                    texture_version=self._flattened_texture_version(scene),
                    offset_x=0,
                    offset_y=0,
                ),
            )
            clear_rgba = (0, 0, 0, 0)
            full_frame_fallbacks = 1

        if full_frame_fallbacks:
            warnings.extend(self._bitmap_flatten_diagnostics(scene))

        encoder = self._device.create_command_encoder()
        self._clear_target(encoder, target, clear_rgba)
        active_layer_ids = {layer.id for layer in scene.flat_layers}
        pass_result: _LayerPassResult | None = None
        try:
            pass_result = self._draw_layer_passes(
                encoder,
                target,
                scene,
                draw_layers,
                invalidation,
            )
            self._queue.submit([encoder.finish()])
        except Exception:
            finish_render_caches(self, active_layer_ids, submitted=False)
            self._filter_prefix_renderer.finish_frame()
            if pass_result is not None:
                for owned_target in pass_result.owned_targets:
                    self._destroy_target(owned_target)
            raise
        finally:
            self._encoder_scratch = None
        finish_render_caches(self, active_layer_ids, submitted=True)

        self._retire_targets(
            [
                owned_target
                for owned_target in pass_result.owned_targets
                if owned_target is not pass_result.current_target
            ]
        )
        self._filter_prefix_renderer.finish_frame()
        warnings.extend(pass_result.warnings)
        reports.extend(pass_result.reports)
        layers_drawn += pass_result.layers_drawn
        self.texture_manager.remove_stale(pass_result.current_ids)
        self._mask_texture_manager.remove_stale(pass_result.current_ids)
        self.three_d_renderer.remove_stale(pass_result.current_ids)
        self.vector_renderer.remove_stale(pass_result.current_ids)
        metrics = WgpuRenderMetrics(
            layers_seen=len(scene.flat_layers),
            layers_drawn=layers_drawn,
            full_uploads=sum(1 for report in reports if report.uploaded and not report.partial),
            partial_uploads=sum(1 for report in reports if report.partial),
            partial_upload_fallbacks=sum(1 for report in reports if report.fallback_full_upload),
            texture_uploads=tuple(reports),
            full_frame_fallbacks=full_frame_fallbacks,
        )
        return pass_result.current_target, tuple(warnings), metrics

    def create_target(self, width: int, height: int) -> WgpuRenderTargetHandle:
        if self._encoder_scratch is not None:
            reused = self._encoder_scratch.take(int(width), int(height))
            if reused is not None:
                return reused
        reused = take_retired_target(
            self._retired_target_batches,
            int(width),
            int(height),
            required_usage=int(self._wgpu.TextureUsage.RENDER_ATTACHMENT),
            required_format="rgba8unorm",
        )
        if reused is not None:
            return self._encoder_scratch.register(reused) if self._encoder_scratch else reused
        target = allocate_render_target(self._bundle, width, height)
        return self._encoder_scratch.register(target) if self._encoder_scratch else target

    def _create_retained_target(self, width: int, height: int) -> WgpuRenderTargetHandle:
        return allocate_render_target(self._bundle, width, height)

    def _blend_pipeline_for(self, layers: tuple[Any, ...]) -> Any:
        def normal_stack(nodes: Any, depth: int) -> bool:
            if depth > self.MAX_WGPU_GROUP_DEPTH:
                return False
            return all(
                self._blend_mode_name(getattr(node, "blend_mode", "normal"))
                in ("normal", "pass_through")
                and normal_stack(getattr(node, "children", ()), depth + 1)
                for node in nodes
            )

        specialized = getattr(self.shader_library, "_normal_layer_blend_pipeline", None)
        if callable(specialized) and normal_stack(layers, 0):
            return specialized()
        return self.shader_library.get_layer_blend_pipeline()

    def _draw_layer_passes(
        self,
        encoder: Any,
        initial_target: WgpuRenderTargetHandle,
        scene: RenderSceneSnapshot,
        draw_layers: tuple[Any, ...],
        invalidation: RenderInvalidation | None,
        *,
        depth: int = 0,
    ) -> _LayerPassResult:
        current_target = initial_target
        spare_target: WgpuRenderTargetHandle | None = None
        owned_targets = [initial_target]
        current_ids: set[str] = set()
        reports: list[WgpuTextureUploadReport] = []
        warnings: list[str] = []
        layers_drawn = 0

        def merge(draw_result: _TargetDrawResult) -> None:
            nonlocal current_target, spare_target, layers_drawn
            current_target, spare_target = self._merge_draw_result(
                draw_result,
                owned_targets=owned_targets,
                current_ids=current_ids,
                reports=reports,
                warnings=warnings,
            )
            layers_drawn += draw_result.layers_drawn

        try:
            layer_list = tuple(draw_layers)
            pipeline = self._blend_pipeline_for(layer_list)
            index = 0
            if self._view is not None:
                prefix = self._filter_prefix_renderer.render(
                    scene,
                    layer_list,
                    depth=depth,
                    max_depth=self.MAX_WGPU_GROUP_DEPTH,
                    display_scale=self.three_d_display_scale,
                    invalidation=invalidation,
                )
                if prefix is not None:
                    index, projected = prefix
                    current_target = self._filter_prefix_renderer.project(
                        self, encoder, initial_target, projected.target, scene
                    )
                    spare_target = initial_target
                    owned_targets.append(current_target)
                    reports.extend(projected.metrics.texture_uploads)
                    warnings.extend(projected.warnings)
                    # A reused prefix still contributes content to its group.
                    layers_drawn += max(1, projected.metrics.layers_drawn)
            while index < len(layer_list):
                layer = layer_list[index]
                run_base, run_members, run_last = self._collect_clipping_run(layer_list, index)
                if run_members:
                    for member in layer_list[index : run_last + 1]:
                        current_ids.add(member.id)
                    draw_result = self._draw_clipping_run(
                        encoder,
                        pipeline,
                        current_target,
                        spare_target,
                        scene,
                        run_base,
                        run_members,
                        invalidation,
                        depth=depth,
                    )
                    merge(draw_result)
                    index = run_last + 1
                    continue
                index += 1
                current_ids.add(layer.id)
                draw_result = self._dispatch_layer_draw(
                    encoder,
                    pipeline,
                    current_target,
                    spare_target,
                    scene,
                    layer,
                    invalidation,
                    warnings,
                    depth=depth,
                )
                if draw_result is not None:
                    merge(draw_result)
        except Exception:
            for owned_target in owned_targets:
                self._destroy_target(owned_target)
            raise
        return _LayerPassResult(
            current_target=current_target,
            owned_targets=owned_targets,
            current_ids=current_ids,
            reports=reports,
            warnings=warnings,
            layers_drawn=layers_drawn,
        )

    def _dispatch_layer_draw(  # noqa: PLR0911
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
        invalidation: RenderInvalidation | None,
        warnings: list[str],
        *,
        depth: int,
    ) -> _TargetDrawResult | None:
        """Draw one non-run layer via its type-specific pass, or skip it."""

        if self._is_group_drawable(layer):
            return self._draw_group_layer(
                encoder,
                pipeline,
                current_target,
                spare_target,
                scene,
                layer,
                invalidation,
                depth=depth,
            )
        if self._is_vector_direct_drawable(layer) and str(getattr(layer, "id", "")) != (
            self._screen_space_vector_layer_id or ""
        ):
            return self._draw_vector_layer(
                encoder,
                pipeline,
                current_target,
                spare_target,
                scene,
                layer,
            )
        if str(getattr(layer, "layer_type", "raster")) == "effect":
            if not getattr(layer, "visible", True):
                return None
            filter_warnings = self.filter_processor.warnings_for_layer(layer)
            if filter_warnings:
                warnings.extend(filter_warnings)
                return None
            return self._draw_filter_layer(
                encoder,
                current_target,
                spare_target,
                scene,
                layer,
            )
        if not self._is_drawable(layer):
            warnings.extend(self._unsupported_warnings(layer))
            return None
        if str(getattr(layer, "layer_type", "raster")) == "3d":
            return self._draw_3d_layer(
                encoder,
                pipeline,
                current_target,
                spare_target,
                scene,
                layer,
            )
        filter_warnings = self.filter_processor.warnings_for_layer(layer)
        if filter_warnings:
            warnings.extend(filter_warnings)
            return None
        return self._draw_raster_layer(
            encoder,
            pipeline,
            current_target,
            spare_target,
            scene,
            layer,
            invalidation,
        )

    def _collect_clipping_run(
        self,
        layers: tuple[Any, ...],
        start: int,
    ) -> tuple[Any, tuple[Any, ...], int]:
        """Detect a clipping run (base + following clip layers) at *start*.

        Returns ``(base, members, last_index)``; ``members`` is empty when no
        run starts here. Hidden layers do not break the chain — CPU
        compositing filters them before grouping, so a clip after a hidden
        layer still attaches to the same base.
        """

        base = layers[start]
        if bool(getattr(base, "clipping", False)) or not getattr(base, "visible", True):
            return base, (), start
        if str(getattr(base, "layer_type", "raster")) == "effect":
            return base, (), start
        clips: list[Any] = []
        last_index = start
        probe = start + 1
        while probe < len(layers):
            candidate = layers[probe]
            if bool(getattr(candidate, "clipping", False)):
                if getattr(candidate, "visible", True):
                    clips.append(candidate)
                last_index = probe
                probe += 1
                continue
            if not getattr(candidate, "visible", True):
                probe += 1
                continue
            break
        if not clips:
            return base, (), start
        members: list[Any] = [_ClipRunMember(base, clipping=False, blend_mode="normal")]
        members.extend(
            _ClipRunMember(clip, clipping=False, wgpu_atop_composite=True) for clip in clips
        )
        return base, tuple(members), last_index

    def _draw_clipping_run(
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        base: Any,
        members: tuple[Any, ...],
        invalidation: RenderInvalidation | None,
        *,
        depth: int,
    ) -> _TargetDrawResult:
        """Compose a clipping run offscreen, then land it with the base blend.

        Mirrors the CPU clipping group: the base is drawn into a transparent
        target with its own mask and opacity, every clip lands source-atop
        (never exceeding the base's final alpha), and the finished run blends
        onto the canvas once with the base's blend mode at full opacity.
        """

        warnings: list[str] = []
        if depth >= self.MAX_WGPU_GROUP_DEPTH:
            warnings.append(
                f"layer {getattr(base, 'id', '<unknown>')}: wgpu clipping depth limit exceeded"
            )
            return self._unchanged_draw(current_target, spare_target, warnings=warnings)

        composite_width, composite_height = self._composite_size(scene)
        run_initial = self.create_target(composite_width, composite_height)
        try:
            self._clear_target(encoder, run_initial, (0, 0, 0, 0))
        except Exception:
            self._destroy_target(run_initial)
            raise

        with clip_backdrop_scope(self, members, current_target):
            run_result = self._draw_layer_passes(
                encoder,
                run_initial,
                scene,
                members,
                invalidation,
                depth=depth + 1,
            )
        owned_targets = list(run_result.owned_targets)
        if run_result.layers_drawn <= 0:
            return _TargetDrawResult(
                current_target=current_target,
                spare_target=spare_target,
                owned_targets=owned_targets,
                current_ids=run_result.current_ids,
                reports=run_result.reports,
                warnings=[*run_result.warnings, *warnings],
                layers_drawn=0,
            )

        if spare_target is None:
            spare_target = self.create_target(composite_width, composite_height)
            owned_targets.append(spare_target)
        bind_group = self.shader_library.create_blend_bind_group(
            current_target,
            run_result.current_target,
            canvas_size=(float(composite_width), float(composite_height)),
            dest_origin=(0.0, 0.0),
            dest_size=(float(composite_width), float(composite_height)),
            uv_origin=(0.0, 0.0),
            uv_size=(1.0, 1.0),
            # The base's opacity and layer mask are already inside the run;
            # the run lands once with the base's blend mode (CPU parity).
            opacity=1.0,
            blend_mode=self._blend_mode_name(getattr(base, "blend_mode", "normal")),
            src_premultiplied=True,
            **self._view_blend_kwargs(scene, src_in_target_space=True),
        )
        self._draw_blend_pass(encoder, pipeline, spare_target, bind_group)
        return _TargetDrawResult(
            current_target=spare_target,
            spare_target=current_target,
            owned_targets=owned_targets,
            current_ids=run_result.current_ids,
            reports=run_result.reports,
            warnings=[*run_result.warnings, *warnings],
            layers_drawn=run_result.layers_drawn,
        )

    def _draw_group_layer(
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
        invalidation: RenderInvalidation | None,
        *,
        depth: int,
    ) -> _TargetDrawResult:
        warnings: list[str] = []
        if depth >= self.MAX_WGPU_GROUP_DEPTH:
            warnings.append(
                f"layer {getattr(layer, 'id', '<unknown>')}: wgpu group depth limit exceeded"
            )
            return self._unchanged_draw(current_target, spare_target, warnings=warnings)

        children = tuple(getattr(layer, "children", []) or ())
        if not children:
            return self._unchanged_draw(current_target, spare_target)

        composite_width, composite_height = self._composite_size(scene)
        region = group_target_region(self, scene, children)
        if style_signature(layer):
            region = (0, 0, scene.width, scene.height)
        cache_key = group_composite_key(
            self, layer, scene, region, depth=depth, mask_upload_policy=nearest_mask_upload_region
        )
        group_result = (
            self._group_composite_cache.lookup(cache_key) if cache_key is not None else None
        )
        if group_result is None:
            group_initial = self.create_target(region[2], region[3])
            try:
                self._clear_target(encoder, group_initial, (0, 0, 0, 0))
            except Exception:
                self._destroy_target(group_initial)
                raise
            with (
                document_group_view(self, bool(style_signature(layer))),
                group_region_view(self, region, scene),
            ):
                group_result = self._draw_layer_passes(
                    encoder, group_initial, scene, children, invalidation, depth=depth + 1
                )
            if cache_key is not None:
                self._group_composite_cache.stage(cache_key, group_result, self, encoder)
        owned_targets = list(group_result.owned_targets)
        with (
            release_targets_on_error(self, owned_targets),
            group_mask_upload_scope(self, layer, region, scene),
        ):
            mask_texture, mask_report = self._sync_mask_texture(layer)
            reports = list(group_result.reports)
            current_ids = set(group_result.current_ids)
            if mask_report is not None:
                reports.append(mask_report)
                current_ids.add(mask_report.layer_id)
            if group_result.layers_drawn <= 0:
                if self._encoder_scratch is not None:
                    self._encoder_scratch.retire(
                        group_result.owned_targets, keep=(current_target, spare_target)
                    )
                return _TargetDrawResult(
                    current_target=current_target,
                    spare_target=spare_target,
                    owned_targets=owned_targets,
                    current_ids=current_ids,
                    reports=reports,
                    warnings=group_result.warnings,
                    layers_drawn=0,
                )

            if spare_target is None:
                spare_target = self.create_target(composite_width, composite_height)
                owned_targets.append(spare_target)
            backdrop = style_backdrop(self, scene, layer, current_target, group=True)
            group_texture, padding = self.layer_style_processor.apply(
                encoder, group_result.current_target, layer, premultiplied=True, backdrop=backdrop
            )
            if backdrop is not None:
                region = (0, 0, composite_width, composite_height)
            bind_group = self.shader_library.create_blend_bind_group(
                current_target,
                group_texture,
                canvas_size=(float(composite_width), float(composite_height)),
                dest_origin=(float(region[0] - padding), float(region[1] - padding)),
                dest_size=(float(region[2] + 2 * padding), float(region[3] + 2 * padding)),
                uv_origin=(0.0, 0.0),
                uv_size=(1.0, 1.0),
                opacity=self._clamped_opacity(getattr(layer, "opacity", 1.0)),
                blend_mode=self._blend_mode_name(getattr(layer, "blend_mode", "normal")),
                mask_texture=mask_texture,
                mask_nearest=bool(getattr(layer, "mask_nearest", False)),
                atop_composite=bool(getattr(layer, "wgpu_atop_composite", False)),
                # Group results are composite render targets: premultiplied.
                src_premultiplied=group_texture is group_result.current_target,
                # Group results live in the composite target's own space.
                **dict(
                    self._view_blend_kwargs(scene),
                    src_in_target_space=backdrop is not None or not bool(style_signature(layer)),
                ),
            )
            self._draw_blend_pass(encoder, pipeline, spare_target, bind_group)
            if self._encoder_scratch is not None:
                self._encoder_scratch.retire(
                    group_result.owned_targets, keep=(current_target, spare_target)
                )
            return _TargetDrawResult(
                current_target=spare_target,
                spare_target=current_target,
                owned_targets=owned_targets,
                current_ids=current_ids,
                reports=reports,
                warnings=group_result.warnings,
                layers_drawn=group_result.layers_drawn + 1,
            )

    def _draw_raster_layer(
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
        invalidation: RenderInvalidation | None,
    ) -> _TargetDrawResult:
        texture, report, upload_warnings = self.texture_manager.sync_layer_texture(
            layer,
            invalidation=invalidation,
            active_layer_id=scene.active_layer_id,
        )
        filter_result = self._apply_layer_filters(
            encoder,
            texture,
            layer,
            document_dpi=self._scene_document_dpi(scene),
            raster_context=FilterRasterContext.from_layer(layer, (scene.width, scene.height)),
        )
        source_texture = filter_result.texture
        # View-resolution compositing C2: prescaled vector/text rasters hold
        # the canvas-space rectangle at ``raster_origin`` at scale-x texels.
        # All canvas-space placement math (and the shader's normalized UVs)
        # works on the LOGICAL size; the origin shifts the placement.
        prescale = max(1, int(getattr(layer, "vector_raster_scale", 1) or 1))
        raster_origin = tuple(getattr(layer, "vector_raster_origin", (0, 0)) or (0, 0))
        backdrop = style_backdrop(
            self, scene, layer, current_target, scale=prescale, origin=raster_origin
        )
        source_texture, style_padding = self.layer_style_processor.apply(
            encoder,
            source_texture,
            layer,
            scale=prescale,
            backdrop=backdrop,
            reference=(
                (
                    int(raster_origin[0]) * prescale,
                    int(raster_origin[1]) * prescale,
                    scene.width * prescale,
                    scene.height * prescale,
                )
                if layer.layer_type in {"text", "vector"}
                else None
            ),
            dirty=(
                self.texture_manager._layer_upload_rect(
                    layer, invalidation.dirty_rect, (texture.width, texture.height)
                )
                if report.partial and invalidation is not None and source_texture is texture
                else None
            ),
        )
        logical_width = -(-source_texture.width // prescale)
        logical_height = -(-source_texture.height // prescale)
        region = self.transform_processor.compute_draw_region(
            layer,
            canvas_width=scene.width,
            canvas_height=scene.height,
            texture_width=logical_width,
            texture_height=logical_height,
            extra_offset=(
                int(raster_origin[0]) - style_padding // prescale,
                int(raster_origin[1]) - style_padding // prescale,
            ),
        )
        if backdrop is not None:
            region = backdrop_region(backdrop)
        if bool(getattr(layer, "mask_nearest", False)) and backdrop is None:
            mask_texture, mask_report = self._sync_mask_texture(
                layer, region=region, canvas_size=(scene.width, scene.height)
            )
        else:
            mask_texture, mask_report = self._sync_mask_texture(layer)
        reports = [report]
        current_ids: set[str] = set()
        if mask_report is not None:
            reports.append(mask_report)
            current_ids.add(mask_report.layer_id)
        if region is None:
            return self._unchanged_draw(
                current_target,
                spare_target,
                current_ids=current_ids,
                reports=reports,
                warnings=list(upload_warnings),
            )

        composite_width, composite_height = self._composite_size(scene)
        owned_targets: list[WgpuRenderTargetHandle] = list(filter_result.owned_targets)
        if spare_target is None:
            spare_target = self.create_target(composite_width, composite_height)
            owned_targets.append(spare_target)
        bind_group = self.shader_library.create_blend_bind_group(
            current_target,
            source_texture,
            canvas_size=(float(composite_width), float(composite_height)),
            dest_origin=region.dest_origin,
            dest_size=region.dest_size,
            uv_origin=region.uv_origin,
            uv_size=region.uv_size,
            src_size=region.src_size,
            inverse_affine=region.inverse_affine,
            opacity=self._clamped_opacity(getattr(layer, "opacity", 1.0)),
            blend_mode=self._blend_mode_name(getattr(layer, "blend_mode", "normal")),
            mask_texture=mask_texture,
            mask_nearest=bool(getattr(layer, "mask_nearest", False)),
            atop_composite=bool(getattr(layer, "wgpu_atop_composite", False)),
            # Raster textures stay canvas-resolution; placement math runs in
            # canvas space via the view inverse.
            src_prescaled=prescale > 1 and backdrop is None,
            **self._view_blend_kwargs(scene, src_in_target_space=backdrop is not None),
        )
        self._draw_blend_pass(encoder, pipeline, spare_target, bind_group)
        return _TargetDrawResult(
            current_target=spare_target,
            spare_target=current_target,
            owned_targets=owned_targets,
            current_ids=current_ids,
            reports=reports,
            warnings=list(upload_warnings),
            layers_drawn=1,
        )

    def _draw_vector_layer(
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
    ) -> _TargetDrawResult:
        composite_width, composite_height = self._composite_size(scene)
        view = self._view
        if view is not None:
            # View-resolution compositing: draw retained geometry straight at
            # target resolution (sharp at any zoom, LOD from the view scale).
            payload = getattr(layer, "vector_render_layer", None)
            layer_transform = getattr(payload, "layer_transform", None) or (
                1.0,
                0.0,
                0.0,
                1.0,
                0.0,
                0.0,
            )
            result = self.vector_renderer.render_layer(
                encoder,
                layer,
                width=composite_width,
                height=composite_height,
                create_target=self._create_retained_target,
                transform_override=self._compose_affine(view.affine, layer_transform),
                lod_bucket=lod_bucket_for_scale(view.scale()),
            )
        else:
            result = self.vector_renderer.render_layer(
                encoder,
                layer,
                width=composite_width,
                height=composite_height,
                create_target=self._create_retained_target,
            )
        if result is None:
            return self._unchanged_draw(
                current_target,
                spare_target,
                warnings=[
                    f"layer {getattr(layer, 'id', '<unknown>')}: "
                    "direct vector layer could not be rendered by wgpu"
                ],
            )

        mask_texture, mask_report = self._sync_mask_texture(layer)
        reports: list[WgpuTextureUploadReport] = []
        current_ids: set[str] = set()
        if mask_report is not None:
            reports.append(mask_report)
            current_ids.add(mask_report.layer_id)

        source_texture = result.texture
        filter_result = self._apply_layer_filters(
            encoder, source_texture, layer, document_dpi=self._scene_document_dpi(scene)
        )
        source_texture = filter_result.texture

        owned_targets: list[WgpuRenderTargetHandle] = list(result.owned_targets)
        self._extend_owned_targets(owned_targets, list(filter_result.owned_targets))
        if spare_target is None:
            spare_target = self.create_target(composite_width, composite_height)
            owned_targets.append(spare_target)

        bind_group = self.shader_library.create_blend_bind_group(
            current_target,
            source_texture,
            canvas_size=(float(composite_width), float(composite_height)),
            dest_origin=(0.0, 0.0),
            dest_size=(float(composite_width), float(composite_height)),
            uv_origin=(0.0, 0.0),
            uv_size=(1.0, 1.0),
            src_size=(float(source_texture.width), float(source_texture.height)),
            opacity=self._clamped_opacity(getattr(layer, "opacity", 1.0)),
            blend_mode=self._blend_mode_name(getattr(layer, "blend_mode", "normal")),
            mask_texture=mask_texture,
            mask_nearest=bool(getattr(layer, "mask_nearest", False)),
            atop_composite=bool(getattr(layer, "wgpu_atop_composite", False)),
            # In view mode the vector layer target is already target-space.
            **self._view_blend_kwargs(scene, src_in_target_space=True),
        )
        self._draw_blend_pass(encoder, pipeline, spare_target, bind_group)
        return _TargetDrawResult(
            current_target=spare_target,
            spare_target=current_target,
            owned_targets=owned_targets,
            current_ids=current_ids,
            reports=reports,
            warnings=list(result.warnings),
            layers_drawn=1,
        )

    def _draw_3d_layer(
        self,
        encoder: Any,
        pipeline: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
    ) -> _TargetDrawResult:
        result = self.three_d_renderer.render_layer(
            encoder,
            layer,
            width=self._three_d_render_width(scene.width),
            height=self._three_d_render_height(scene.height),
            create_target=self.create_target,
        )
        if result is None:
            return self._unchanged_draw(
                current_target,
                spare_target,
                warnings=[
                    f"layer {getattr(layer, 'id', '<unknown>')}: "
                    "3D scene could not be rendered by wgpu"
                ],
            )

        mask_texture, mask_report = self._sync_mask_texture(layer)
        reports: list[WgpuTextureUploadReport] = []
        current_ids: set[str] = set()
        if mask_report is not None:
            reports.append(mask_report)
            current_ids.add(mask_report.layer_id)

        source_texture = result.texture
        filter_result = self._apply_layer_filters(
            encoder, source_texture, layer, document_dpi=self._scene_document_dpi(scene)
        )
        source_texture = filter_result.texture

        composite_width, composite_height = self._composite_size(scene)
        owned_targets: list[WgpuRenderTargetHandle] = list(result.owned_targets)
        self._extend_owned_targets(owned_targets, list(filter_result.owned_targets))
        if spare_target is None:
            spare_target = self.create_target(composite_width, composite_height)
            owned_targets.append(spare_target)

        bind_group = self.shader_library.create_blend_bind_group(
            current_target,
            source_texture,
            canvas_size=(float(composite_width), float(composite_height)),
            dest_origin=(
                float(getattr(layer, "offset_x", 0)),
                float(getattr(layer, "offset_y", 0)),
            ),
            dest_size=(float(scene.width), float(scene.height)),
            uv_origin=(0.0, 0.0),
            uv_size=(1.0, 1.0),
            src_size=(float(source_texture.width), float(source_texture.height)),
            opacity=self._clamped_opacity(getattr(layer, "opacity", 1.0)),
            blend_mode=self._blend_mode_name(getattr(layer, "blend_mode", "normal")),
            mask_texture=mask_texture,
            mask_nearest=bool(getattr(layer, "mask_nearest", False)),
            atop_composite=bool(getattr(layer, "wgpu_atop_composite", False)),
            # 3D targets are canvas-space content (canvas-rect placement).
            **self._view_blend_kwargs(scene, src_in_target_space=False),
        )
        self._draw_blend_pass(encoder, pipeline, spare_target, bind_group)
        return _TargetDrawResult(
            current_target=spare_target,
            spare_target=current_target,
            owned_targets=owned_targets,
            current_ids=current_ids,
            reports=reports,
            warnings=list(result.warnings),
            layers_drawn=1,
        )

    @staticmethod
    def _scene_document_dpi(scene: RenderSceneSnapshot) -> float | None:
        """Document DPI for scale-aware filters (CT-02), ``None`` when unknown."""
        try:
            dpi = float(scene.canvas_state.dpi)
        except (AttributeError, TypeError, ValueError):
            return None
        return dpi if dpi >= 1.0 else None

    @staticmethod
    def _visible_child_filters(layer: Any) -> list[Any]:
        """Return the filter layers parented to *layer*, in tree order."""

        return [
            child
            for child in (getattr(layer, "children", []) or [])
            if str(getattr(child, "layer_type", "raster")) == "effect"
            and getattr(child, "visible", True)
            and getattr(child, "filter_type", getattr(child, "effect_type", None))
        ]

    def _apply_layer_filters(
        self,
        encoder: Any,
        texture: Any,
        layer: Any,
        *,
        document_dpi: float | None = None,
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        """Apply the layer's own filter, then chain its child filter layers.

        Mirrors ``CanvasMergeFlattenService.get_filtered_image``: child filters
        apply to the layer's own pixels only, in tree order, before the layer
        is blended into the composite.  ``document_dpi`` and the layer's
        canvas offset reach scale-aware filters (CT-02 screentone).
        """

        layer_origin = (
            int(getattr(layer, "offset_x", 0) or 0),
            int(getattr(layer, "offset_y", 0) or 0),
        )
        result = self.filter_processor.apply_layer_filter(
            encoder,
            texture,
            layer,
            create_target=self.create_target,
            document_dpi=document_dpi,
            layer_origin=layer_origin,
            raster_context=raster_context,
        )
        source = result.texture
        owned: list[WgpuRenderTargetHandle] = list(result.owned_targets)
        chain_version = int(getattr(texture, "texture_version", -1))
        for child in self._visible_child_filters(layer):
            child_result = self.filter_processor.apply_layer_filter(
                encoder,
                source,
                child,
                create_target=self.create_target,
                source_version=chain_version,
                document_dpi=document_dpi,
                layer_origin=layer_origin,
                raster_context=raster_context,
            )
            if child_result.texture is source:
                continue
            self._extend_owned_targets(owned, list(child_result.owned_targets))
            source = child_result.texture
            chain_version = self.filter_processor.chain_version(
                chain_version, child, document_dpi=document_dpi
            )
        return WgpuFilterResult(texture=source, owned_targets=owned)

    def _draw_filter_layer(
        self,
        encoder: Any,
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        scene: RenderSceneSnapshot,
        layer: Any,
    ) -> _TargetDrawResult:
        """Apply a sibling filter layer to everything composited below it.

        The input is the running composite target, whose content identity is
        not knowable here, so the filter cache is bypassed and the result is
        blitted back into the ping-pong pair.
        """

        composite_width, composite_height = self._composite_size(scene)
        filter_result = self.filter_processor.apply_layer_filter(
            encoder,
            current_target,
            layer,
            create_target=self.create_target,
            use_cache=False,
            document_dpi=self._scene_document_dpi(scene),
        )
        if filter_result.texture is current_target:
            return self._unchanged_draw(current_target, spare_target)

        owned_targets: list[WgpuRenderTargetHandle] = list(filter_result.owned_targets)
        if spare_target is None:
            spare_target = self.create_target(composite_width, composite_height)
            owned_targets.append(spare_target)
        encoder.copy_texture_to_texture(
            {"texture": filter_result.texture.texture, "origin": (0, 0, 0)},
            {"texture": spare_target.texture, "origin": (0, 0, 0)},
            (composite_width, composite_height, 1),
        )
        return _TargetDrawResult(
            current_target=spare_target,
            spare_target=current_target,
            owned_targets=owned_targets,
            current_ids=set(),
            reports=[],
            warnings=[],
            layers_drawn=1,
        )

    @staticmethod
    def _unchanged_draw(
        current_target: WgpuRenderTargetHandle,
        spare_target: WgpuRenderTargetHandle | None,
        *,
        current_ids: set[str] | None = None,
        reports: list[WgpuTextureUploadReport] | None = None,
        warnings: list[str] | None = None,
    ) -> _TargetDrawResult:
        return _TargetDrawResult(
            current_target=current_target,
            spare_target=spare_target,
            owned_targets=[],
            current_ids=current_ids or set(),
            reports=reports or [],
            warnings=warnings or [],
            layers_drawn=0,
        )

    def _merge_draw_result(
        self,
        draw_result: _TargetDrawResult,
        *,
        owned_targets: list[WgpuRenderTargetHandle],
        current_ids: set[str],
        reports: list[WgpuTextureUploadReport],
        warnings: list[str],
    ) -> tuple[WgpuRenderTargetHandle, WgpuRenderTargetHandle | None]:
        self._extend_owned_targets(owned_targets, draw_result.owned_targets)
        current_ids.update(draw_result.current_ids)
        reports.extend(draw_result.reports)
        warnings.extend(draw_result.warnings)
        return (
            draw_result.current_target,
            draw_result.spare_target,
        )

    def _sync_mask_texture(
        self,
        layer: Any,
        *,
        region=None,
        canvas_size=None,
    ) -> tuple[Any | None, WgpuTextureUploadReport | None]:
        request = getattr(self, "_mask_region_request", None)
        if region is None and request is not None and request[0] is layer:
            _, region, canvas_size = request
        mask = getattr(layer, "layer_mask", None)
        if mask is None or mask.isNull() or not bool(getattr(layer, "mask_enabled", True)):
            return None, None
        mask_version = int(
            getattr(
                layer,
                "filter_mask_texture_version",
                getattr(
                    layer,
                    "effect_mask_texture_version",
                    getattr(layer, "filter_mask_version", getattr(layer, "effect_mask_version", 0)),
                ),
            )
        )
        if mask_version <= 0:
            # Keep the full cacheKey entropy: the high half is the image
            # serial number, so masking to 31 bits collides for two freshly
            # created images (both end up as 1).
            mask_version = int(mask.cacheKey() & 0x7FFFFFFFFFFFFFFF)
        mask_id = f"__wgpu_mask__:{getattr(layer, 'id', '<unknown>')}"
        # Canonicalize the grayscale mask into Alpha8 coverage so the shader
        # can read one-byte coverage. Cache per mask version: the texture
        # manager only re-uploads on a version change, and the conversion
        # must not run every frame either.
        cached = self._alpha8_masks.get(mask_id)
        if cached is not None and cached[0] == mask_version:
            alpha8_mask = cached[1]
        else:
            if len(self._alpha8_masks) >= 256:
                self._alpha8_masks.clear()
            alpha8_mask = mask_coverage_alpha8(mask)
            self._alpha8_masks[mask_id] = (mask_version, alpha8_mask)
        crop = (
            nearest_mask_upload_region(
                (alpha8_mask.width(), alpha8_mask.height()), canvas_size, region
            )
            if canvas_size is not None and region is not None
            else None
        )
        texture, report = self._mask_texture_manager.sync(
            mask_id, alpha8_mask, mask_version, region=crop
        )
        return texture, report

    @staticmethod
    def _extend_owned_targets(
        owned_targets: list[WgpuRenderTargetHandle],
        new_targets: list[WgpuRenderTargetHandle],
    ) -> None:
        known_ids = {id(target) for target in owned_targets}
        for target in new_targets:
            target_id = id(target)
            if target_id in known_ids:
                continue
            owned_targets.append(target)
            known_ids.add(target_id)

    def _draw_blend_pass(
        self,
        encoder: Any,
        pipeline: Any,
        target: WgpuRenderTargetHandle,
        bind_group: Any,
    ) -> None:
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": target.view,
                    "resolve_target": None,
                    "clear_value": (0.0, 0.0, 0.0, 0.0),
                    "load_op": self._wgpu.LoadOp.clear,
                    "store_op": self._wgpu.StoreOp.store,
                }
            ]
        )
        render_pass.set_pipeline(pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()

    def _three_d_render_width(self, canvas_width: int) -> int:
        return max(1, round(float(canvas_width) * self.three_d_display_scale))

    def _three_d_render_height(self, canvas_height: int) -> int:
        return max(1, round(float(canvas_height) * self.three_d_display_scale))

    @staticmethod
    def _clamped_three_d_display_scale(scale: float) -> float:
        value = max(0.25, min(1.0, float(scale)))
        return 1.0 if value >= 0.95 else value

    @staticmethod
    def _is_drawable(layer: Any) -> bool:
        if not getattr(layer, "visible", True):
            return False
        if float(getattr(layer, "opacity", 1.0)) <= 0.0:
            return False
        layer_type = str(getattr(layer, "layer_type", "raster"))
        blend_mode = WgpuCompositor._blend_mode_name(getattr(layer, "blend_mode", "normal"))
        if not WgpuShaderLibrary.is_blend_mode_enabled(blend_mode):
            return False
        if layer_type == "3d":
            return getattr(layer, "scene_3d", None) is not None
        if layer_type not in _RASTER_LIKE_LAYER_TYPES:
            return False
        return (
            getattr(layer, "image", None) is not None
            or getattr(layer, "native_display", None) is not None
        )

    @staticmethod
    def _is_vector_direct_drawable(layer: Any) -> bool:
        # Text layers join the direct path in V5 (glyph-run rendering).
        if str(getattr(layer, "layer_type", "raster")) not in {"vector", "text"}:
            return False
        if not getattr(layer, "vector_direct_ready", False):
            return False
        if getattr(layer, "vector_render_layer", None) is None:
            return False
        if not getattr(layer, "visible", True):
            return False
        if float(getattr(layer, "opacity", 1.0)) <= 0.0:
            return False
        blend_mode = WgpuCompositor._blend_mode_name(getattr(layer, "blend_mode", "normal"))
        return WgpuShaderLibrary.is_blend_mode_enabled(blend_mode)

    @staticmethod
    def _is_group_drawable(layer: Any) -> bool:
        if not getattr(layer, "visible", True):
            return False
        if float(getattr(layer, "opacity", 1.0)) <= 0.0:
            return False
        if str(getattr(layer, "layer_type", "raster")) != "group":
            return False
        blend_mode = WgpuCompositor._blend_mode_name(getattr(layer, "blend_mode", "normal"))
        return WgpuShaderLibrary.is_blend_mode_enabled(blend_mode)

    @staticmethod
    def _unsupported_warnings(layer: Any) -> tuple[str, ...]:
        if not getattr(layer, "visible", True):
            return ()
        layer_id = getattr(layer, "id", "<unknown>")
        layer_type = str(getattr(layer, "layer_type", "raster"))
        blend_mode = WgpuCompositor._blend_mode_name(getattr(layer, "blend_mode", "normal"))
        if layer_type == "3d":
            message: str | None = None
            if getattr(layer, "scene_3d", None) is None:
                message = f"layer {layer_id}: 3D layer has no scene_3d payload"
            elif not WgpuShaderLibrary.is_blend_mode_enabled(blend_mode):
                message = f"layer {layer_id}: unsupported wgpu blend mode {blend_mode!r}"
            return () if message is None else (message,)
        if layer_type in {"text", "vector"} and getattr(layer, "image", None) is None:
            return ()
        if layer_type not in _RASTER_LIKE_LAYER_TYPES:
            return (f"layer {layer_id}: unsupported wgpu layer_type {layer_type!r}",)
        if not WgpuShaderLibrary.is_blend_mode_enabled(blend_mode):
            return (f"layer {layer_id}: unsupported wgpu blend mode {blend_mode!r}",)
        return ()

    def requires_bitmap_flatten(self, scene: RenderSceneSnapshot) -> bool:
        """Read-only probe: would rendering this scene take the full-frame
        CPU flatten fallback?  Live preview callers skip rendering instead
        of paying a full-canvas QImage flatten (UI-PERF-01)."""

        return self._requires_bitmap_flatten(scene)

    def _requires_bitmap_flatten(self, scene: RenderSceneSnapshot) -> bool:
        if scene.selection_preview_image is not None and scene.selection_preview_bounds is not None:
            return True
        return any(self._layer_requires_bitmap_flatten(layer) for layer in scene.layers)

    def _layer_requires_bitmap_flatten(self, layer: Any, *, depth: int = 0) -> bool:
        if not getattr(layer, "visible", True) or float(getattr(layer, "opacity", 1.0)) <= 0:
            return False
        layer_type = str(getattr(layer, "layer_type", "raster"))
        if layer_type == "3d":
            return False
        children = list(getattr(layer, "children", []) or [])
        if is_cpu_only_filter_layer(layer) or any(
            is_cpu_only_filter_layer(child) for child in children
        ):
            return True
        if not WgpuShaderLibrary.is_blend_mode_enabled(
            self._blend_mode_name(getattr(layer, "blend_mode", "normal"))
        ):
            return True
        if layer_type == "group" and children and depth >= self.MAX_WGPU_GROUP_DEPTH:
            return True
        return any(
            self._layer_requires_bitmap_flatten(child, depth=depth + 1) for child in children
        )

    def _clear_target(
        self,
        encoder: Any,
        target: WgpuRenderTargetHandle,
        clear_rgba: tuple[int, int, int, int],
    ) -> None:
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": target.view,
                    "resolve_target": None,
                    "clear_value": self._premultiplied_clear(clear_rgba),
                    "load_op": self._wgpu.LoadOp.clear,
                    "store_op": self._wgpu.StoreOp.store,
                }
            ]
        )
        render_pass.end()

    def _bitmap_flatten_diagnostics(self, scene: RenderSceneSnapshot) -> tuple[str, ...]:
        modes = [
            self._blend_mode_name(getattr(layer, "blend_mode", "normal"))
            for layer in scene.flat_layers
            if getattr(layer, "visible", True)
            and float(getattr(layer, "opacity", 1.0)) > 0.0
            and not WgpuShaderLibrary.is_blend_mode_enabled(
                self._blend_mode_name(getattr(layer, "blend_mode", "normal"))
            )
        ]
        if not modes:
            return ()
        unique_modes = ", ".join(sorted(set(modes)))
        return (
            "wgpu bitmap fallback retained for "
            f"{len(modes)} unsupported blend layer(s): {unique_modes}",
        )

    def _flatten_scene_to_qimage(
        self,
        scene: RenderSceneSnapshot,
    ) -> tuple[QImage, tuple[str, ...]]:
        target = QImage(
            scene.width,
            scene.height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        target.fill(Qt.GlobalColor.transparent)
        background = scene.canvas_state.background
        if background.alpha() > 0:
            painter = QPainter(target)
            painter.fillRect(0, 0, scene.width, scene.height, background)
            painter.end()
        warnings: list[str] = ["wgpu bitmap compositor used full-frame QImage flatten fallback"]
        # Scale-aware CPU filters (CT-02 screentone) read the scene DPI while
        # the flatten runs; the attribute is transient render context only.
        self._flatten_document_dpi = self._scene_document_dpi(scene)
        try:
            self._compose_layer_sequence(
                target,
                list(scene.layers),
                canvas_size=(scene.width, scene.height),
                warnings=warnings,
            )
        finally:
            self._flatten_document_dpi = None
        if scene.selection_preview_image is not None and scene.selection_preview_bounds is not None:
            x, y, _w, _h = scene.selection_preview_bounds
            painter = QPainter(target)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.drawImage(int(x), int(y), scene.selection_preview_image)
            painter.end()
        return target, tuple(warnings)

    def _compose_layer_sequence(
        self,
        target: QImage,
        layers: list[Any],
        *,
        canvas_size: tuple[int, int],
        warnings: list[str],
    ) -> None:
        index = 0
        while index < len(layers):
            layer = layers[index]
            if str(getattr(layer, "layer_type", "raster")) == "effect":
                # A sibling filter layer re-processes everything below it.
                filtered = self._apply_cpu_filters(target, [layer])
                if filtered is not target:
                    painter = QPainter(target)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                    painter.drawImage(0, 0, filtered)
                    painter.end()
                index += 1
                continue
            group: list[Any] = [layer]
            next_index = index + 1
            while next_index < len(layers):
                next_layer = layers[next_index]
                if not bool(getattr(next_layer, "clipping", False)):
                    break
                group.append(next_layer)
                next_index += 1

            if len(group) == 1:
                self._paint_layer(
                    target,
                    layer,
                    canvas_size=canvas_size,
                    warnings=warnings,
                )
            else:
                self._paint_clipping_group(
                    target,
                    group,
                    canvas_size=canvas_size,
                    warnings=warnings,
                )
            index = next_index

    def _paint_clipping_group(
        self,
        target: QImage,
        layers: list[Any],
        *,
        canvas_size: tuple[int, int],
        warnings: list[str],
    ) -> None:
        base = layers[0]
        temp = self._transparent_canvas(canvas_size)
        base_image = self._layer_canvas_image(
            base,
            canvas_size=canvas_size,
            warnings=warnings,
        )
        if base_image is None:
            return
        painter = QPainter(temp)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(self._clamped_opacity(getattr(base, "opacity", 1.0)))
        painter.drawImage(0, 0, base_image)
        painter.end()
        for clipped in layers[1:]:
            clipped_image = self._layer_canvas_image(
                clipped,
                canvas_size=canvas_size,
                warnings=warnings,
            )
            if clipped_image is None:
                continue
            clip_mode = CanvasBlendService.normalize_blend_mode(
                getattr(clipped, "blend_mode", "normal")
            )
            clip_opacity = self._clamped_opacity(getattr(clipped, "opacity", 1.0))
            if clip_mode in (BlendMode.NORMAL, BlendMode.PASS_THROUGH):
                painter = QPainter(temp)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
                painter.setOpacity(clip_opacity)
                painter.drawImage(0, 0, clipped_image)
                painter.end()
            else:
                # Same math as the flatten path and the wgpu atop composite.
                CanvasBlendService.custom_blend_atop(temp, clipped_image, clip_mode, clip_opacity)
        self._paint_image(
            target,
            temp,
            blend_mode=getattr(base, "blend_mode", "normal"),
            opacity=1.0,
        )

    def _paint_layer(
        self,
        target: QImage,
        layer: Any,
        *,
        canvas_size: tuple[int, int],
        warnings: list[str],
    ) -> None:
        layer_image = self._layer_canvas_image(
            layer,
            canvas_size=canvas_size,
            warnings=warnings,
        )
        if layer_image is None:
            return
        self._paint_image(
            target,
            layer_image,
            blend_mode=getattr(layer, "blend_mode", "normal"),
            opacity=self._clamped_opacity(getattr(layer, "opacity", 1.0)),
        )

    def _layer_canvas_image(
        self,
        layer: Any,
        *,
        canvas_size: tuple[int, int],
        warnings: list[str],
    ) -> QImage | None:
        if not getattr(layer, "visible", True):
            return None
        if float(getattr(layer, "opacity", 1.0)) <= 0:
            return None
        layer_type = str(getattr(layer, "layer_type", "raster"))
        if layer_type == "effect":
            # Sibling filter layers are applied to the accumulated target by
            # ``_compose_layer_sequence``, never painted as content.
            return None

        source = self._source_image_for_layer(
            layer,
            canvas_size=canvas_size,
            warnings=warnings,
        )
        if source is None or source.isNull():
            if layer_type not in {"group", *_RASTER_LIKE_LAYER_TYPES}:
                warnings.append(
                    f"layer {getattr(layer, 'id', '<unknown>')}: "
                    f"unsupported wgpu layer_type {layer_type!r}"
                )
            return None

        canvas = self._transparent_canvas(canvas_size)
        painter = QPainter(canvas)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        offset_x = int(getattr(layer, "offset_x", 0))
        offset_y = int(getattr(layer, "offset_y", 0))
        prescale = max(1, int(getattr(layer, "vector_raster_scale", 1) or 1))
        raster_origin = tuple(getattr(layer, "vector_raster_origin", (0, 0)) or (0, 0))
        if prescale != 1 or raster_origin != (0, 0):
            # Prescaled / tight-region raster (view compositing C2, live-edit
            # bypass): draw at the logical size shifted by the region origin.
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(
                QRectF(
                    float(offset_x + raster_origin[0]),
                    float(offset_y + raster_origin[1]),
                    source.width() / prescale,
                    source.height() / prescale,
                ),
                source,
            )
        else:
            painter.drawImage(offset_x, offset_y, source)
        painter.end()

        mask = getattr(layer, "layer_mask", None)
        if mask is not None and bool(getattr(layer, "mask_enabled", True)):
            painter = QPainter(canvas)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            painter.drawImage(0, 0, mask_coverage_alpha8(mask))
            painter.end()
        return canvas

    def _source_image_for_layer(
        self,
        layer: Any,
        *,
        canvas_size: tuple[int, int],
        warnings: list[str],
    ) -> QImage | None:
        children = list(getattr(layer, "children", []) or [])
        if str(getattr(layer, "layer_type", "raster")) == "group":
            group_image = self._transparent_canvas(canvas_size)
            if not children:
                return group_image
            self._compose_layer_sequence(
                group_image,
                children,
                canvas_size=canvas_size,
                warnings=warnings,
            )
            return group_image
        image = getattr(layer, "image", None)
        if image is None and getattr(layer, "native_display", None) is not None:
            image = layer.native_display.image
        if image is None or image.isNull():
            return None
        return self._apply_cpu_filters(
            cast(QImage, image),
            self._visible_child_filters(layer),
            layer_origin=(
                int(getattr(layer, "offset_x", 0) or 0),
                int(getattr(layer, "offset_y", 0) or 0),
            ),
        )

    def _apply_cpu_filters(
        self,
        source: QImage,
        filter_layers: list[Any],
        *,
        layer_origin: tuple[int, int] = (0, 0),
    ) -> QImage:
        """Run *filter_layers* over *source* through the CPU filter registry.

        Shared by the bitmap fallback's sibling and child filter handling; the
        registry is the same one the merge/flatten and export paths use, so
        the fallback matches them pixel for pixel.
        """

        applicable = [
            layer
            for layer in filter_layers
            if getattr(layer, "visible", True)
            and getattr(layer, "filter_type", getattr(layer, "effect_type", None))
        ]
        if not applicable:
            return source
        # Deferred: the filter services pull in host_api, which must not be a
        # hard import dependency of the render backend.
        from loomstroke.core.canvas_filter_layer_service import (  # noqa: PLC0415
            CanvasPluginFilterImageApplier,
        )
        from loomstroke.core.filters import FilterRegistry  # noqa: PLC0415

        applier = CanvasPluginFilterImageApplier(FilterRegistry)
        # Filter implementations are free to work in place, and ``source`` may
        # be a live layer image or the accumulating flatten target.
        result = source.copy()
        document_dpi = getattr(self, "_flatten_document_dpi", None)
        for filter_layer in applicable:
            result = applier.apply_filter_to_image(
                result,
                filter_layer,
                document_dpi=document_dpi,
                layer_origin=layer_origin,
            )
        return result

    def _paint_image(
        self,
        target: QImage,
        image: QImage,
        *,
        blend_mode: Any,
        opacity: float,
    ) -> None:
        painter = QPainter(target)
        painter.setCompositionMode(self._composition_mode(blend_mode))
        painter.setOpacity(self._clamped_opacity(opacity))
        painter.drawImage(0, 0, image)
        painter.end()

    @staticmethod
    def _transparent_canvas(canvas_size: tuple[int, int]) -> QImage:
        image = QImage(
            canvas_size[0],
            canvas_size[1],
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        return image

    @staticmethod
    def _composition_mode(blend_mode: Any) -> QPainter.CompositionMode:
        mapping = {
            "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
            "pass_through": QPainter.CompositionMode.CompositionMode_SourceOver,
            "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
            "screen": QPainter.CompositionMode.CompositionMode_Screen,
            "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
            "darken": QPainter.CompositionMode.CompositionMode_Darken,
            "lighten": QPainter.CompositionMode.CompositionMode_Lighten,
            "color_burn": QPainter.CompositionMode.CompositionMode_ColorBurn,
            "color_dodge": QPainter.CompositionMode.CompositionMode_ColorDodge,
            "linear_dodge": QPainter.CompositionMode.CompositionMode_Plus,
            "soft_light": QPainter.CompositionMode.CompositionMode_SoftLight,
            "hard_light": QPainter.CompositionMode.CompositionMode_HardLight,
            "difference": QPainter.CompositionMode.CompositionMode_Difference,
            "exclusion": QPainter.CompositionMode.CompositionMode_Exclusion,
        }
        return mapping.get(
            WgpuCompositor._blend_mode_name(blend_mode),
            QPainter.CompositionMode.CompositionMode_SourceOver,
        )

    @staticmethod
    def _blend_mode_name(blend_mode: Any) -> str:
        raw = getattr(blend_mode, "value", blend_mode)
        if not isinstance(raw, str):
            raw = getattr(blend_mode, "name", str(raw))
        return str(raw).rsplit(".", maxsplit=1)[-1].lower()

    @staticmethod
    def _clamped_opacity(value: Any) -> float:
        return max(0.0, min(1.0, float(value)))

    def _composite_size(self, scene: RenderSceneSnapshot) -> tuple[int, int]:
        if self._view is not None:
            return (max(1, int(self._view.width)), max(1, int(self._view.height)))
        return (int(scene.width), int(scene.height))

    def _view_blend_kwargs(
        self,
        scene: RenderSceneSnapshot,
        *,
        src_in_target_space: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "canvas_dims": (float(scene.width), float(scene.height)),
        }
        if self._view_inverse is not None:
            kwargs["view_inverse"] = self._view_inverse
            kwargs["src_in_target_space"] = src_in_target_space
        return kwargs

    @staticmethod
    def _compose_affine(
        outer: tuple[float, float, float, float, float, float],
        inner: tuple[float, float, float, float, float, float],
    ) -> tuple[float, float, float, float, float, float]:
        oa, ob, oc, od, otx, oty = outer
        ia, ib, ic, id_, itx, ity = inner
        return (
            oa * ia + ob * ic,
            oa * ib + ob * id_,
            oc * ia + od * ic,
            oc * ib + od * id_,
            oa * itx + ob * ity + otx,
            oc * itx + od * ity + oty,
        )

    @staticmethod
    def _flattened_texture_version(scene: RenderSceneSnapshot) -> int:
        layer_parts: list[tuple[Any, ...]] = []
        for layer in scene.flat_layers:
            image = getattr(layer, "image", None)
            layer_parts.append(
                (
                    getattr(layer, "id", ""),
                    getattr(layer, "texture_version", 0),
                    image.cacheKey() if image is not None and not image.isNull() else 0,
                    getattr(layer, "offset_x", 0),
                    getattr(layer, "offset_y", 0),
                    getattr(layer, "opacity", 1.0),
                    WgpuCompositor._blend_mode_name(getattr(layer, "blend_mode", "normal")),
                    bool(getattr(layer, "visible", True)),
                    bool(getattr(layer, "clipping", False)),
                    bool(getattr(layer, "mask_enabled", True)),
                )
            )
        preview = scene.selection_preview_image
        version = hash(
            (
                scene.canvas_state.layer_cache_version,
                scene.canvas_state.background_rgba,
                tuple(layer_parts),
                preview.cacheKey() if preview is not None and not preview.isNull() else 0,
                scene.selection_preview_bounds,
            )
        )
        return version & 0x7FFFFFFF

    @staticmethod
    def _premultiplied_clear(rgba: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
        alpha = rgba[3] / 255.0
        return (
            (rgba[0] / 255.0) * alpha,
            (rgba[1] / 255.0) * alpha,
            (rgba[2] / 255.0) * alpha,
            alpha,
        )

    @staticmethod
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()

    def _retire_targets(self, targets: list[WgpuRenderTargetHandle]) -> None:
        if targets:
            self._retired_target_batches.append(targets)
        while len(self._retired_target_batches) > 3:
            for target in self._retired_target_batches.pop(0):
                self._destroy_target(target)

    # Preserve established private entry points while new rendering uses Filter names.
    _visible_child_effects = _visible_child_filters
    _apply_layer_effects = _apply_layer_filters
    _draw_effect_layer = _draw_filter_layer
    _apply_cpu_effects = _apply_cpu_filters

    def release(self) -> None:
        self.layer_style_processor.release()
        self._group_composite_cache.release()
        release_bindings = getattr(self.shader_library, "release_blend_bindings", None)
        if callable(release_bindings):
            release_bindings()
        for batch in self._retired_target_batches:
            for target in batch:
                self._destroy_target(target)
        self._retired_target_batches.clear()
        self._filter_prefix_renderer.release()
        self.texture_manager.release()
        self._mask_texture_manager.release()
        self.filter_processor.release()
        self.three_d_renderer.release()
        self.vector_renderer.release()


# Saved layer_type="effect" remains stable; only the runtime vocabulary changes.
is_cpu_only_effect_layer = is_cpu_only_filter_layer
