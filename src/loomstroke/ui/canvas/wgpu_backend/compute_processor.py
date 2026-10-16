"""WGSL compute passes for wgpu effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from loomstroke.core.filters.mosaic import mosaic_cell_size
from loomstroke.core.filters.noise_reduction import bilateral_denoise_params

from .types import WgpuRenderTargetHandle, WgpuShaderError

_COMPUTE_WGSL = """
struct ComputeParams {
    op: f32,
    strength: f32,
    radius: f32,
    axis: f32,
    sigma_luma: f32,
    sigma_chroma: f32,
    origin_x: f32,
    origin_y: f32,
};

@group(0) @binding(0) var src_tex: texture_2d<f32>;
@group(0) @binding(1) var dst_tex: texture_storage_2d<rgba8unorm, write>;
@group(0) @binding(2) var<uniform> params: ComputeParams;

fn sample_px(pos: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    return textureLoad(src_tex, clamp(pos, vec2<i32>(0), dims - vec2<i32>(1)), 0);
}

// cv2.GaussianBlur's default BORDER_REFLECT_101, so a blur wider than the
// layer lands on the same pixels as the CPU effect the export path runs.
fn reflect101(index: i32, size: i32) -> i32 {
    if (size <= 1) {
        return 0;
    }
    let period = 2 * (size - 1);
    // reflect101 is even around zero. Nonnegative remainder also avoids
    // backend-dependent signed-remainder lowering at the texture edges.
    var folded = abs(index) % period;
    if (folded >= size) {
        folded = period - folded;
    }
    return folded;
}

fn sample_reflect(pos: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    let x = reflect101(pos.x, dims.x);
    let y = reflect101(pos.y, dims.y);
    return textureLoad(src_tex, vec2<i32>(x, y), 0);
}

// One axis of a separable gaussian.  ``strength`` carries sigma and the host
// dispatches this twice (horizontal, then vertical).  Taps are weighted in
// premultiplied alpha so a blur across a soft edge does not drag the colour
// of fully transparent texels into the result; the store un-premultiplies
// again because layer textures are straight RGBA.
fn gaussian(pos: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    let radius = i32(params.radius + 0.5);
    if (radius <= 0) {
        return sample_px(pos, dims);
    }
    let step = select(vec2<i32>(0, 1), vec2<i32>(1, 0), params.axis < 0.5);
    let sigma = max(params.strength, 0.0001);
    let denom = 2.0 * sigma * sigma;
    var accum = vec4<f32>(0.0);
    var weight_sum = 0.0;
    for (var i = -radius; i <= radius; i = i + 1) {
        let offset = f32(i);
        let weight = exp(-(offset * offset) / denom);
        let texel = sample_reflect(pos + step * i, dims);
        accum = accum + vec4<f32>(texel.rgb * texel.a, texel.a) * weight;
        weight_sum = weight_sum + weight;
    }
    let premultiplied = accum / weight_sum;
    if (premultiplied.a <= 0.0) {
        return vec4<f32>(0.0, 0.0, 0.0, 0.0);
    }
    return vec4<f32>(premultiplied.rgb / premultiplied.a, premultiplied.a);
}

fn luma_of(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.299, 0.587, 0.114));
}

// One axis of the mosaic block average.  ``radius`` carries the cell size and
// the host dispatches horizontal then vertical; every row of a block shares
// the same column range, so the mean-of-means equals the 2D block mean.  The
// grid anchors at the origin and edge blocks average only the pixels that
// exist — ``mosaic_block_average`` on the CPU follows the same rule.  Taps
// are weighted in premultiplied alpha (layer textures are straight RGBA) so
// transparent texels do not drag black into a block.
fn mosaic_axis(pos: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    let cell = i32(params.radius + 0.5);
    if (cell <= 1) {
        return sample_px(pos, dims);
    }
    let horizontal = params.axis < 0.5;
    let coord = select(pos.y, pos.x, horizontal);
    let limit = select(dims.y, dims.x, horizontal);
    let offset = i32(select(params.origin_y, params.origin_x, horizontal));
    let start = ((coord + offset) / cell) * cell - offset;
    let end = min(start + cell, limit);
    var accum = vec4<f32>(0.0);
    for (var i = start; i < end; i = i + 1) {
        let tap = select(vec2<i32>(pos.x, i), vec2<i32>(i, pos.y), horizontal);
        let texel = sample_px(tap, dims);
        accum = accum + vec4<f32>(texel.rgb * texel.a, texel.a);
    }
    let mean = accum / f32(end - start);
    if (mean.a <= 0.0) {
        return vec4<f32>(0.0, 0.0, 0.0, 0.0);
    }
    return vec4<f32>(mean.rgb / mean.a, mean.a);
}

// Denoise: a bilateral whose spatial reach comes from Strength and whose
// range falloff is split into luma (Strength) and chroma (Color Strength),
// mirroring OpenCV's h / hColor split.  The CPU effect runs the identical
// kernel so merge and export match the canvas.
fn bilateral(pos: vec2<i32>, dims: vec2<i32>) -> vec4<f32> {
    let radius = i32(params.radius + 0.5);
    if (radius <= 0) {
        return sample_px(pos, dims);
    }
    let center = sample_px(pos, dims);
    let centre_luma = luma_of(center.rgb);
    let centre_chroma = center.rgb - vec3<f32>(centre_luma);
    let spatial_sigma = max(f32(radius) * 0.5, 0.0001);
    let spatial_denom = 2.0 * spatial_sigma * spatial_sigma;
    let luma_denom = 2.0 * max(params.sigma_luma, 0.0001) * max(params.sigma_luma, 0.0001);
    let chroma_denom = 2.0 * max(params.sigma_chroma, 0.0001) * max(params.sigma_chroma, 0.0001);
    var accum = vec4<f32>(0.0);
    var weight_sum = 0.0;
    for (var y = -radius; y <= radius; y = y + 1) {
        for (var x = -radius; x <= radius; x = x + 1) {
            let sample = sample_px(pos + vec2<i32>(x, y), dims);
            let offset = f32(x * x + y * y);
            let sample_luma = luma_of(sample.rgb);
            let luma_diff = sample_luma - centre_luma;
            let chroma_diff = length((sample.rgb - vec3<f32>(sample_luma)) - centre_chroma);
            let weight = exp(
                -offset / spatial_denom
                - (luma_diff * luma_diff) / luma_denom
                - (chroma_diff * chroma_diff) / chroma_denom
            );
            accum = accum + sample * weight;
            weight_sum = weight_sum + weight;
        }
    }
    return accum / weight_sum;
}

@compute @workgroup_size(8, 8)
fn cs_main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let dims_u = textureDimensions(dst_tex);
    if (gid.x >= dims_u.x || gid.y >= dims_u.y) {
        return;
    }
    let pos = vec2<i32>(i32(gid.x), i32(gid.y));
    let dims = vec2<i32>(i32(dims_u.x), i32(dims_u.y));
    let op = i32(params.op + 0.5);
    var color = sample_px(pos, dims);
    if (op == 2) {
        color = gaussian(pos, dims);
    } else if (op == 3) {
        color = bilateral(pos, dims);
    } else if (op == 4) {
        color = mosaic_axis(pos, dims);
    }
    textureStore(dst_tex, pos, color);
}
"""


@dataclass(frozen=True, slots=True)
class WgpuComputeResult:
    texture: WgpuRenderTargetHandle
    owned_targets: list[WgpuRenderTargetHandle]


class WgpuComputeProcessor:
    """Small compute processor for copy, gaussian blur, and bilateral denoise."""

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._pipeline: Any | None = None
        self._layout: Any | None = None

    def copy(self, encoder: Any, source: Any) -> WgpuComputeResult:
        return self._dispatch(encoder, source, op=1, strength=1.0)

    MAX_BLUR_RADIUS = 250

    def gaussian_blur(
        self, encoder: Any, source: Any, *, radius: float = 5.0, raster_scale: int = 1
    ) -> WgpuComputeResult:
        """Blur *source* with a separable gaussian of the given pixel radius.

        Matches ``GaussianBlurEffect``: a non-positive radius is a no-op, and
        sigma follows OpenCV's ``ksize``-derived default for ``ksize =
        2*radius+1`` so the GPU and CPU paths agree.
        """

        logical_radius = max(0, min(self.MAX_BLUR_RADIUS, int(float(radius))))
        scale = max(1, int(raster_scale))
        kernel_radius = logical_radius * scale
        if kernel_radius <= 0:
            return WgpuComputeResult(texture=source, owned_targets=[])
        sigma = (0.3 * (float(logical_radius) - 1.0) + 0.8) * scale
        horizontal = self._dispatch(
            encoder,
            source,
            op=2,
            strength=sigma,
            radius=kernel_radius,
            axis=0,
        )
        vertical = self._dispatch(
            encoder,
            horizontal.texture,
            op=2,
            strength=sigma,
            radius=kernel_radius,
            axis=1,
        )
        return WgpuComputeResult(
            texture=vertical.texture,
            owned_targets=[horizontal.texture, vertical.texture],
        )

    def mosaic(
        self,
        encoder: Any,
        source: Any,
        *,
        cell_size: float = 8.0,
        raster_scale: int = 1,
        texel_origin: tuple[int, int] = (0, 0),
    ) -> WgpuComputeResult:
        """Pixelate *source* on a square grid of ``cell_size`` pixels.

        Two separable passes; ``MosaicEffect`` runs the identical block
        average on the CPU via
        :func:`loomstroke.core.filters.mosaic.mosaic_block_average`.
        """

        cell = mosaic_cell_size({"cell_size": cell_size})
        if cell <= 1:
            return WgpuComputeResult(texture=source, owned_targets=[])
        cell *= max(1, int(raster_scale))
        horizontal = self._dispatch(
            encoder, source, op=4, strength=0.0, radius=cell, axis=0, texel_origin=texel_origin
        )
        vertical = self._dispatch(
            encoder,
            horizontal.texture,
            op=4,
            strength=0.0,
            radius=cell,
            axis=1,
            texel_origin=texel_origin,
        )
        return WgpuComputeResult(
            texture=vertical.texture,
            owned_targets=[horizontal.texture, vertical.texture],
        )

    def bilateral(
        self,
        encoder: Any,
        source: Any,
        *,
        strength: float = 10.0,
        color_strength: float = 10.0,
        raster_scale: int = 1,
    ) -> WgpuComputeResult:
        """Denoise *source*, honouring Strength and Color Strength.

        ``NoiseReductionEffect`` runs the identical kernel on the CPU — see
        :func:`loomstroke.core.filters.noise_reduction.bilateral_denoise_params`
        for the shared parameter derivation.
        """

        radius, sigma_luma, sigma_chroma = bilateral_denoise_params(strength, color_strength)
        if radius <= 0:
            return WgpuComputeResult(texture=source, owned_targets=[])
        return self._dispatch(
            encoder,
            source,
            op=3,
            strength=float(strength),
            radius=radius * max(1, int(raster_scale)),
            sigma_luma=sigma_luma,
            sigma_chroma=sigma_chroma,
        )

    def _dispatch(
        self,
        encoder: Any,
        source: Any,
        *,
        op: int,
        strength: float,
        radius: int = 0,
        axis: int = 0,
        sigma_luma: float = 0.0,
        sigma_chroma: float = 0.0,
        texel_origin: tuple[int, int] = (0, 0),
    ) -> WgpuComputeResult:
        target = self._create_storage_target(source.width, source.height)
        params = np.array(
            [
                float(op),
                float(strength),
                float(radius),
                float(axis),
                float(sigma_luma),
                float(sigma_chroma),
                float(texel_origin[0]),
                float(texel_origin[1]),
            ],
            dtype=np.float32,
        )
        uniform = self._device.create_buffer_with_data(
            data=params.tobytes(),
            usage=self._wgpu.BufferUsage.UNIFORM,
        )
        bind_group = self._device.create_bind_group(
            layout=self._bind_group_layout,
            entries=[
                {"binding": 0, "resource": source.view},
                {"binding": 1, "resource": target.view},
                {"binding": 2, "resource": {"buffer": uniform, "offset": 0, "size": params.nbytes}},
            ],
        )
        compute_pass = encoder.begin_compute_pass()
        compute_pass.set_pipeline(self._compute_pipeline)
        compute_pass.set_bind_group(0, bind_group, [], 0, 999999)
        compute_pass.dispatch_workgroups(
            max(1, (source.width + 7) // 8),
            max(1, (source.height + 7) // 8),
            1,
        )
        compute_pass.end()
        return WgpuComputeResult(texture=target, owned_targets=[target])

    def _create_storage_target(self, width: int, height: int) -> WgpuRenderTargetHandle:
        texture = self._device.create_texture(
            size=(int(width), int(height), 1),
            usage=(
                self._wgpu.TextureUsage.STORAGE_BINDING
                | self._wgpu.TextureUsage.TEXTURE_BINDING
                | self._wgpu.TextureUsage.COPY_SRC
            ),
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        return WgpuRenderTargetHandle(
            texture=texture,
            view=texture.create_view(),
            width=int(width),
            height=int(height),
            format="rgba8unorm",
        )

    @property
    def _bind_group_layout(self) -> Any:
        if self._layout is None:
            self._layout = self._device.create_bind_group_layout(
                entries=[
                    {
                        "binding": 0,
                        "visibility": self._wgpu.ShaderStage.COMPUTE,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                    {
                        "binding": 1,
                        "visibility": self._wgpu.ShaderStage.COMPUTE,
                        "storage_texture": {
                            "access": self._wgpu.StorageTextureAccess.write_only,
                            "format": self._wgpu.TextureFormat.rgba8unorm,
                            "view_dimension": "2d",
                        },
                    },
                    {
                        "binding": 2,
                        "visibility": self._wgpu.ShaderStage.COMPUTE,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                ]
            )
        return self._layout

    @property
    def _compute_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        module = self._device.create_shader_module(code=_COMPUTE_WGSL)
        layout = self._device.create_pipeline_layout(bind_group_layouts=[self._bind_group_layout])
        try:
            self._pipeline = self._device.create_compute_pipeline(
                layout=layout,
                compute={"module": module, "entry_point": "cs_main"},
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuShaderError(f"failed to create compute pipeline: {exc}") from exc
        return self._pipeline
