"""WGSL shader and pipeline cache for the wgpu backend."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np

from .blend_binding_cache import BlendBindingCache
from .types import WgpuShaderError

BLEND_COMPOSITOR_WGSL = """
struct BlendParams {
    canvas_size: vec2<f32>,
    dest_origin: vec2<f32>,
    dest_size: vec2<f32>,
    uv_origin: vec2<f32>,
    uv_size: vec2<f32>,
    src_size: vec2<f32>,
    inv_affine_x: vec4<f32>,
    inv_affine_y: vec4<f32>,
    opacity: f32,
    blend_mode: f32,
    dissolve_seed: f32,
    mask_enabled: f32,
    transform_enabled: f32,
    atop_enabled: f32,
    _pad0: f32,
    _pad1: f32,
    // View-resolution compositing: maps target pixels back to canvas pixels
    // so all canvas-space placement/mask math stays untouched.
    view_inv_x: vec4<f32>,
    view_inv_y: vec4<f32>,
    canvas_dims: vec2<f32>,
    view_enabled: f32,
    src_in_target_space: f32,
    src_premultiplied: f32,
    src_prescaled: f32,
    src_replace: f32,
    _pad4: f32,
    mask_origin: vec2<f32>,
    mask_source_size: vec2<f32>,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var dst_tex: texture_2d<f32>;
@group(0) @binding(2) var src_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> params: BlendParams;
@group(0) @binding(4) var mask_tex: texture_2d<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) screen_uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) vertex_index: u32) -> VertexOut {
    var corners = array<vec2<f32>, 6>(
        vec2<f32>(0.0, 0.0),
        vec2<f32>(1.0, 0.0),
        vec2<f32>(0.0, 1.0),
        vec2<f32>(0.0, 1.0),
        vec2<f32>(1.0, 0.0),
        vec2<f32>(1.0, 1.0),
    );
    let p = corners[vertex_index];
    let ndc = vec2<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0);
    var out: VertexOut;
    out.position = vec4<f32>(ndc, 0.0, 1.0);
    out.screen_uv = p;
    return out;
}

fn hash21(p_in: vec2<f32>) -> f32 {
    var p = fract((p_in + vec2<f32>(params.dissolve_seed, 0.0)) * vec2<f32>(123.34, 456.21));
    p = p + dot(p, p + vec2<f32>(45.32, 45.32));
    return fract(p.x * p.y);
}

fn rgb2hsl(c: vec3<f32>) -> vec3<f32> {
    let mx = max(c.r, max(c.g, c.b));
    let mn = min(c.r, min(c.g, c.b));
    let l = (mx + mn) * 0.5;
    let d = mx - mn;
    if (d < 0.001) {
        return vec3<f32>(0.0, 0.0, l);
    }
    let s = select(d / (mx + mn), d / (2.0 - mx - mn), l > 0.5);
    var h = 0.0;
    if (mx == c.r) {
        h = (c.g - c.b) / d;
        if (c.g < c.b) {
            h = h + 6.0;
        }
    } else if (mx == c.g) {
        h = (c.b - c.r) / d + 2.0;
    } else {
        h = (c.r - c.g) / d + 4.0;
    }
    return vec3<f32>(h / 6.0, s, l);
}

fn hue2rgb(p: f32, q: f32, t_in: f32) -> f32 {
    var t = t_in;
    if (t < 0.0) {
        t = t + 1.0;
    }
    if (t > 1.0) {
        t = t - 1.0;
    }
    if (t < 1.0 / 6.0) {
        return p + (q - p) * 6.0 * t;
    }
    if (t < 0.5) {
        return q;
    }
    if (t < 2.0 / 3.0) {
        return p + (q - p) * (2.0 / 3.0 - t) * 6.0;
    }
    return p;
}

fn hsl2rgb(hsl: vec3<f32>) -> vec3<f32> {
    if (hsl.y < 0.001) {
        return vec3<f32>(hsl.z);
    }
    let q = select(
        hsl.z * (1.0 + hsl.y),
        hsl.z + hsl.y - hsl.z * hsl.y,
        hsl.z >= 0.5,
    );
    let p = 2.0 * hsl.z - q;
    return vec3<f32>(
        hue2rgb(p, q, hsl.x + 1.0 / 3.0),
        hue2rgb(p, q, hsl.x),
        hue2rgb(p, q, hsl.x - 1.0 / 3.0),
    );
}

fn blend_color(s: vec3<f32>, d: vec3<f32>, mode: i32) -> vec3<f32> {
    if (mode == 0) {
        return s;
    }
    if (mode == 1) {
        return min(s, d);
    }
    if (mode == 2) {
        return s * d;
    }
    if (mode == 3) {
        return clamp(1.0 - (1.0 - d) / max(s, vec3<f32>(0.001)), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 4) {
        return clamp(s + d - vec3<f32>(1.0), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 5) {
        let ls = dot(s, vec3<f32>(0.299, 0.587, 0.114));
        let ld = dot(d, vec3<f32>(0.299, 0.587, 0.114));
        return select(d, s, ls < ld);
    }
    if (mode == 6) {
        return max(s, d);
    }
    if (mode == 7) {
        return 1.0 - (1.0 - s) * (1.0 - d);
    }
    if (mode == 8) {
        return clamp(d / max(1.0 - s, vec3<f32>(0.001)), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 9) {
        return clamp(s + d, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 10) {
        let ls = dot(s, vec3<f32>(0.299, 0.587, 0.114));
        let ld = dot(d, vec3<f32>(0.299, 0.587, 0.114));
        return select(d, s, ls > ld);
    }
    if (mode == 11) {
        return select(
            2.0 * s * d,
            1.0 - 2.0 * (1.0 - s) * (1.0 - d),
            d >= vec3<f32>(0.5),
        );
    }
    if (mode == 12) {
        let t = 2.0 * s;
        let dark = d * (t + d * (1.0 - t));
        let light = sqrt(max(d, vec3<f32>(0.0))) * (t - 1.0) + 2.0 * d * (1.0 - s);
        return select(dark, light, s >= vec3<f32>(0.5));
    }
    if (mode == 13) {
        return select(
            2.0 * s * d,
            1.0 - 2.0 * (1.0 - s) * (1.0 - d),
            s >= vec3<f32>(0.5),
        );
    }
    if (mode == 14) {
        let burn = clamp(
            1.0 - (1.0 - d) / max(2.0 * s, vec3<f32>(0.001)),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
        let dodge = clamp(
            d / max(2.0 * (1.0 - s), vec3<f32>(0.001)),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
        return select(burn, dodge, s >= vec3<f32>(0.5));
    }
    if (mode == 15) {
        return clamp(d + 2.0 * s - vec3<f32>(1.0), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 16) {
        return select(
            min(d, 2.0 * s),
            max(d, 2.0 * s - vec3<f32>(1.0)),
            s >= vec3<f32>(0.5),
        );
    }
    if (mode == 17) {
        return abs(s - d);
    }
    if (mode == 18) {
        return s + d - 2.0 * s * d;
    }
    if (mode == 19) {
        return clamp(d - s, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 20) {
        return clamp(d / max(s, vec3<f32>(0.001)), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (mode == 21) {
        let hs = rgb2hsl(s);
        let hd = rgb2hsl(d);
        return hsl2rgb(vec3<f32>(hs.x, hd.y, hd.z));
    }
    if (mode == 22) {
        let hs = rgb2hsl(s);
        let hd = rgb2hsl(d);
        return hsl2rgb(vec3<f32>(hd.x, hs.y, hd.z));
    }
    if (mode == 23) {
        let hs = rgb2hsl(s);
        let hd = rgb2hsl(d);
        return hsl2rgb(vec3<f32>(hs.x, hs.y, hd.z));
    }
    if (mode == 24) {
        let hs = rgb2hsl(s);
        let hd = rgb2hsl(d);
        return hsl2rgb(vec3<f32>(hd.x, hd.y, hs.z));
    }
    if (mode == 25) {
        return select(vec3<f32>(0.0), vec3<f32>(1.0), (s + d) >= vec3<f32>(1.0));
    }
    return s;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let dst = textureSample(dst_tex, tex_sampler, in.screen_uv);
    let source_dims = vec2<f32>(textureDimensions(src_tex));
    let prescaled = params.src_prescaled > 0.5;
    var target_pixel = in.screen_uv * params.canvas_size;
    if (prescaled) {
        // Interpolated UVs can land on opposite sides of a nearest-sample
        // boundary when the same 2x raster is cropped to a different size.
        target_pixel = in.position.xy;
    }
    var canvas_pixel = target_pixel;
    if (params.view_enabled > 0.5) {
        canvas_pixel = vec2<f32>(
            dot(params.view_inv_x.xyz, vec3<f32>(target_pixel, 1.0)),
            dot(params.view_inv_y.xyz, vec3<f32>(target_pixel, 1.0)),
        );
    }
    var pixel = canvas_pixel;
    if (params.src_in_target_space > 0.5) {
        pixel = target_pixel;
    }
    let layer_pos = (pixel - params.dest_origin) / max(params.dest_size, vec2<f32>(0.0001));
    let inside_dest = all(layer_pos >= vec2<f32>(0.0)) && all(layer_pos <= vec2<f32>(1.0));
    var src_uv = params.uv_origin + layer_pos * params.uv_size;
    var source_pixel = round(params.uv_origin * params.src_size) + pixel - params.dest_origin;
    var inside = inside_dest;
    if (params.transform_enabled > 0.5) {
        let local = vec2<f32>(
            dot(params.inv_affine_x.xyz, vec3<f32>(pixel, 1.0)),
            dot(params.inv_affine_y.xyz, vec3<f32>(pixel, 1.0)),
        );
        inside = inside && all(local >= vec2<f32>(0.0)) && all(local <= params.src_size);
        src_uv = local / max(params.src_size, vec2<f32>(0.0001));
        source_pixel = local;
    }
    if (!inside || params.dest_size.x <= 0.0 || params.dest_size.y <= 0.0) {
        return dst;
    }

    var src = textureSample(src_tex, tex_sampler, src_uv);
    if (prescaled) {
        // Raster buckets are integers. GPU reciprocal approximation must
        // not turn an exact 4x ratio into 3.999999 and move one texel back.
        let raster_scale = round(source_dims / params.src_size);
        let texel = vec2<i32>(floor(source_pixel * raster_scale));
        src = textureLoad(src_tex, clamp(texel, vec2<i32>(0), vec2<i32>(source_dims) - 1), 0);
    }
    if (params.src_replace > 0.5) {
        // A document projection already includes its background. Copy its
        // premultiplied pixels without applying that background twice.
        return src;
    }
    var alpha_factor = params.opacity;
    if (params.mask_enabled > 0.5) {
        var mask_uv = in.screen_uv;
        if (params.view_enabled > 0.5) {
            // Masks stay canvas-resolution; sample them at the canvas position.
            mask_uv = canvas_pixel / max(params.canvas_dims, vec2<f32>(0.0001));
        }
        // Canonical Alpha8 coverage is uploaded into a one-channel R8 texture.
        let mask_size = textureDimensions(mask_tex);
        let source_size = max(params.mask_source_size, vec2<f32>(mask_size));
        let source_pixel = clamp(mask_uv * source_size,
            vec2<f32>(0.5), source_size - vec2<f32>(0.5));
        let local_uv = (source_pixel - params.mask_origin) / vec2<f32>(mask_size);
        var coverage = textureSample(mask_tex, tex_sampler, local_uv).r;
        if (params.mask_enabled > 1.5) {
            // Comic edge ownership is a binary canvas-pixel partition. Linear
            // filtering would let both adjacent parts blend a seam pixel.
            let size = textureDimensions(mask_tex);
            let full_size = max(params.mask_source_size, vec2<f32>(size));
            let pixel = clamp(
                vec2<i32>(floor(mask_uv * full_size)),
                vec2<i32>(0), vec2<i32>(full_size) - 1,
            );
            let local_pixel = pixel - vec2<i32>(params.mask_origin);
            coverage = textureLoad(
                mask_tex, clamp(local_pixel, vec2<i32>(0), vec2<i32>(size) - 1), 0,
            ).r;
        }
        alpha_factor = alpha_factor * coverage;
    }
    let src_alpha = clamp(src.a * alpha_factor, 0.0, 1.0);
    let mode = i32(params.blend_mode + 0.5);
    // Layer textures are uploaded straight-alpha, but composite render
    // targets (group / clipping-run results) hold premultiplied pixels and
    // must be unpremultiplied before the straight-alpha blend math.
    var src_rgb = src.rgb;
    if (params.src_premultiplied > 0.5 && src.a > 0.001) {
        src_rgb = src.rgb / src.a;
    }
    var dst_rgb = vec3<f32>(0.0);
    if (dst.a > 0.001) {
        dst_rgb = dst.rgb / dst.a;
    }

    if (mode == 26) {
        if (hash21(in.position.xy) < src_alpha) {
            return vec4<f32>(src_rgb, 1.0);
        }
        return dst;
    }

    let blended = blend_color(src_rgb, dst_rgb, mode);
    if (params.atop_enabled > 0.5) {
        // Source-atop composite for clipping-group members: the source only
        // lands where the group already has coverage, and the destination
        // alpha is preserved (QPainter CompositionMode_SourceAtop parity).
        return vec4<f32>(
            blended * src_alpha * dst.a + dst.rgb * (1.0 - src_alpha),
            dst.a,
        );
    }
    return vec4<f32>(
        blended * src_alpha + dst.rgb * (1.0 - src_alpha),
        clamp(src_alpha + dst.a * (1.0 - src_alpha), 0.0, 1.0),
    );
}
"""

WGPU_BLEND_MODE_IDS: dict[str, int] = {
    "normal": 0,
    "pass_through": 0,
    "darken": 1,
    "multiply": 2,
    "color_burn": 3,
    "linear_burn": 4,
    "darker_color": 5,
    "lighten": 6,
    "screen": 7,
    "color_dodge": 8,
    "linear_dodge": 9,
    "lighter_color": 10,
    "overlay": 11,
    "soft_light": 12,
    "hard_light": 13,
    "vivid_light": 14,
    "linear_light": 15,
    "pin_light": 16,
    "difference": 17,
    "exclusion": 18,
    "subtract": 19,
    "divide": 20,
    "hue": 21,
    "saturation": 22,
    "color": 23,
    "luminosity": 24,
    "hard_mix": 25,
    "dissolve": 26,
}

WGPU_ENABLED_BLEND_MODES = frozenset(WGPU_BLEND_MODE_IDS)


class WgpuShaderLibrary:
    """Central WGSL registry and render-pipeline cache."""

    BLEND_MODE_IDS: ClassVar[dict[str, int]] = WGPU_BLEND_MODE_IDS
    ENABLED_BLEND_MODES: ClassVar[frozenset[str]] = WGPU_ENABLED_BLEND_MODES

    def __init__(self, bundle: Any, *, target_format: str = "rgba8unorm") -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self.target_format = target_format
        self._shader_modules: dict[str, Any] = {}
        self._pipelines: dict[str, Any] = {}
        self._blend_bind_group_layout: Any | None = None
        self._sampler: Any | None = None
        self._blend_bindings = BlendBindingCache()

    @property
    def blend_bind_group_layout(self) -> Any:
        if self._blend_bind_group_layout is None:
            self._blend_bind_group_layout = self._device.create_bind_group_layout(
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
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                    {
                        "binding": 3,
                        "visibility": self._wgpu.ShaderStage.VERTEX
                        | self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                    {
                        "binding": 4,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                ]
            )
        return self._blend_bind_group_layout

    @property
    def sampler(self) -> Any:
        if self._sampler is None:
            self._sampler = self._device.create_sampler(
                mag_filter="nearest",
                min_filter="nearest",
            )
        return self._sampler

    def get_layer_blend_pipeline(self) -> Any:
        return self._layer_blend_pipeline(normal_only=False)

    def _normal_layer_blend_pipeline(self) -> Any:
        return self._layer_blend_pipeline(normal_only=True)

    def _layer_blend_pipeline(self, *, normal_only: bool) -> Any:
        suffix = ":normal" if normal_only else ""
        key = f"layer_blend:{self.target_format}{suffix}"
        if key in self._pipelines:
            return self._pipelines[key]
        source = BLEND_COMPOSITOR_WGSL
        if normal_only:
            # Only selected after checking every descendant. Constant folding
            # avoids compiling unused blend branches on the first normal frame.
            source = source.replace("let mode = i32(params.blend_mode + 0.5);", "let mode = 0;")
        module = self._shader_module(f"blend_compositor{suffix}", source)
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self.blend_bind_group_layout]
        )
        try:
            pipeline = self._device.create_render_pipeline(
                layout=layout,
                vertex={"module": module, "entry_point": "vs_main", "buffers": []},
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
            )
        except Exception as exc:  # pragma: no cover - backend/platform dependent
            raise WgpuShaderError(f"failed to create layer blend pipeline: {exc}") from exc
        self._pipelines[key] = pipeline
        return pipeline

    def create_blend_bind_group(
        self,
        dst_texture: Any,
        src_texture: Any,
        *,
        canvas_size: tuple[float, float],
        dest_origin: tuple[float, float],
        dest_size: tuple[float, float],
        uv_origin: tuple[float, float],
        uv_size: tuple[float, float],
        src_size: tuple[float, float] = (1.0, 1.0),
        inverse_affine: tuple[float, float, float, float, float, float] | None = None,
        opacity: float,
        blend_mode: str,
        mask_texture: Any | None = None,
        atop_composite: bool = False,
        src_premultiplied: bool = False,
        view_inverse: tuple[float, float, float, float, float, float] | None = None,
        canvas_dims: tuple[float, float] | None = None,
        src_in_target_space: bool = False,
        src_prescaled: bool = False,
        src_replace: bool = False,
        mask_nearest: bool = False,
    ) -> Any:
        mask_enabled = mask_texture is not None
        inv = inverse_affine or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        transform_enabled = inverse_affine is not None
        view_inv = view_inverse or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        dims = canvas_dims or canvas_size
        params = np.array(
            [
                canvas_size[0],
                canvas_size[1],
                dest_origin[0],
                dest_origin[1],
                dest_size[0],
                dest_size[1],
                uv_origin[0],
                uv_origin[1],
                uv_size[0],
                uv_size[1],
                src_size[0],
                src_size[1],
                inv[0],
                inv[1],
                inv[4],
                0.0,
                inv[2],
                inv[3],
                inv[5],
                0.0,
                opacity,
                float(self.blend_mode_id(blend_mode)),
                0.0,
                (2.0 if mask_nearest else 1.0) if mask_enabled else 0.0,
                1.0 if transform_enabled else 0.0,
                1.0 if atop_composite else 0.0,
                0.0,
                0.0,
                view_inv[0],
                view_inv[1],
                view_inv[4],
                0.0,
                view_inv[2],
                view_inv[3],
                view_inv[5],
                0.0,
                dims[0],
                dims[1],
                1.0 if view_inverse is not None else 0.0,
                1.0 if src_in_target_space else 0.0,
                1.0 if src_premultiplied else 0.0,
                1.0 if src_prescaled else 0.0,
                1.0 if src_replace else 0.0,
                0.0,
                float(getattr(mask_texture, "offset_x", 0)),
                float(getattr(mask_texture, "offset_y", 0)),
                float(getattr(mask_texture, "source_width", 0)),
                float(getattr(mask_texture, "source_height", 0)),
            ],
            dtype=np.float32,
        )
        return self._blend_bindings.get_or_create(
            self._device,
            self._wgpu.BufferUsage.UNIFORM,
            self.blend_bind_group_layout,
            self.sampler,
            (dst_texture.view, src_texture.view, (mask_texture or src_texture).view),
            params.tobytes(),
        )

    def release_blend_bindings(self) -> None:
        self._blend_bindings.release()

    @classmethod
    def blend_mode_id(cls, blend_mode: str) -> int:
        return cls.BLEND_MODE_IDS.get(blend_mode, 0)

    @classmethod
    def is_blend_mode_enabled(cls, blend_mode: str) -> bool:
        return blend_mode in cls.ENABLED_BLEND_MODES

    def _shader_module(self, name: str, source: str) -> Any:
        if name not in self._shader_modules:
            try:
                self._shader_modules[name] = self._device.create_shader_module(code=source)
            except Exception as exc:
                raise WgpuShaderError(f"WGSL shader {name!r} failed to compile: {exc}") from exc
        return self._shader_modules[name]
