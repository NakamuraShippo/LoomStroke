"""Composite a live filter against its input with the canvas blend equations."""

from __future__ import annotations

from typing import Any

import numpy as np

from .shader_library import BLEND_COLOR_WGSL, WGPU_BLEND_MODE_IDS
from .types import WgpuShaderError

_FILTER_BLEND_WGSL = """
@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var filtered_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> blend_mode: vec4<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertex_index: u32) -> VertexOut {
    var corners = array<vec2<f32>, 6>(
        vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0),
        vec2<f32>(0.0, 1.0), vec2<f32>(0.0, 1.0),
        vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0),
    );
    let p = corners[vertex_index];
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

$BLEND_COLOR_WGSL

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    let filtered = textureSample(filtered_tex, tex_sampler, in.uv);
    let mode = i32(blend_mode.x + 0.5);
    if (mode == 26) {
        let x = u32(in.position.x);
        let y = u32(in.position.y);
        let sample = x * 1664525u + y * 1013904223u;
        return vec4<f32>(select(src.rgb, filtered.rgb, sample < 0x80000000u), filtered.a);
    }
    return vec4<f32>(
        clamp(blend_color(filtered.rgb, src.rgb, mode), vec3<f32>(0.0), vec3<f32>(1.0)),
        filtered.a,
    );
}
""".replace("$BLEND_COLOR_WGSL", BLEND_COLOR_WGSL)

_ALPHA_CONVERT_WGSL = """
@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var<uniform> direction: vec4<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertex_index: u32) -> VertexOut {
    var corners = array<vec2<f32>, 6>(
        vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0),
        vec2<f32>(0.0, 1.0), vec2<f32>(0.0, 1.0),
        vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0),
    );
    let p = corners[vertex_index];
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    if (direction.x > 0.5) {
        return vec4<f32>(src.rgb * src.a, src.a);
    }
    if (src.a <= 0.0) {
        return vec4<f32>(0.0);
    }
    return vec4<f32>(src.rgb / src.a, src.a);
}
"""


class WgpuFilterBlendProcessor:
    def __init__(self, bundle: Any) -> None:
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._layout: Any | None = None
        self._pipeline: Any | None = None
        self._convert_layout: Any | None = None
        self._convert_pipeline: Any | None = None

    def convert_alpha(
        self,
        encoder: Any,
        source: Any,
        *,
        to_premultiplied: bool,
        sampler: Any,
        create_target: Any,
    ) -> Any:
        target = create_target(source.width, source.height)
        values = np.array([float(to_premultiplied), 0.0, 0.0, 0.0], dtype=np.float32)
        uniform = self._device.create_buffer_with_data(
            data=values.tobytes(), usage=self._wgpu.BufferUsage.UNIFORM
        )
        bind_group = self._device.create_bind_group(
            layout=self._alpha_convert_layout,
            entries=[
                {"binding": 0, "resource": sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": {"buffer": uniform, "offset": 0, "size": values.nbytes}},
            ],
        )
        self._draw(encoder, target, self._alpha_convert_pipeline, bind_group)
        return target

    def apply(
        self,
        encoder: Any,
        source: Any,
        filtered: Any,
        *,
        blend_mode: str,
        sampler: Any,
        create_target: Any,
    ) -> Any:
        target = create_target(source.width, source.height)
        mode = WGPU_BLEND_MODE_IDS.get(blend_mode, 0)
        values = np.array([float(mode), 0.0, 0.0, 0.0], dtype=np.float32)
        uniform = self._device.create_buffer_with_data(
            data=values.tobytes(), usage=self._wgpu.BufferUsage.UNIFORM
        )
        bind_group = self._device.create_bind_group(
            layout=self._bind_group_layout,
            entries=[
                {"binding": 0, "resource": sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": filtered.view},
                {"binding": 3, "resource": {"buffer": uniform, "offset": 0, "size": values.nbytes}},
            ],
        )
        self._draw(encoder, target, self._render_pipeline, bind_group)
        return target

    def _draw(self, encoder: Any, target: Any, pipeline: Any, bind_group: Any) -> None:
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

    @property
    def _alpha_convert_layout(self) -> Any:
        if self._convert_layout is None:
            self._convert_layout = self._device.create_bind_group_layout(
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
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                ]
            )
        return self._convert_layout

    @property
    def _alpha_convert_pipeline(self) -> Any:
        if self._convert_pipeline is None:
            self._convert_pipeline = self._make_pipeline(
                _ALPHA_CONVERT_WGSL, self._alpha_convert_layout
            )
        return self._convert_pipeline

    @property
    def _bind_group_layout(self) -> Any:
        if self._layout is None:
            self._layout = self._device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "sampler": {"type": self._wgpu.SamplerBindingType.filtering},
                    },
                    *(
                        {
                            "binding": binding,
                            "visibility": self._wgpu.ShaderStage.FRAGMENT,
                            "texture": {
                                "sample_type": self._wgpu.TextureSampleType.float,
                                "view_dimension": "2d",
                            },
                        }
                        for binding in (1, 2)
                    ),
                    {
                        "binding": 3,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                ]
            )
        return self._layout

    @property
    def _render_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        self._pipeline = self._make_pipeline(_FILTER_BLEND_WGSL, self._bind_group_layout)
        return self._pipeline

    def _make_pipeline(self, source: str, bind_group_layout: Any) -> Any:
        module = self._device.create_shader_module(code=source)
        layout = self._device.create_pipeline_layout(bind_group_layouts=[bind_group_layout])
        try:
            return self._device.create_render_pipeline(
                layout=layout,
                vertex={"module": module, "entry_point": "vs_main", "buffers": []},
                fragment={
                    "module": module,
                    "entry_point": "fs_main",
                    "targets": [
                        {
                            "format": self._wgpu.TextureFormat.rgba8unorm,
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
            raise WgpuShaderError(f"failed to create filter blend pipeline: {exc}") from exc
