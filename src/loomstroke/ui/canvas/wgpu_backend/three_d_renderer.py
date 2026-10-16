"""wgpu renderer for editable 3D layer scenes."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any

import numpy as np
from PySide6.QtGui import QColor, QImage

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.assets_3d.texture import TextureAsset
from loomstroke.core.layer_3d_rendering import orbit_camera_state, preview_display_mode
from loomstroke.rendering_3d import PreviewDisplayMode

from .texture_manager import qimage_to_straight_rgba
from .types import WgpuRenderTargetHandle, WgpuShaderError

_VERTEX_STRIDE_FLOATS = 12
_VERTEX_STRIDE_BYTES = _VERTEX_STRIDE_FLOATS * 4

_THREE_D_LAYER_WGSL = """
struct Params {
    canvas_scale_depth: vec4<f32>,
    target_mode: vec4<f32>,
    row0: vec4<f32>,
    row1: vec4<f32>,
    row2: vec4<f32>,
    light: vec4<f32>,
};

@group(0) @binding(0) var<uniform> params: Params;
@group(0) @binding(1) var base_texture: texture_2d<f32>;
@group(0) @binding(2) var base_sampler: sampler;

struct VertexIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec4<f32>,
    @location(3) uv: vec2<f32>,
};

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) normal: vec3<f32>,
    @location(1) color: vec4<f32>,
    @location(2) uv: vec2<f32>,
};

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    let centered = in.position - params.target_mode.xyz;
    let cam = vec3<f32>(
        dot(centered, params.row0.xyz),
        dot(centered, params.row1.xyz),
        dot(centered, params.row2.xyz),
    );
    let canvas = max(params.canvas_scale_depth.xy, vec2<f32>(1.0));
    let scale = params.canvas_scale_depth.z;
    let depth_radius = max(params.canvas_scale_depth.w, 0.0001);
    let ndc = vec2<f32>(
        cam.x * scale / (canvas.x * 0.5),
        cam.y * scale / (canvas.y * 0.5),
    );
    let depth = clamp(0.5 + cam.z / (depth_radius * 2.0), 0.0, 1.0);

    var out: VertexOut;
    out.position = vec4<f32>(ndc.x, ndc.y, depth, 1.0);
    out.normal = normalize(vec3<f32>(
        dot(in.normal, params.row0.xyz),
        dot(in.normal, params.row1.xyz),
        dot(in.normal, params.row2.xyz),
    ));
    out.color = in.color;
    out.uv = in.uv;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let mode = i32(params.target_mode.w + 0.5);
    if (mode == 3) {
        let encoded = (normalize(in.normal) + vec3<f32>(1.0)) * 0.5;
        return vec4<f32>(encoded, in.color.a);
    }
    var base = in.color;
    if (mode == 2 && params.light.w > 0.5) {
        let texel = textureSample(base_texture, base_sampler, in.uv);
        base = vec4<f32>(base.rgb * texel.rgb, base.a);
    }
    let light_dir = normalize(params.light.xyz);
    var shade = clamp(dot(normalize(in.normal), -light_dir) * 0.75 + 0.35, 0.28, 1.0);
    if (mode == 2 && params.light.w > 0.5) {
        shade = 1.0;
    }
    let rgb = clamp(base.rgb * shade, vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(rgb, clamp(base.a, 0.0, 1.0));
}
"""


@dataclass(frozen=True, slots=True)
class Wgpu3DRenderResult:
    texture: WgpuRenderTargetHandle
    owned_targets: list[WgpuRenderTargetHandle]
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class _GeometryBatch:
    texture_id: str | None
    vertex_buffer: Any
    index_buffer: Any
    index_count: int
    uses_texture: bool


@dataclass(slots=True)
class _GeometryHandle:
    key: tuple[Any, ...]
    batches: tuple[_GeometryBatch, ...]


@dataclass(slots=True)
class _GeometryBatchData:
    texture_id: str | None
    vertices: np.ndarray
    indices: np.ndarray
    uses_texture: bool


@dataclass(slots=True)
class _SceneTextureHandle:
    key: tuple[Any, ...]
    texture: Any
    view: Any
    width: int
    height: int


@dataclass(slots=True)
class _RenderedLayerCacheEntry:
    key: tuple[Any, ...]
    target: WgpuRenderTargetHandle
    depth: WgpuRenderTargetHandle
    texture_cache_ids: frozenset[tuple[Any, ...]]
    warnings: tuple[str, ...]


class Wgpu3DLayerRenderer:
    """Render provider-neutral 3D scene payloads into wgpu textures."""

    def __init__(self, bundle: Any, *, target_format: str = "rgba8unorm") -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self.target_format = target_format
        self._pipeline: Any | None = None
        self._bind_group_layout: Any | None = None
        self._geometry: dict[str, _GeometryHandle] = {}
        self._textures: dict[tuple[Any, ...], _SceneTextureHandle] = {}
        self._layer_texture_cache_ids: dict[str, set[tuple[Any, ...]]] = {}
        self._render_cache: dict[str, _RenderedLayerCacheEntry] = {}
        self._default_texture: _SceneTextureHandle | None = None
        self._sampler: Any | None = None
        self._render_cache_hits = 0
        self._render_cache_misses = 0

    @property
    def render_cache_hits(self) -> int:
        return self._render_cache_hits

    @property
    def render_cache_misses(self) -> int:
        return self._render_cache_misses

    def render_layer(
        self,
        encoder: Any,
        layer: Any,
        *,
        width: int,
        height: int,
        create_target: Any,
    ) -> Wgpu3DRenderResult | None:
        layer_id = str(getattr(layer, "id", ""))
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            self._layer_texture_cache_ids[layer_id] = set()
            self._destroy_render_cache(layer_id)
            return None
        geometry = self._geometry_for_layer(layer, scene)
        if geometry is None or not geometry.batches:
            self._layer_texture_cache_ids[layer_id] = set()
            self._destroy_render_cache(layer_id)
            return None

        mode = preview_display_mode(getattr(layer, "display_mode_3d", "solid"))
        render_key = self._render_cache_key(
            layer,
            scene,
            geometry,
            width=width,
            height=height,
            mode=mode,
        )
        existing = self._render_cache.get(layer_id)
        if existing is not None and existing.key == render_key:
            self._render_cache_hits += 1
            self._layer_texture_cache_ids[layer_id] = set(existing.texture_cache_ids)
            return Wgpu3DRenderResult(
                texture=existing.target,
                owned_targets=[],
                warnings=existing.warnings,
            )

        self._render_cache_misses += 1
        target, depth = self._take_render_targets(
            layer_id,
            width=width,
            height=height,
            create_target=create_target,
        )
        default_texture = self._default_texture_handle()
        layer_texture_cache_ids: set[tuple[Any, ...]] = set()
        warnings: list[str] = []
        try:
            render_pass = encoder.begin_render_pass(
                color_attachments=[
                    {
                        "view": target.view,
                        "resolve_target": None,
                        "clear_value": (0.0, 0.0, 0.0, 0.0),
                        "load_op": self._wgpu.LoadOp.clear,
                        "store_op": self._wgpu.StoreOp.store,
                    }
                ],
                depth_stencil_attachment={
                    "view": depth.view,
                    "depth_clear_value": 1.0,
                    "depth_load_op": self._wgpu.LoadOp.clear,
                    "depth_store_op": self._wgpu.StoreOp.discard,
                },
            )
            render_pass.set_pipeline(self._render_pipeline)
            for batch in geometry.batches:
                texture_entry = (
                    self._texture_for_scene(scene, batch.texture_id)
                    if mode is PreviewDisplayMode.TEXTURED
                    else None
                )
                texture = texture_entry[0] if texture_entry is not None else None
                if texture_entry is not None:
                    layer_texture_cache_ids.add(texture_entry[1])
                use_texture = texture is not None and batch.uses_texture
                if mode is PreviewDisplayMode.TEXTURED and batch.uses_texture and texture is None:
                    warnings.append(
                        f"layer {getattr(layer, 'id', '<unknown>')}: "
                        f"3D texture {batch.texture_id!r} could not be loaded"
                    )
                bind_group = self._create_bind_group(
                    scene,
                    layer,
                    width=width,
                    height=height,
                    texture=texture or default_texture,
                    use_texture=use_texture,
                )
                render_pass.set_bind_group(0, bind_group, [], 0, 999999)
                render_pass.set_vertex_buffer(0, batch.vertex_buffer)
                render_pass.set_index_buffer(batch.index_buffer, self._index_format)
                render_pass.draw_indexed(batch.index_count, 1, 0, 0, 0)
            render_pass.end()
        except Exception:
            self._destroy_target_handle(target)
            self._destroy_target_handle(depth)
            raise

        if mode is PreviewDisplayMode.WIREFRAME:
            warnings.append(
                f"layer {getattr(layer, 'id', '<unknown>')}: "
                "wireframe 3D mode is rendered by the wgpu solid pass"
            )
        self._layer_texture_cache_ids[layer_id] = layer_texture_cache_ids
        warning_tuple = tuple(warnings)
        self._render_cache[layer_id] = _RenderedLayerCacheEntry(
            key=render_key,
            target=target,
            depth=depth,
            texture_cache_ids=frozenset(layer_texture_cache_ids),
            warnings=warning_tuple,
        )
        return Wgpu3DRenderResult(
            texture=target,
            owned_targets=[],
            warnings=warning_tuple,
        )

    def remove_stale(self, active_layer_ids: set[str]) -> None:
        active_ids = {str(layer_id) for layer_id in active_layer_ids}
        for layer_id in set(self._geometry) - active_ids:
            self._destroy_geometry(layer_id)
        for layer_id in set(self._render_cache) - active_ids:
            self._destroy_render_cache(layer_id)
        for layer_id in set(self._layer_texture_cache_ids) - active_ids:
            self._layer_texture_cache_ids.pop(layer_id, None)
        active_texture_keys: set[tuple[Any, ...]] = set()
        for layer_id in active_ids:
            active_texture_keys.update(self._layer_texture_cache_ids.get(layer_id, set()))
        for texture_key in set(self._textures) - active_texture_keys:
            self._destroy_texture(texture_key)

    def release(self) -> None:
        for layer_id in tuple(self._geometry):
            self._destroy_geometry(layer_id)
        for layer_id in tuple(self._render_cache):
            self._destroy_render_cache(layer_id)
        for texture_key in tuple(self._textures):
            self._destroy_texture(texture_key)
        self._layer_texture_cache_ids.clear()
        if self._default_texture is not None:
            self._destroy_texture_handle(self._default_texture)
            self._default_texture = None

    @property
    def _render_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        module = self._shader_module()
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._uniform_bind_group_layout]
        )
        try:
            self._pipeline = self._device.create_render_pipeline(
                layout=layout,
                vertex={
                    "module": module,
                    "entry_point": "vs_main",
                    "buffers": [
                        {
                            "array_stride": _VERTEX_STRIDE_BYTES,
                            "step_mode": self._wgpu.VertexStepMode.vertex,
                            "attributes": [
                                {
                                    "format": self._wgpu.VertexFormat.float32x3,
                                    "offset": 0,
                                    "shader_location": 0,
                                },
                                {
                                    "format": self._wgpu.VertexFormat.float32x3,
                                    "offset": 12,
                                    "shader_location": 1,
                                },
                                {
                                    "format": self._wgpu.VertexFormat.float32x4,
                                    "offset": 24,
                                    "shader_location": 2,
                                },
                                {
                                    "format": self._wgpu.VertexFormat.float32x2,
                                    "offset": 40,
                                    "shader_location": 3,
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
                            "format": self.target_format,
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
                depth_stencil={
                    "format": self._depth_format,
                    "depth_write_enabled": True,
                    "depth_compare": self._wgpu.CompareFunction.less,
                },
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuShaderError(f"failed to create 3D layer pipeline: {exc}") from exc
        return self._pipeline

    @property
    def _uniform_bind_group_layout(self) -> Any:
        if self._bind_group_layout is None:
            self._bind_group_layout = self._device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.VERTEX
                        | self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
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
                ]
            )
        return self._bind_group_layout

    @property
    def _texture_sampler(self) -> Any:
        if self._sampler is None:
            self._sampler = self._device.create_sampler(
                mag_filter="linear",
                min_filter="linear",
            )
        return self._sampler

    @property
    def _depth_format(self) -> Any:
        return getattr(self._wgpu.TextureFormat, "depth24plus", "depth24plus")

    @property
    def _index_format(self) -> Any:
        return getattr(self._wgpu.IndexFormat, "uint32", "uint32")

    def _shader_module(self) -> Any:
        try:
            return self._device.create_shader_module(code=_THREE_D_LAYER_WGSL)
        except Exception as exc:
            raise WgpuShaderError(f"WGSL shader '3d_layer' failed to compile: {exc}") from exc

    def _geometry_for_layer(self, layer: Any, scene: Scene3DDocument) -> _GeometryHandle | None:
        layer_id = str(getattr(layer, "id", ""))
        key = self._geometry_key(layer, scene)
        existing = self._geometry.get(layer_id)
        if existing is not None and existing.key == key:
            return existing
        if existing is not None:
            self._destroy_geometry(layer_id)

        batch_data = self._build_geometry_batches(scene)
        if not batch_data:
            return None
        batches: list[_GeometryBatch] = []
        for data in batch_data:
            vertex_buffer = self._device.create_buffer_with_data(
                data=np.ascontiguousarray(data.vertices).tobytes(),
                usage=self._wgpu.BufferUsage.VERTEX,
            )
            index_buffer = self._device.create_buffer_with_data(
                data=np.ascontiguousarray(data.indices).tobytes(),
                usage=self._wgpu.BufferUsage.INDEX,
            )
            batches.append(
                _GeometryBatch(
                    texture_id=data.texture_id,
                    vertex_buffer=vertex_buffer,
                    index_buffer=index_buffer,
                    index_count=int(data.indices.size),
                    uses_texture=data.uses_texture,
                )
            )
        handle = _GeometryHandle(
            key=key,
            batches=tuple(batches),
        )
        self._geometry[layer_id] = handle
        return handle

    def _build_geometry_batches(self, scene: Scene3DDocument) -> tuple[_GeometryBatchData, ...]:
        grouped: dict[str | None, list[tuple[np.ndarray, np.ndarray, bool]]] = {}
        for mesh in scene.meshes:
            for primitive in mesh.primitives:
                vertices = np.asarray(primitive.vertices, dtype=np.float32)
                indices = np.asarray(primitive.indices, dtype=np.uint32).reshape(-1)
                if vertices.size == 0 or indices.size == 0:
                    continue
                normals = self._primitive_normals(vertices, getattr(primitive, "normals", None))
                color = self._material_color(scene, getattr(primitive, "material_id", None))
                colors = np.repeat(np.asarray([color], dtype=np.float32), len(vertices), axis=0)
                texture_id = self._material_texture_id(
                    scene,
                    getattr(primitive, "material_id", None),
                )
                uvs, has_uvs = self._primitive_uvs(vertices, getattr(primitive, "uvs", None))
                uses_texture = texture_id is not None and has_uvs
                vertex_data = np.concatenate([vertices, normals, colors, uvs], axis=1)
                grouped.setdefault(texture_id if uses_texture else None, []).append(
                    (vertex_data, indices, uses_texture)
                )
        batches: list[_GeometryBatchData] = []
        for texture_id, chunks in grouped.items():
            vertex_chunks: list[np.ndarray] = []
            index_chunks: list[np.ndarray] = []
            vertex_offset = 0
            uses_texture = False
            for vertices, indices, chunk_uses_texture in chunks:
                vertex_chunks.append(vertices)
                index_chunks.append(indices + np.uint32(vertex_offset))
                vertex_offset += len(vertices)
                uses_texture = uses_texture or chunk_uses_texture
            batches.append(
                _GeometryBatchData(
                    texture_id=texture_id,
                    vertices=np.concatenate(vertex_chunks, axis=0).astype(np.float32, copy=False),
                    indices=np.concatenate(index_chunks, axis=0).astype(np.uint32, copy=False),
                    uses_texture=uses_texture,
                )
            )
        return tuple(batches)

    @staticmethod
    def _primitive_uvs(vertices: np.ndarray, uvs: Any) -> tuple[np.ndarray, bool]:
        uv_array = None if uvs is None else np.asarray(uvs, dtype=np.float32)
        if uv_array is not None and uv_array.shape == (len(vertices), 2):
            return uv_array.astype(np.float32, copy=False), True
        return (
            np.zeros((len(vertices), 2), dtype=np.float32),
            False,
        )

    def _primitive_normals(self, vertices: np.ndarray, normals: Any) -> np.ndarray:
        normal_array = None if normals is None else np.asarray(normals, dtype=np.float32)
        if normal_array is not None and normal_array.shape == vertices.shape:
            lengths = np.linalg.norm(normal_array, axis=1, keepdims=True)
            return np.divide(normal_array, np.maximum(lengths, 1e-6), dtype=np.float32)
        center = vertices.mean(axis=0, keepdims=True) if len(vertices) else np.zeros((1, 3))
        fallback = vertices - center
        lengths = np.linalg.norm(fallback, axis=1, keepdims=True)
        fallback = np.divide(fallback, np.maximum(lengths, 1e-6), dtype=np.float32)
        if len(fallback):
            fallback[lengths.reshape(-1) <= 1e-6] = np.asarray((0.0, 0.0, 1.0), dtype=np.float32)
        return fallback.astype(np.float32, copy=False)

    @staticmethod
    def _material_color(
        scene: Scene3DDocument,
        material_id: str | None,
    ) -> tuple[float, float, float, float]:
        material = scene.material_by_id(material_id)
        if material is None:
            return (0.72, 0.72, 0.72, 1.0)
        return tuple(float(max(0.0, min(1.0, value))) for value in material.base_color)  # type: ignore[return-value]

    @staticmethod
    def _material_texture_id(scene: Scene3DDocument, material_id: str | None) -> str | None:
        material = scene.material_by_id(material_id)
        if material is None:
            return None
        return material.base_color_texture_id

    def _create_bind_group(
        self,
        scene: Scene3DDocument,
        layer: Any,
        *,
        width: int,
        height: int,
        texture: _SceneTextureHandle,
        use_texture: bool,
    ) -> Any:
        params = self._uniform_params(
            scene,
            layer,
            width=width,
            height=height,
            use_texture=use_texture,
        )
        uniform = self._device.create_buffer_with_data(
            data=params.tobytes(),
            usage=self._wgpu.BufferUsage.UNIFORM,
        )
        return self._device.create_bind_group(
            layout=self._uniform_bind_group_layout,
            entries=[
                {
                    "binding": 0,
                    "resource": {
                        "buffer": uniform,
                        "offset": 0,
                        "size": params.nbytes,
                    },
                },
                {
                    "binding": 1,
                    "resource": texture.view,
                },
                {
                    "binding": 2,
                    "resource": self._texture_sampler,
                },
            ],
        )

    def _uniform_params(
        self,
        scene: Scene3DDocument,
        layer: Any,
        *,
        width: int,
        height: int,
        use_texture: bool,
    ) -> np.ndarray:
        camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        rotation = camera.rotation_matrix()
        low, high = scene.bounds()
        radius = float(np.linalg.norm(high - low) * 0.5)
        if radius <= 1e-6:
            radius = 1.0
        scale = min(width, height) * 0.42 * camera.zoom / radius
        mode = self._mode_id(preview_display_mode(getattr(layer, "display_mode_3d", "solid")))
        light = self._light_direction()
        return np.asarray(
            [
                float(width),
                float(height),
                float(scale),
                float(radius * 2.0 + camera.distance),
                float(camera.target[0]),
                float(camera.target[1]),
                float(camera.target[2]),
                float(mode),
                float(rotation[0, 0]),
                float(rotation[0, 1]),
                float(rotation[0, 2]),
                0.0,
                float(rotation[1, 0]),
                float(rotation[1, 1]),
                float(rotation[1, 2]),
                0.0,
                float(rotation[2, 0]),
                float(rotation[2, 1]),
                float(rotation[2, 2]),
                0.0,
                float(light[0]),
                float(light[1]),
                float(light[2]),
                1.0 if use_texture else 0.0,
            ],
            dtype=np.float32,
        )

    def _texture_for_scene(
        self,
        scene: Scene3DDocument,
        texture_id: str | None,
    ) -> tuple[_SceneTextureHandle, tuple[Any, ...]] | None:
        texture_asset = self._texture_asset_by_id(scene, texture_id)
        if texture_asset is None:
            return None
        texture_cache_id = self._texture_cache_id(scene, texture_asset)
        cache_key = self._texture_key(texture_asset)
        existing = self._textures.get(texture_cache_id)
        if existing is not None and existing.key == cache_key:
            return existing, texture_cache_id
        if existing is not None:
            self._destroy_texture(texture_cache_id)
        image = self._qimage_for_texture(texture_asset)
        if image is None or image.isNull():
            return None
        handle = self._upload_qimage_texture(cache_key, image)
        self._textures[texture_cache_id] = handle
        return handle, texture_cache_id

    def _default_texture_handle(self) -> _SceneTextureHandle:
        if self._default_texture is None:
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(QColor(255, 255, 255, 255))
            self._default_texture = self._upload_qimage_texture(("__default_white__",), image)
        return self._default_texture

    def _upload_qimage_texture(
        self,
        key: tuple[Any, ...],
        image: QImage,
    ) -> _SceneTextureHandle:
        rgba = qimage_to_straight_rgba(image)
        height, width = int(rgba.shape[0]), int(rgba.shape[1])
        texture = self._device.create_texture(
            size=(width, height, 1),
            usage=self._wgpu.TextureUsage.TEXTURE_BINDING | self._wgpu.TextureUsage.COPY_DST,
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        self._queue.write_texture(
            {"texture": texture, "origin": (0, 0, 0)},
            np.ascontiguousarray(rgba),
            {"bytes_per_row": width * 4, "rows_per_image": height},
            (width, height, 1),
        )
        return _SceneTextureHandle(
            key=key,
            texture=texture,
            view=texture.create_view(),
            width=width,
            height=height,
        )

    @staticmethod
    def _texture_asset_by_id(
        scene: Scene3DDocument,
        texture_id: str | None,
    ) -> TextureAsset | None:
        if texture_id is None:
            return None
        return next((texture for texture in scene.textures if texture.id == texture_id), None)

    @staticmethod
    def _qimage_for_texture(texture: TextureAsset) -> QImage | None:
        data = texture.metadata.get("data") if isinstance(texture.metadata, dict) else None
        if isinstance(data, bytes):
            return QImage.fromData(data)
        if texture.path is not None:
            return QImage(str(texture.path))
        if texture.color is not None:
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(QColor(*texture.color))
            return image
        return None

    @staticmethod
    def _texture_key(texture: TextureAsset) -> tuple[Any, ...]:
        data = texture.metadata.get("data") if isinstance(texture.metadata, dict) else None
        if isinstance(data, bytes):
            return (texture.id, "data", len(data), data[:32], data[-32:])
        if texture.path is not None:
            try:
                stat = texture.path.stat()
            except OSError:
                return (texture.id, "path", str(texture.path), None)
            return (
                texture.id,
                "path",
                str(texture.path),
                int(stat.st_size),
                int(stat.st_mtime_ns),
            )
        return (texture.id, "color", texture.color)

    @staticmethod
    def _texture_cache_id(
        scene: Scene3DDocument,
        texture: TextureAsset,
    ) -> tuple[Any, ...]:
        return (id(scene), getattr(scene, "id", None), texture.id)

    def _take_render_targets(
        self,
        layer_id: str,
        *,
        width: int,
        height: int,
        create_target: Any,
    ) -> tuple[WgpuRenderTargetHandle, WgpuRenderTargetHandle]:
        existing = self._render_cache.pop(layer_id, None)
        if (
            existing is not None
            and existing.target.width == int(width)
            and existing.target.height == int(height)
            and existing.target.format == self.target_format
            and existing.depth.width == int(width)
            and existing.depth.height == int(height)
        ):
            return existing.target, existing.depth
        if existing is not None:
            self._destroy_target_handle(existing.target)
            self._destroy_target_handle(existing.depth)
        return create_target(width, height), self._create_depth_target(width, height)

    def _create_depth_target(self, width: int, height: int) -> WgpuRenderTargetHandle:
        texture = self._device.create_texture(
            size=(int(width), int(height), 1),
            usage=self._wgpu.TextureUsage.RENDER_ATTACHMENT,
            format=self._depth_format,
        )
        return WgpuRenderTargetHandle(
            texture=texture,
            view=texture.create_view(),
            width=int(width),
            height=int(height),
            format=str(self._depth_format),
        )

    def _render_cache_key(
        self,
        layer: Any,
        scene: Scene3DDocument,
        geometry: _GeometryHandle,
        *,
        width: int,
        height: int,
        mode: PreviewDisplayMode,
    ) -> tuple[Any, ...]:
        texture_ids = tuple(
            sorted(
                {
                    str(batch.texture_id)
                    for batch in geometry.batches
                    if batch.uses_texture and batch.texture_id is not None
                }
            )
        )
        texture_signature = (
            self._scene_texture_signature(scene, texture_ids)
            if mode is PreviewDisplayMode.TEXTURED
            else ()
        )
        return (
            str(getattr(layer, "id", "")),
            int(width),
            int(height),
            str(mode.value),
            geometry.key,
            self._camera_signature(layer, scene),
            _stable_signature(getattr(layer, "lighting_3d", None)),
            texture_signature,
        )

    def _scene_texture_signature(
        self,
        scene: Scene3DDocument,
        texture_ids: tuple[str, ...],
    ) -> tuple[Any, ...]:
        signature: list[tuple[Any, ...]] = []
        for texture_id in texture_ids:
            texture = self._texture_asset_by_id(scene, texture_id)
            if texture is None:
                signature.append((texture_id, None))
                continue
            signature.append((texture_id, self._texture_key(texture)))
        return tuple(signature)

    @staticmethod
    def _camera_signature(layer: Any, scene: Scene3DDocument) -> tuple[Any, ...]:
        camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        return (
            round(float(camera.yaw_degrees), 6),
            round(float(camera.pitch_degrees), 6),
            round(float(camera.zoom), 6),
            tuple(round(float(value), 6) for value in camera.target),
            round(float(camera.distance), 6),
        )

    @staticmethod
    def _mode_id(mode: PreviewDisplayMode) -> int:
        return {
            PreviewDisplayMode.SOLID: 0,
            PreviewDisplayMode.WIREFRAME: 1,
            PreviewDisplayMode.TEXTURED: 2,
            PreviewDisplayMode.NORMAL_DEBUG: 3,
        }[mode]

    @staticmethod
    def _light_direction() -> np.ndarray:
        light = np.asarray((-0.4, -0.7, -1.0), dtype=np.float32)
        length = float(np.linalg.norm(light))
        if length <= 1e-6:
            return np.asarray((0.0, 0.0, -1.0), dtype=np.float32)
        return light / length

    @staticmethod
    def _geometry_key(layer: Any, scene: Scene3DDocument) -> tuple[Any, ...]:
        primitive_counts: list[tuple[Any, ...]] = []
        material_signatures = {
            material.id: (
                tuple(float(value) for value in material.base_color),
                material.base_color_texture_id,
            )
            for material in scene.materials
        }
        for mesh in scene.meshes:
            for primitive in mesh.primitives:
                vertices = getattr(primitive, "vertices", None)
                indices = getattr(primitive, "indices", None)
                uvs = getattr(primitive, "uvs", None)
                primitive_counts.append(
                    (
                        int(getattr(vertices, "size", 0)),
                        int(getattr(indices, "size", 0)),
                        int(getattr(uvs, "size", 0)),
                        getattr(primitive, "material_id", None),
                        material_signatures.get(getattr(primitive, "material_id", None)),
                    )
                )
        return (
            str(getattr(layer, "id", "")),
            id(scene),
            getattr(scene, "id", None),
            tuple(primitive_counts),
        )

    def _destroy_geometry(self, layer_id: str) -> None:
        handle = self._geometry.pop(layer_id, None)
        if handle is None:
            return
        for batch in handle.batches:
            for buffer in (batch.vertex_buffer, batch.index_buffer):
                destroy = getattr(buffer, "destroy", None)
                if callable(destroy):
                    destroy()

    def _destroy_texture(self, texture_key: tuple[Any, ...]) -> None:
        handle = self._textures.pop(texture_key, None)
        if handle is not None:
            self._destroy_texture_handle(handle)

    def _destroy_render_cache(self, layer_id: str) -> None:
        entry = self._render_cache.pop(layer_id, None)
        if entry is None:
            return
        self._destroy_target_handle(entry.target)
        self._destroy_target_handle(entry.depth)

    @staticmethod
    def _destroy_texture_handle(handle: _SceneTextureHandle) -> None:
        destroy = getattr(handle.texture, "destroy", None)
        if callable(destroy):
            destroy()

    @staticmethod
    def _destroy_target_handle(handle: WgpuRenderTargetHandle) -> None:
        destroy = getattr(handle.texture, "destroy", None)
        if callable(destroy):
            destroy()


def _stable_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(key), _stable_signature(val)) for key, val in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_stable_signature(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if is_dataclass(value):
        return tuple(
            (field.name, _stable_signature(getattr(value, field.name))) for field in fields(value)
        )
    return repr(value)
