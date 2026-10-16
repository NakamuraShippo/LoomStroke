"""WGPU retained-mode vector layer renderer (Phase V1/V2: fill + stroke).

Renders eligible vector layers straight from compiled geometry instead of a
QImage raster snapshot.  Structure mirrors ``Wgpu3DLayerRenderer``: the
compositor calls :meth:`WgpuVectorRenderer.render_layer` inside its layer
traversal, receives an RGBA texture, and applies opacity / blend / mask /
clipping through the shared blend pass.

Draw order matches the QPainter path (fill then stroke per object, objects
in document order).  The layer's non-destructive affine transform is applied
in the vertex shader, so transform-only edits never re-upload vertices
(spec §9.4).

Retained GPU resources (spec §9):

- geometry buffers keyed ``(object_id, geometry_version, kind, ...)`` where
  stroke entries also key on the stroke geometry signature
- material uniforms keyed ``(object_id, kind)``, refreshed on ``style_version``
- one transform uniform per layer, rewritten in place on change
- one cached layer target per layer, reused while content is unchanged

None of these caches ever touch the vector document; eviction only forces a
re-render on the next frame.
"""

from __future__ import annotations

import contextlib
import logging
import math
import struct
from dataclasses import dataclass
from typing import Any

from loomstroke.core.vector_render_models import (
    AffineTuple,
    VectorObjectSnapshot,
    VectorRenderLayerSnapshot,
)
from loomstroke.ui.canvas.wgpu_backend.types import WgpuRenderTargetHandle
from loomstroke.ui.canvas.wgpu_backend.vector.glyph_atlas import PAGE_SIZE, GlyphAtlasManager
from loomstroke.ui.canvas.wgpu_backend.vector.stroke import tessellate_stroke
from loomstroke.ui.canvas.wgpu_backend.vector.tessellator import (
    CompiledFillGeometry,
    tessellate_fill,
)

logger = logging.getLogger(__name__)

_VECTOR_FILL_WGSL = """
struct FrameUniform {
    canvas_size: vec2<f32>,
    _pad: vec2<f32>,
};

struct MaterialUniform {
    color: vec4<f32>,
};

struct TransformUniform {
    row0: vec4<f32>,  // a, b, tx, 0
    row1: vec4<f32>,  // c, d, ty, 0
};

@group(0) @binding(0) var<uniform> frame: FrameUniform;
@group(0) @binding(1) var<uniform> material: MaterialUniform;
@group(0) @binding(2) var<uniform> layer_transform: TransformUniform;

@vertex
fn vs_main(@location(0) position: vec2<f32>) -> @builtin(position) vec4<f32> {
    let world_x = layer_transform.row0.x * position.x
        + layer_transform.row0.y * position.y
        + layer_transform.row0.z;
    let world_y = layer_transform.row1.x * position.x
        + layer_transform.row1.y * position.y
        + layer_transform.row1.z;
    let ndc_x = world_x / frame.canvas_size.x * 2.0 - 1.0;
    let ndc_y = 1.0 - world_y / frame.canvas_size.y * 2.0;
    return vec4<f32>(ndc_x, ndc_y, 0.0, 1.0);
}

@fragment
fn fs_main() -> @location(0) vec4<f32> {
    // Premultiplied color; the layer target composites source-over.
    return material.color;
}
"""

_VECTOR_GLYPH_WGSL = """
struct FrameUniform {
    canvas_size: vec2<f32>,
    _pad: vec2<f32>,
};

struct MaterialUniform {
    color: vec4<f32>,  // straight (non-premultiplied) text color
};

struct TransformUniform {
    row0: vec4<f32>,  // a, b, tx, 0
    row1: vec4<f32>,  // c, d, ty, 0
};

@group(0) @binding(0) var<uniform> frame: FrameUniform;
@group(0) @binding(1) var<uniform> material: MaterialUniform;
@group(0) @binding(2) var<uniform> layer_transform: TransformUniform;
@group(0) @binding(3) var glyph_sampler: sampler;
@group(0) @binding(4) var glyph_atlas: texture_2d<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@location(0) position: vec2<f32>, @location(1) uv: vec2<f32>) -> VertexOut {
    let world_x = layer_transform.row0.x * position.x
        + layer_transform.row0.y * position.y
        + layer_transform.row0.z;
    let world_y = layer_transform.row1.x * position.x
        + layer_transform.row1.y * position.y
        + layer_transform.row1.z;
    var out: VertexOut;
    out.position = vec4<f32>(
        world_x / frame.canvas_size.x * 2.0 - 1.0,
        1.0 - world_y / frame.canvas_size.y * 2.0,
        0.0,
        1.0,
    );
    out.uv = uv;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let coverage = textureSample(glyph_atlas, glyph_sampler, in.uv).r;
    let alpha = material.color.a * coverage;
    // Premultiplied output; the layer target composites source-over.
    return vec4<f32>(material.color.rgb * alpha, alpha);
}
"""

_FRAME_UNIFORM_BYTES = 16
_MATERIAL_UNIFORM_BYTES = 16
_TRANSFORM_UNIFORM_BYTES = 32
_VERTEX_STRIDE_BYTES = 8
_GLYPH_VERTEX_STRIDE_BYTES = 16
_BASE_TOLERANCE = 0.25
_MAX_LOD_BUCKET = 6
_SCREEN_SPACE_SAMPLES = 4


def lod_bucket_for_scale(scale: float) -> int:
    """Map an effective view scale to a tessellation LOD bucket (spec §8.3).

    Bucket ``n`` compiles with tolerance ``0.25 / 2**n`` so the on-screen
    flattening error stays at or below ~0.25px up to ``2**n`` zoom.
    """

    if not math.isfinite(scale) or scale <= 1.0:
        return 0
    return min(_MAX_LOD_BUCKET, max(0, math.ceil(math.log2(scale))))


def _tolerance_for_lod(lod_bucket: int) -> float:
    return _BASE_TOLERANCE / float(1 << max(0, int(lod_bucket)))


@dataclass(frozen=True, slots=True)
class WgpuVectorRenderResult:
    texture: WgpuRenderTargetHandle
    owned_targets: list[WgpuRenderTargetHandle]
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class _GeometryEntry:
    key: tuple[Any, ...]
    vertex_buffer: Any
    index_buffer: Any
    index_count: int
    bounds: tuple[float, float, float, float]


@dataclass(slots=True)
class _MaterialEntry:
    style_version: int
    buffer: Any
    bind_group: Any
    layer_id: str


@dataclass(frozen=True, slots=True)
class _DrawItem:
    """One draw call: geometry + bind group under a pipeline kind."""

    pipeline: str  # "fill" | "glyph"
    geometry: _GeometryEntry
    bind_group: Any


@dataclass(slots=True)
class _LayerTransformEntry:
    buffer: Any
    transform: AffineTuple | None


@dataclass(slots=True)
class _LayerTargetEntry:
    key: tuple[Any, ...]
    target: WgpuRenderTargetHandle
    warnings: tuple[str, ...]


@dataclass(slots=True)
class _ScreenSpaceResources:
    width: int
    height: int
    msaa_texture: Any
    msaa_view: Any
    resolve_target: WgpuRenderTargetHandle
    key: tuple[Any, ...] | None = None


class WgpuVectorRenderer:
    """Draw direct-eligible vector layers into wgpu textures."""

    def __init__(self, bundle: Any, *, target_format: str = "rgba8unorm") -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self.target_format = target_format
        self._pipelines: dict[int, Any] = {}
        self._glyph_pipelines: dict[int, Any] = {}
        self._bind_group_layout: Any | None = None
        self._glyph_bind_group_layout: Any | None = None
        self._glyph_sampler: Any | None = None
        self._glyph_atlas: GlyphAtlasManager | None = None
        self._screen_space: _ScreenSpaceResources | None = None
        self._layer_msaa: tuple[int, int, Any, Any] | None = None  # (w, h, texture, view)
        self._frame_buffer: Any | None = None
        self._frame_size: tuple[float, float] | None = None
        self._geometry: dict[tuple[str, str], _GeometryEntry] = {}
        self._materials: dict[tuple[str, str], _MaterialEntry] = {}
        self._layer_transforms: dict[str, _LayerTransformEntry] = {}
        self._layer_targets: dict[str, _LayerTargetEntry] = {}
        self._layer_object_ids: dict[str, set[str]] = {}
        self._counters: dict[str, int] = {
            "vector_geometry_compiles": 0,
            "vector_geometry_cache_hits": 0,
            "vector_geometry_uploads": 0,
            "vector_material_updates": 0,
            "vector_layer_target_renders": 0,
            "vector_layer_target_reuses": 0,
            "vector_objects_drawn": 0,
            "vector_screen_space_renders": 0,
            "vector_screen_space_reuses": 0,
            "vector_glyph_quads_drawn": 0,
        }
        self._fallback_reasons: list[str] = []

    def diagnostics(self) -> dict[str, Any]:
        """Runtime-only counters (spec §20); never persisted."""

        data: dict[str, Any] = dict(self._counters)
        data["vector_fallback_reasons"] = tuple(self._fallback_reasons)
        data["vector_glyph_atlas_uploads"] = (
            self._glyph_atlas.uploads if self._glyph_atlas is not None else 0
        )
        return data

    def render_layer(
        self,
        encoder: Any,
        layer: Any,
        *,
        width: int,
        height: int,
        create_target: Any,
        transform_override: AffineTuple | None = None,
        lod_bucket: int = 0,
    ) -> WgpuVectorRenderResult | None:
        payload = getattr(layer, "vector_render_layer", None)
        if not isinstance(payload, VectorRenderLayerSnapshot):
            return None

        layer_id = payload.layer_id
        effective_transform = (
            transform_override if transform_override is not None else payload.layer_transform
        )
        lod = max(0, int(lod_bucket))
        render_key = self._render_key(payload, width, height, effective_transform, lod)
        existing = self._layer_targets.get(layer_id)
        if existing is not None and existing.key == render_key:
            self._counters["vector_layer_target_reuses"] += 1
            return WgpuVectorRenderResult(
                texture=existing.target,
                owned_targets=[],
                warnings=existing.warnings,
            )

        warnings: list[str] = []
        transform_buffer = self._layer_transform_buffer(layer_id, effective_transform)
        draw_items = self._collect_draw_items(payload, layer_id, transform_buffer, warnings, lod)
        self._prune_layer_objects(layer_id, set(obj.object_id for obj in payload.objects))

        self._write_frame_uniform(width, height)
        self._destroy_layer_target(layer_id)
        target = create_target(int(width), int(height))
        try:
            # 4x MSAA into a shared scratch texture, resolved into the layer
            # target (spec §9.6) so edges match the antialiased QPainter path.
            msaa_view = self._layer_msaa_view(int(width), int(height))
            self._draw_items_pass(
                encoder, msaa_view, target.view, _SCREEN_SPACE_SAMPLES, draw_items
            )
        except Exception:
            self._destroy_target_handle(target)
            raise

        self._counters["vector_layer_target_renders"] += 1
        warning_tuple = tuple(warnings)
        self._layer_targets[layer_id] = _LayerTargetEntry(
            key=render_key,
            target=target,
            warnings=warning_tuple,
        )
        return WgpuVectorRenderResult(
            texture=target,
            owned_targets=[],
            warnings=warning_tuple,
        )

    def render_screen_space(
        self,
        encoder: Any,
        payload: VectorRenderLayerSnapshot,
        *,
        view_transform: AffineTuple,
        width: int,
        height: int,
        lod_bucket: int = 0,
    ) -> WgpuRenderTargetHandle | None:
        """Render *payload* at display resolution for the presenter overlay.

        Draws the layer's retained geometry with ``view_transform`` (canvas
        -> surface physical pixels, combined with the layer transform by the
        caller) into a 4x MSAA target and resolves it.  Used to replace the
        QPainter screen-space overlay (SSO) for the topmost eligible vector
        layer; the compositor skips the layer in the same frame.
        """

        if not payload.objects or width <= 0 or height <= 0:
            return None
        resources = self._screen_space_resources(int(width), int(height))
        render_key = (
            view_transform,
            int(lod_bucket),
            tuple(
                (obj.object_id, obj.geometry_version, obj.style_version) for obj in payload.objects
            ),
        )
        if resources.key == render_key:
            self._counters["vector_screen_space_reuses"] += 1
            return resources.resolve_target

        warnings: list[str] = []
        layer_id = payload.layer_id
        transform_buffer = self._layer_transform_buffer(layer_id, view_transform)
        draw_items = self._collect_draw_items(
            payload, layer_id, transform_buffer, warnings, lod_bucket
        )
        self._write_frame_uniform(width, height)
        self._draw_items_pass(
            encoder,
            resources.msaa_view,
            resources.resolve_target.view,
            _SCREEN_SPACE_SAMPLES,
            draw_items,
        )
        self._counters["vector_screen_space_renders"] += 1
        resources.key = render_key
        return resources.resolve_target

    def _collect_draw_items(
        self,
        payload: VectorRenderLayerSnapshot,
        layer_id: str,
        transform_buffer: Any,
        warnings: list[str],
        lod_bucket: int,
    ) -> list[_DrawItem]:
        draw_items: list[_DrawItem] = []
        for obj in payload.objects:
            if obj.object_kind == "text" and obj.text is not None:
                draw_items.extend(
                    self._glyph_draw_items(obj, layer_id, transform_buffer, warnings, lod_bucket)
                )
                continue
            # QPainter draws each object's fill first, then its stroke.
            if obj.style.fill_enabled and obj.style.fill_color is not None:
                fill = self._geometry_for_object(obj, "fill", warnings, lod_bucket)
                if fill is not None:
                    material = self._material_for_object(obj, "fill", layer_id, transform_buffer)
                    draw_items.append(_DrawItem("fill", fill, material.bind_group))
            stroke = self._geometry_for_object(obj, "stroke", warnings, lod_bucket)
            if stroke is not None:
                material = self._material_for_object(obj, "stroke", layer_id, transform_buffer)
                draw_items.append(_DrawItem("fill", stroke, material.bind_group))
        return draw_items

    def _draw_items_pass(
        self,
        encoder: Any,
        view: Any,
        resolve_view: Any | None,
        samples: int,
        draw_items: list[_DrawItem],
    ) -> None:
        render_pass = encoder.begin_render_pass(
            color_attachments=[
                {
                    "view": view,
                    "resolve_target": resolve_view,
                    "clear_value": (0.0, 0.0, 0.0, 0.0),
                    "load_op": self._wgpu.LoadOp.clear,
                    "store_op": (
                        self._wgpu.StoreOp.discard
                        if resolve_view is not None
                        else self._wgpu.StoreOp.store
                    ),
                }
            ]
        )
        current_pipeline: str | None = None
        for item in draw_items:
            if item.pipeline != current_pipeline:
                if item.pipeline == "glyph":
                    render_pass.set_pipeline(self._render_pipeline_glyph(samples))
                else:
                    render_pass.set_pipeline(self._render_pipeline(samples))
                current_pipeline = item.pipeline
            render_pass.set_bind_group(0, item.bind_group, [], 0, 999999)
            render_pass.set_vertex_buffer(0, item.geometry.vertex_buffer)
            render_pass.set_index_buffer(item.geometry.index_buffer, self._wgpu.IndexFormat.uint32)
            render_pass.draw_indexed(item.geometry.index_count, 1, 0, 0, 0)
            self._counters["vector_objects_drawn"] += 1
        render_pass.end()

    def _screen_space_resources(self, width: int, height: int) -> _ScreenSpaceResources:
        existing = self._screen_space
        if existing is not None and existing.width == width and existing.height == height:
            return existing
        self._destroy_screen_space()
        msaa_texture = self._device.create_texture(
            size=(width, height, 1),
            sample_count=_SCREEN_SPACE_SAMPLES,
            usage=self._wgpu.TextureUsage.RENDER_ATTACHMENT,
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        resolve_texture = self._device.create_texture(
            size=(width, height, 1),
            usage=(
                self._wgpu.TextureUsage.RENDER_ATTACHMENT
                | self._wgpu.TextureUsage.TEXTURE_BINDING
                | self._wgpu.TextureUsage.COPY_SRC
            ),
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        resources = _ScreenSpaceResources(
            width=width,
            height=height,
            msaa_texture=msaa_texture,
            msaa_view=msaa_texture.create_view(),
            resolve_target=WgpuRenderTargetHandle(
                texture=resolve_texture,
                view=resolve_texture.create_view(),
                width=width,
                height=height,
                format="rgba8unorm",
            ),
        )
        self._screen_space = resources
        return resources

    def _layer_msaa_view(self, width: int, height: int) -> Any:
        """Shared 4x MSAA scratch texture for layer target renders.

        Layer passes run sequentially inside one encoder, so a single
        canvas-sized scratch texture serves every layer in the frame.
        """

        existing = self._layer_msaa
        if existing is not None and existing[0] == width and existing[1] == height:
            return existing[3]
        self._destroy_layer_msaa()
        texture = self._device.create_texture(
            size=(width, height, 1),
            sample_count=_SCREEN_SPACE_SAMPLES,
            usage=self._wgpu.TextureUsage.RENDER_ATTACHMENT,
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        view = texture.create_view()
        self._layer_msaa = (width, height, texture, view)
        return view

    def _destroy_layer_msaa(self) -> None:
        existing = self._layer_msaa
        if existing is None:
            return
        self._layer_msaa = None
        self._destroy_buffer(existing[2])

    def _destroy_screen_space(self) -> None:
        resources = self._screen_space
        if resources is None:
            return
        self._screen_space = None
        self._destroy_buffer(resources.msaa_texture)
        self._destroy_target_handle(resources.resolve_target)

    def remove_stale(self, active_layer_ids: set[str]) -> None:
        active_ids = {str(layer_id) for layer_id in active_layer_ids}
        for layer_id in set(self._layer_targets) - active_ids:
            self._destroy_layer_target(layer_id)
        for layer_id in set(self._layer_transforms) - active_ids:
            self._destroy_layer_transform(layer_id)
        stale_layers = set(self._layer_object_ids) - active_ids
        for layer_id in stale_layers:
            for object_id in self._layer_object_ids.pop(layer_id, set()):
                self._destroy_object_resources(object_id)

    def release(self) -> None:
        for layer_id in list(self._layer_targets):
            self._destroy_layer_target(layer_id)
        for layer_id in list(self._layer_transforms):
            self._destroy_layer_transform(layer_id)
        for object_id, _kind in list(self._geometry):
            self._destroy_object_resources(object_id)
        self._materials.clear()
        self._layer_object_ids.clear()
        self._destroy_screen_space()
        self._destroy_layer_msaa()
        if self._glyph_atlas is not None:
            self._glyph_atlas.release()
            self._glyph_atlas = None
        if self._frame_buffer is not None:
            self._destroy_buffer(self._frame_buffer)
            self._frame_buffer = None
        self._frame_size = None
        self._pipelines.clear()
        self._glyph_pipelines.clear()
        self._bind_group_layout = None
        self._glyph_bind_group_layout = None
        self._glyph_sampler = None

    @staticmethod
    def _render_key(
        payload: VectorRenderLayerSnapshot,
        width: int,
        height: int,
        effective_transform: AffineTuple | None = None,
        lod_bucket: int = 0,
    ) -> tuple[Any, ...]:
        transform = (
            effective_transform if effective_transform is not None else payload.layer_transform
        )
        return (
            int(width),
            int(height),
            transform,
            int(lod_bucket),
            tuple(
                (
                    obj.object_id,
                    obj.geometry_version,
                    obj.style_version,
                    obj.text.layout_version if obj.text is not None else 0,
                )
                for obj in payload.objects
            ),
        )

    def _geometry_for_object(
        self,
        obj: VectorObjectSnapshot,
        kind: str,
        warnings: list[str],
        lod_bucket: int = 0,
    ) -> _GeometryEntry | None:
        lod = max(0, int(lod_bucket))
        key: tuple[Any, ...]
        if kind == "stroke":
            key = (
                obj.object_id,
                obj.geometry_version,
                "stroke",
                obj.style.stroke_geometry_signature,
                lod,
            )
        else:
            key = (obj.object_id, obj.geometry_version, "fill", lod)
        cache_key = (obj.object_id, kind)
        existing = self._geometry.get(cache_key)
        if existing is not None and existing.key == key:
            self._counters["vector_geometry_cache_hits"] += 1
            return existing
        if existing is not None:
            self._destroy_geometry_entry(cache_key)

        self._counters["vector_geometry_compiles"] += 1
        tolerance = _tolerance_for_lod(lod)
        compiled = (
            tessellate_stroke(obj, tolerance=tolerance, lod_bucket=lod)
            if kind == "stroke"
            else tessellate_fill(obj, tolerance=tolerance, lod_bucket=lod)
        )
        if compiled is None:
            reason = f"vector_geometry_compile_failed:{obj.object_id}:{kind}"
            self._fallback_reasons.append(reason)
            warnings.append(f"vector object {obj.object_id}: {kind} geometry compile failed")
            return None
        if compiled.is_empty:
            return None
        return self._upload_geometry(cache_key, key, compiled)

    def _upload_geometry(
        self,
        cache_key: tuple[str, str],
        key: tuple[Any, ...],
        compiled: CompiledFillGeometry,
    ) -> _GeometryEntry:
        vertex_buffer = self._device.create_buffer_with_data(
            data=compiled.vertices,
            usage=self._wgpu.BufferUsage.VERTEX,
        )
        index_buffer = self._device.create_buffer_with_data(
            data=compiled.indices,
            usage=self._wgpu.BufferUsage.INDEX,
        )
        self._counters["vector_geometry_uploads"] += 1
        entry = _GeometryEntry(
            key=key,
            vertex_buffer=vertex_buffer,
            index_buffer=index_buffer,
            index_count=compiled.index_count,
            bounds=compiled.bounds,
        )
        self._geometry[cache_key] = entry
        return entry

    def _material_for_object(
        self,
        obj: VectorObjectSnapshot,
        kind: str,
        layer_id: str,
        transform_buffer: Any,
    ) -> _MaterialEntry:
        cache_key = (obj.object_id, kind)
        existing = self._materials.get(cache_key)
        if (
            existing is not None
            and existing.style_version == obj.style_version
            and existing.layer_id == layer_id
        ):
            return existing
        color_bytes = self._material_bytes(obj, kind)
        if existing is not None and existing.layer_id == layer_id:
            self._queue.write_buffer(existing.buffer, 0, color_bytes)
            existing.style_version = obj.style_version
            self._counters["vector_material_updates"] += 1
            return existing

        buffer = (
            existing.buffer
            if existing is not None
            else self._device.create_buffer_with_data(
                data=color_bytes,
                usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
            )
        )
        if existing is not None:
            self._queue.write_buffer(buffer, 0, color_bytes)
        bind_group = self._device.create_bind_group(
            layout=self._bind_group_layout_handle(),
            entries=[
                {
                    "binding": 0,
                    "resource": {
                        "buffer": self._frame_buffer_handle(),
                        "offset": 0,
                        "size": _FRAME_UNIFORM_BYTES,
                    },
                },
                {
                    "binding": 1,
                    "resource": {
                        "buffer": buffer,
                        "offset": 0,
                        "size": _MATERIAL_UNIFORM_BYTES,
                    },
                },
                {
                    "binding": 2,
                    "resource": {
                        "buffer": transform_buffer,
                        "offset": 0,
                        "size": _TRANSFORM_UNIFORM_BYTES,
                    },
                },
            ],
        )
        entry = _MaterialEntry(
            style_version=obj.style_version,
            buffer=buffer,
            bind_group=bind_group,
            layer_id=layer_id,
        )
        self._materials[cache_key] = entry
        self._counters["vector_material_updates"] += 1
        return entry

    @staticmethod
    def _material_bytes(obj: VectorObjectSnapshot, kind: str) -> bytes:
        color = obj.style.stroke_color if kind == "stroke" else obj.style.fill_color or (0, 0, 0)
        red = max(0.0, min(1.0, color[0] / 255.0))
        green = max(0.0, min(1.0, color[1] / 255.0))
        blue = max(0.0, min(1.0, color[2] / 255.0))
        return struct.pack("<4f", red, green, blue, 1.0)

    # --- Direct text rendering (spec §12, V5) ---

    def _glyph_draw_items(
        self,
        obj: VectorObjectSnapshot,
        layer_id: str,
        transform_buffer: Any,
        warnings: list[str],
        lod_bucket: int,
    ) -> list[_DrawItem]:
        text = obj.text
        if text is None or not text.glyph_runs:
            return []
        atlas = self._glyph_atlas_handle()
        lod = max(0, int(lod_bucket))
        geometries = self._glyph_geometry_for_object(obj, atlas, warnings, lod)
        items: list[_DrawItem] = []
        for page_index, geometry in geometries:
            bind_group = self._glyph_bind_group(obj, page_index, layer_id, transform_buffer, atlas)
            items.append(_DrawItem("glyph", geometry, bind_group))
        return items

    def _glyph_geometry_for_object(
        self,
        obj: VectorObjectSnapshot,
        atlas: GlyphAtlasManager,
        warnings: list[str],
        lod: int,
    ) -> list[tuple[int, _GeometryEntry]]:
        text = obj.text
        assert text is not None
        key = (obj.object_id, text.layout_version, "glyph", lod)
        # Cache entries are keyed "glyph:<atlas page number>" per object.
        cached = [
            (int(cache_key[1].split(":", 1)[1]), entry)
            for cache_key, entry in self._geometry.items()
            if cache_key[0] == obj.object_id and cache_key[1].startswith("glyph:")
        ]
        if cached and all(entry.key == key for _page, entry in cached):
            self._counters["vector_geometry_cache_hits"] += 1
            return sorted(cached)

        self._destroy_glyph_geometry(obj.object_id)
        self._counters["vector_geometry_compiles"] += 1
        per_page: dict[int, tuple[list[float], list[int]]] = {}
        for run in text.glyph_runs:
            for glyph_id, (pen_x, pen_y) in zip(run.glyph_ids, run.positions, strict=True):
                slot = atlas.slot_for_glyph(
                    font_resource_id=run.font_resource_id,
                    font_family=text.font_family,
                    font_style=text.font_style,
                    base_pixel_size=run.font_pixel_size,
                    glyph_id=glyph_id,
                    scale_bucket=lod,
                )
                if slot is None:
                    continue
                scale = max(1.0, slot.raster_scale)
                x0 = pen_x + slot.bearing_x / scale
                y0 = pen_y + slot.bearing_y / scale
                x1 = x0 + slot.width / scale
                y1 = y0 + slot.height / scale
                u0 = slot.x / PAGE_SIZE
                v0 = slot.y / PAGE_SIZE
                u1 = (slot.x + slot.width) / PAGE_SIZE
                v1 = (slot.y + slot.height) / PAGE_SIZE
                vertices, indices = per_page.setdefault(slot.page_index, ([], []))
                base = len(vertices) // 4
                vertices.extend(
                    (
                        x0,
                        y0,
                        u0,
                        v0,
                        x1,
                        y0,
                        u1,
                        v0,
                        x1,
                        y1,
                        u1,
                        v1,
                        x0,
                        y1,
                        u0,
                        v1,
                    )
                )
                indices.extend((base, base + 1, base + 2, base, base + 2, base + 3))
                self._counters["vector_glyph_quads_drawn"] += 1

        results: list[tuple[int, _GeometryEntry]] = []
        for page_number in sorted(per_page):
            vertices, indices = per_page[page_number]
            if not indices:
                continue
            vertex_buffer = self._device.create_buffer_with_data(
                data=struct.pack(f"<{len(vertices)}f", *vertices),
                usage=self._wgpu.BufferUsage.VERTEX,
            )
            index_buffer = self._device.create_buffer_with_data(
                data=struct.pack(f"<{len(indices)}I", *indices),
                usage=self._wgpu.BufferUsage.INDEX,
            )
            self._counters["vector_geometry_uploads"] += 1
            entry = _GeometryEntry(
                key=key,
                vertex_buffer=vertex_buffer,
                index_buffer=index_buffer,
                index_count=len(indices),
                bounds=(0.0, 0.0, 0.0, 0.0),
            )
            self._geometry[(obj.object_id, f"glyph:{page_number}")] = entry
            results.append((page_number, entry))
        return results

    def _glyph_bind_group(
        self,
        obj: VectorObjectSnapshot,
        page_index: int,
        layer_id: str,
        transform_buffer: Any,
        atlas: GlyphAtlasManager,
    ) -> Any:
        text = obj.text
        assert text is not None
        cache_key = (obj.object_id, f"glyph:{page_index}")
        existing = self._materials.get(cache_key)
        if (
            existing is not None
            and existing.style_version == text.layout_version
            and existing.layer_id == layer_id
        ):
            return existing.bind_group
        red, green, blue, alpha = (component / 255.0 for component in text.color_rgba)
        color_bytes = struct.pack("<4f", red, green, blue, alpha)
        buffer = existing.buffer if existing is not None else None
        if buffer is None:
            buffer = self._device.create_buffer_with_data(
                data=color_bytes,
                usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
            )
        else:
            self._queue.write_buffer(buffer, 0, color_bytes)
        bind_group = self._device.create_bind_group(
            layout=self._glyph_bind_group_layout_handle(),
            entries=[
                {
                    "binding": 0,
                    "resource": {
                        "buffer": self._frame_buffer_handle(),
                        "offset": 0,
                        "size": _FRAME_UNIFORM_BYTES,
                    },
                },
                {
                    "binding": 1,
                    "resource": {
                        "buffer": buffer,
                        "offset": 0,
                        "size": _MATERIAL_UNIFORM_BYTES,
                    },
                },
                {
                    "binding": 2,
                    "resource": {
                        "buffer": transform_buffer,
                        "offset": 0,
                        "size": _TRANSFORM_UNIFORM_BYTES,
                    },
                },
                {"binding": 3, "resource": self._glyph_sampler_handle()},
                {"binding": 4, "resource": atlas.page_view(page_index)},
            ],
        )
        self._materials[cache_key] = _MaterialEntry(
            style_version=text.layout_version,
            buffer=buffer,
            bind_group=bind_group,
            layer_id=layer_id,
        )
        self._counters["vector_material_updates"] += 1
        return bind_group

    def _glyph_atlas_handle(self) -> GlyphAtlasManager:
        if self._glyph_atlas is None:
            self._glyph_atlas = GlyphAtlasManager(self._bundle)
        return self._glyph_atlas

    def _glyph_sampler_handle(self) -> Any:
        if self._glyph_sampler is None:
            self._glyph_sampler = self._device.create_sampler(
                mag_filter=self._wgpu.FilterMode.linear,
                min_filter=self._wgpu.FilterMode.linear,
            )
        return self._glyph_sampler

    def _glyph_bind_group_layout_handle(self) -> Any:
        if self._glyph_bind_group_layout is None:
            self._glyph_bind_group_layout = self._device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.VERTEX,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 1,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 2,
                        "visibility": self._wgpu.ShaderStage.VERTEX,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 3,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "sampler": {"type": self._wgpu.SamplerBindingType.filtering},
                    },
                    {
                        "binding": 4,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": self._wgpu.TextureViewDimension.d2,
                        },
                    },
                ]
            )
        return self._glyph_bind_group_layout

    def _render_pipeline_glyph(self, samples: int = 1) -> Any:
        existing = self._glyph_pipelines.get(samples)
        if existing is not None:
            return existing
        module = self._device.create_shader_module(code=_VECTOR_GLYPH_WGSL)
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._glyph_bind_group_layout_handle()]
        )
        blend_component = {
            "src_factor": self._wgpu.BlendFactor.one,
            "dst_factor": self._wgpu.BlendFactor.one_minus_src_alpha,
            "operation": self._wgpu.BlendOperation.add,
        }
        pipeline = self._device.create_render_pipeline(
            layout=layout,
            multisample={"count": int(samples), "mask": 0xFFFFFFFF},
            vertex={
                "module": module,
                "entry_point": "vs_main",
                "buffers": [
                    {
                        "array_stride": _GLYPH_VERTEX_STRIDE_BYTES,
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
            primitive={
                "topology": self._wgpu.PrimitiveTopology.triangle_list,
                "cull_mode": self._wgpu.CullMode.none,
            },
            fragment={
                "module": module,
                "entry_point": "fs_main",
                "targets": [
                    {
                        "format": self.target_format,
                        "blend": {
                            "color": dict(blend_component),
                            "alpha": dict(blend_component),
                        },
                    }
                ],
            },
        )
        self._glyph_pipelines[samples] = pipeline
        return pipeline

    def _destroy_glyph_geometry(self, object_id: str) -> None:
        for cache_key in [
            key for key in self._geometry if key[0] == object_id and key[1].startswith("glyph:")
        ]:
            self._destroy_geometry_entry(cache_key)

    def _layer_transform_buffer(self, layer_id: str, transform: AffineTuple) -> Any:
        entry = self._layer_transforms.get(layer_id)
        if entry is None:
            entry = _LayerTransformEntry(
                buffer=self._device.create_buffer(
                    size=_TRANSFORM_UNIFORM_BYTES,
                    usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
                ),
                transform=None,
            )
            self._layer_transforms[layer_id] = entry
        if entry.transform != transform:
            a, b, c, d, tx, ty = transform
            self._queue.write_buffer(
                entry.buffer,
                0,
                struct.pack("<8f", a, b, tx, 0.0, c, d, ty, 0.0),
            )
            entry.transform = transform
        return entry.buffer

    def _write_frame_uniform(self, width: int, height: int) -> None:
        size = (float(max(1, width)), float(max(1, height)))
        if self._frame_size == size and self._frame_buffer is not None:
            return
        buffer = self._frame_buffer_handle()
        self._queue.write_buffer(buffer, 0, struct.pack("<4f", size[0], size[1], 0.0, 0.0))
        self._frame_size = size

    def _frame_buffer_handle(self) -> Any:
        if self._frame_buffer is None:
            self._frame_buffer = self._device.create_buffer(
                size=_FRAME_UNIFORM_BYTES,
                usage=self._wgpu.BufferUsage.UNIFORM | self._wgpu.BufferUsage.COPY_DST,
            )
            self._frame_size = None
        return self._frame_buffer

    def _bind_group_layout_handle(self) -> Any:
        if self._bind_group_layout is None:
            self._bind_group_layout = self._device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.VERTEX,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 1,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 2,
                        "visibility": self._wgpu.ShaderStage.VERTEX,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                ]
            )
        return self._bind_group_layout

    def _render_pipeline(self, samples: int = 1) -> Any:
        existing = self._pipelines.get(samples)
        if existing is not None:
            return existing
        module = self._device.create_shader_module(code=_VECTOR_FILL_WGSL)
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._bind_group_layout_handle()]
        )
        blend_component = {
            "src_factor": self._wgpu.BlendFactor.one,
            "dst_factor": self._wgpu.BlendFactor.one_minus_src_alpha,
            "operation": self._wgpu.BlendOperation.add,
        }
        pipeline = self._device.create_render_pipeline(
            layout=layout,
            multisample={"count": int(samples), "mask": 0xFFFFFFFF},
            vertex={
                "module": module,
                "entry_point": "vs_main",
                "buffers": [
                    {
                        "array_stride": _VERTEX_STRIDE_BYTES,
                        "step_mode": self._wgpu.VertexStepMode.vertex,
                        "attributes": [
                            {
                                "format": self._wgpu.VertexFormat.float32x2,
                                "offset": 0,
                                "shader_location": 0,
                            }
                        ],
                    }
                ],
            },
            primitive={
                "topology": self._wgpu.PrimitiveTopology.triangle_list,
                "cull_mode": self._wgpu.CullMode.none,
            },
            fragment={
                "module": module,
                "entry_point": "fs_main",
                "targets": [
                    {
                        "format": self.target_format,
                        "blend": {
                            "color": dict(blend_component),
                            "alpha": dict(blend_component),
                        },
                    }
                ],
            },
        )
        self._pipelines[samples] = pipeline
        return pipeline

    def _prune_layer_objects(self, layer_id: str, object_ids: set[str]) -> None:
        previous = self._layer_object_ids.get(layer_id, set())
        owned_elsewhere: set[str] = set()
        for other_layer_id, ids in self._layer_object_ids.items():
            if other_layer_id != layer_id:
                owned_elsewhere.update(ids)
        for object_id in previous - object_ids - owned_elsewhere:
            self._destroy_object_resources(object_id)
        self._layer_object_ids[layer_id] = object_ids

    def _destroy_layer_target(self, layer_id: str) -> None:
        entry = self._layer_targets.pop(layer_id, None)
        if entry is not None:
            self._destroy_target_handle(entry.target)

    def _destroy_object_resources(self, object_id: str) -> None:
        for kind in ("fill", "stroke"):
            self._destroy_geometry_entry((object_id, kind))
        self._destroy_glyph_geometry(object_id)
        for cache_key in [key for key in self._materials if key[0] == object_id]:
            material = self._materials.pop(cache_key, None)
            if material is not None:
                self._destroy_buffer(material.buffer)

    def _destroy_geometry_entry(self, cache_key: tuple[str, str]) -> None:
        geometry = self._geometry.pop(cache_key, None)
        if geometry is not None:
            self._destroy_buffer(geometry.vertex_buffer)
            self._destroy_buffer(geometry.index_buffer)

    def _destroy_layer_transform(self, layer_id: str) -> None:
        entry = self._layer_transforms.pop(layer_id, None)
        if entry is not None:
            self._destroy_buffer(entry.buffer)

    @staticmethod
    def _destroy_target_handle(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            with contextlib.suppress(Exception):
                destroy()

    @staticmethod
    def _destroy_buffer(buffer: Any) -> None:
        destroy = getattr(buffer, "destroy", None)
        if callable(destroy):
            with contextlib.suppress(Exception):
                destroy()
