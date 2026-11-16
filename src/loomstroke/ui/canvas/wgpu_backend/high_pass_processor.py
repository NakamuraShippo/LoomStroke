"""Two-texture high-pass pass built on the shared Gaussian compute blur."""

from __future__ import annotations

from typing import Any

from .types import WgpuShaderError

_HIGH_PASS_WGSL = """
@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var blur_tex: texture_2d<f32>;

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
    let blurred = textureSample(blur_tex, tex_sampler, in.uv);
    let high = clamp(vec3<f32>(128.0 / 255.0) + (src.rgb - blurred.rgb) * 0.5,
                     vec3<f32>(0.0), vec3<f32>(1.0));
    return vec4<f32>(high, src.a);
}
"""


class WgpuHighPassProcessor:
    def __init__(self, bundle: Any, compute_processor: Any) -> None:
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._compute = compute_processor
        self._layout: Any | None = None
        self._pipeline: Any | None = None

    def apply(
        self,
        encoder: Any,
        source: Any,
        *,
        radius: float,
        raster_scale: int,
        sampler: Any,
        create_target: Any,
    ) -> tuple[Any, list[Any]]:
        blur = self._compute.gaussian_blur(
            encoder, source, radius=radius, raster_scale=raster_scale
        )
        target = create_target(source.width, source.height)
        bind_group = self._device.create_bind_group(
            layout=self._bind_group_layout,
            entries=[
                {"binding": 0, "resource": sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": blur.texture.view},
            ],
        )
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
        render_pass.set_pipeline(self._render_pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        return target, [*blur.owned_targets, target]

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
                ]
            )
        return self._layout

    @property
    def _render_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        module = self._device.create_shader_module(code=_HIGH_PASS_WGSL)
        layout = self._device.create_pipeline_layout(bind_group_layouts=[self._bind_group_layout])
        try:
            self._pipeline = self._device.create_render_pipeline(
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
            raise WgpuShaderError(f"failed to create high-pass pipeline: {exc}") from exc
        return self._pipeline
