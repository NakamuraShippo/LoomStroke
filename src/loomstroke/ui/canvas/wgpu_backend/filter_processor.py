"""WGSL image-filter passes for the wgpu compositor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np

from loomstroke.core.filters import FilterRegistry
from loomstroke.core.filters.curves import CurvesFilter
from loomstroke.core.filters.gradient_map import (
    _DEFAULT_STOPS,
    _build_gradient_lut,
    _parse_stops,
)
from loomstroke.core.filters.screentone import FILTER_TYPE as SCREENTONE_FILTER_TYPE
from loomstroke.core.filters.screentone import (
    screentone_gpu_uniforms,
    screentone_runtime_params,
)
from loomstroke.core.mask_coverage import mask_coverage_alpha8

from .compute_processor import WgpuComputeProcessor
from .filter_mask_cache import WgpuFilterMaskCache
from .filter_processor_compat import FilterProcessorLegacyAliases, filter_method
from .filter_raster_context import DEFAULT_FILTER_RASTER_CONTEXT, FilterRasterContext
from .texture_manager import qimage_to_straight_rgba
from .types import WgpuRenderTargetHandle, WgpuShaderError, WgpuTextureHandle

_FILTER_WGSL = """
struct EffectParams {
    effect_id: f32,
    p0: f32,
    p1: f32,
    p2: f32,
    p3: f32,
    p4: f32,
    p5: f32,
    p6: f32,
    p7: f32,
    p8: f32,
    p9: f32,
    p10: f32,
    p11: f32,
    p12: f32,
    p13: f32,
    p14: f32,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var<uniform> params: EffectParams;
@group(0) @binding(3) var lut_tex: texture_2d<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
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
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
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
    if (t < 0.0) { t = t + 1.0; }
    if (t > 1.0) { t = t - 1.0; }
    if (t < 1.0 / 6.0) { return p + (q - p) * 6.0 * t; }
    if (t < 0.5) { return q; }
    if (t < 2.0 / 3.0) { return p + (q - p) * (2.0 / 3.0 - t) * 6.0; }
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

fn sample_src(uv: vec2<f32>) -> vec4<f32> {
    return textureSample(src_tex, tex_sampler, clamp(uv, vec2<f32>(0.0), vec2<f32>(1.0)));
}

fn neighbor_average(uv: vec2<f32>) -> vec4<f32> {
    let dims = vec2<f32>(textureDimensions(src_tex));
    let t = 1.0 / max(dims, vec2<f32>(1.0));
    var color = sample_src(uv) * 4.0;
    color = color + sample_src(uv + vec2<f32>(t.x, 0.0));
    color = color + sample_src(uv - vec2<f32>(t.x, 0.0));
    color = color + sample_src(uv + vec2<f32>(0.0, t.y));
    color = color + sample_src(uv - vec2<f32>(0.0, t.y));
    return color / 8.0;
}

fn apply_effect(rgb: vec3<f32>, alpha: f32, uv: vec2<f32>) -> vec3<f32> {
    let effect_id = i32(params.effect_id + 0.5);
    if (effect_id == 1) {
        return mix(rgb, 1.0 - rgb, clamp(params.p0, 0.0, 1.0));
    }
    if (effect_id == 2) {
        let contrast = 1.0 + clamp(params.p1, -100.0, 100.0) / 100.0;
        let bright = clamp(params.p0, -100.0, 100.0) / 100.0;
        let shifted = (rgb - vec3<f32>(0.5019608)) * contrast;
        return clamp(
            shifted + vec3<f32>(0.5019608 + bright),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
    }
    if (effect_id == 3) {
        let in_black = clamp(params.p0, 0.0, 254.0) / 255.0;
        let in_white = max(in_black + (1.0 / 255.0), clamp(params.p1, 1.0, 255.0) / 255.0);
        let gamma = clamp(params.p2, 0.01, 9.99);
        let out_black = clamp(params.p3, 0.0, 255.0) / 255.0;
        let out_white = clamp(params.p4, 0.0, 255.0) / 255.0;
        let normalized = clamp(
            (rgb - vec3<f32>(in_black)) / (in_white - in_black),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
        let corrected = pow(normalized, vec3<f32>(1.0 / gamma));
        return clamp(
            vec3<f32>(out_black) + corrected * (out_white - out_black),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
    }
    if (effect_id == 4) {
        return vec3<f32>(
            textureSample(lut_tex, tex_sampler, vec2<f32>(rgb.r, 0.5)).r,
            textureSample(lut_tex, tex_sampler, vec2<f32>(rgb.g, 0.5)).g,
            textureSample(lut_tex, tex_sampler, vec2<f32>(rgb.b, 0.5)).b,
        );
    }
    if (effect_id == 5) {
        var hsl = rgb2hsl(rgb);
        hsl.x = fract(hsl.x + params.p0 / 360.0);
        hsl.y = clamp(hsl.y * (1.0 + params.p1 / 100.0), 0.0, 1.0);
        hsl.z = clamp(hsl.z * (1.0 + params.p2 / 100.0), 0.0, 1.0);
        return clamp(hsl2rgb(hsl), vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (effect_id == 6) {
        let lum = dot(rgb, vec3<f32>(0.299, 0.587, 0.114));
        return textureSample(lut_tex, tex_sampler, vec2<f32>(lum, 0.5)).rgb;
    }
    if (effect_id == 7) {
        let blurred = neighbor_average(uv);
        return mix(rgb, blurred.rgb, clamp(params.p0 / 8.0, 0.0, 1.0));
    }
    if (effect_id == 8) {
        let blurred = neighbor_average(uv);
        let amount = max(0.0, params.p0 / 100.0);
        return clamp(rgb + (rgb - blurred.rgb) * amount, vec3<f32>(0.0), vec3<f32>(1.0));
    }
    if (effect_id == 9) {
        let blurred = neighbor_average(uv);
        return mix(rgb, blurred.rgb, clamp(params.p0 / 100.0, 0.0, 1.0));
    }
    return rgb;
}

// CT-02 manga screentone: 32-bit wrapping hash shared with the CPU
// reference (``ScreentoneRenderer._stochastic``); coordinates are canvas
// pixels, the seed is 24 bits.
fn screentone_hash_unit(x: u32, y: u32, seed: u32) -> f32 {
    var h: u32 = (x * 0x9E3779B1u) ^ (y * 0x85EBCA77u) ^ (seed * 0xC2B2AE3Du);
    h = h ^ (h >> 15u);
    h = h * 0x2C1B3C6Du;
    h = h ^ (h >> 12u);
    h = h * 0x297A2D39u;
    h = h ^ (h >> 15u);
    return f32(h & 0xFFFFFFu) / 16777216.0;
}

// Screen coverage (0..1) for the texel at *uv*.  Mirrors
// ``ScreentoneRenderer.render_coverage`` term for term: pitch / angle /
// spot threshold in p0..p5, phase origin p6/p7, density p10, seed p11,
// layer origin p12/p13 (see ``core.effects.screentone.screentone_gpu_uniforms``).
fn screentone_coverage(position: vec2<f32>) -> f32 {
    let raster_scale = max(params.p14, 1.0);
    let px = position / raster_scale;
    let pattern = i32(params.p4 + 0.5);
    let density = clamp(params.p10, 0.0, 1.0);
    if (pattern == 5) {
        return density;
    }
    if (pattern == 4) {
        let cx = bitcast<u32>(i32(floor(px.x + params.p12)));
        let cy = bitcast<u32>(i32(floor(px.y + params.p13)));
        let unit = screentone_hash_unit(cx, cy, u32(params.p11 + 0.5));
        return select(0.0, 1.0, unit < density);
    }
    let pitch = max(params.p0, 0.000001);
    let dx = px.x + params.p12 - params.p6;
    let dy = px.y + params.p13 - params.p7;
    let u = (dx * params.p1 + dy * params.p2) / pitch;
    let v = (-dx * params.p2 + dy * params.p1) / pitch;
    let fu = fract(u) - 0.5;
    let fv = fract(v) - 0.5;
    var spot: f32 = sqrt(fu * fu + fv * fv);
    if (pattern == 1) {
        let aspect = max(params.p5, 0.001);
        spot = sqrt((fu / aspect) * (fu / aspect) + (fv * aspect) * (fv * aspect));
    } else if (pattern == 2) {
        spot = abs(fv);
    } else if (pattern == 3) {
        spot = min(abs(fu), abs(fv));
    }
    let threshold = params.p3;
    if (threshold < 0.0) {
        return 0.0;
    }
    return clamp((threshold - spot) * pitch * raster_scale + 0.5, 0.0, 1.0);
}

fn screentone_color(src: vec4<f32>, position: vec2<f32>) -> vec4<f32> {
    let coverage = screentone_coverage(position);
    let packed = u32(params.p8 + 0.5);
    let tone = vec3<f32>(
        f32((packed >> 16u) & 0xFFu) / 255.0,
        f32((packed >> 8u) & 0xFFu) / 255.0,
        f32(packed & 0xFFu) / 255.0,
    );
    let alpha = clamp(src.a * coverage * clamp(params.p9, 0.0, 1.0), 0.0, 1.0);
    return vec4<f32>(tone, alpha);
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    if (i32(params.effect_id + 0.5) == 11) {
        return screentone_color(src, in.position.xy);
    }
    let rgb = clamp(src.rgb, vec3<f32>(0.0), vec3<f32>(1.0));
    let adjusted = apply_effect(rgb, src.a, in.uv);
    return vec4<f32>(adjusted, src.a);
}
"""

_MASK_MIX_WGSL = """
@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var effect_tex: texture_2d<f32>;
@group(0) @binding(3) var mask_tex: texture_2d<f32>;
@group(0) @binding(4) var<uniform> mask_uv: vec4<f32>;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
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
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    let effected = textureSample(effect_tex, tex_sampler, in.uv);
    // Masks are canonicalized to Alpha8 coverage before upload
    // (mask_coverage_alpha8), so the alpha channel is the mask value.
    let mask = textureSample(mask_tex, tex_sampler, mask_uv.xy + in.uv * mask_uv.zw);
    let amount = clamp(mask.a, 0.0, 1.0);
    return mix(src, effected, amount);
}
"""

_UNSHARP_WGSL = """
struct UnsharpParams {
    amount: f32,
    threshold: f32,
    _pad0: f32,
    _pad1: f32,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var blur_tex: texture_2d<f32>;
@group(0) @binding(3) var<uniform> params: UnsharpParams;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
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
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    let blurred = textureSample(blur_tex, tex_sampler, in.uv);
    var diff = src.rgb - blurred.rgb;
    if (params.threshold > 0.0) {
        let magnitude = max(abs(diff.r), max(abs(diff.g), abs(diff.b)));
        if (magnitude < params.threshold) {
            diff = vec3<f32>(0.0);
        }
    }
    return vec4<f32>(clamp(src.rgb + diff * params.amount, vec3<f32>(0.0), vec3<f32>(1.0)), src.a);
}
"""

_PLUGIN_WGSL_TEMPLATE = """
struct PluginParams {
    p0: f32,
    p1: f32,
    p2: f32,
    p3: f32,
    p4: f32,
    p5: f32,
    p6: f32,
    p7: f32,
};

@group(0) @binding(0) var tex_sampler: sampler;
@group(0) @binding(1) var src_tex: texture_2d<f32>;
@group(0) @binding(2) var<uniform> params: PluginParams;

struct VertexOut {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
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
    var out: VertexOut;
    out.position = vec4<f32>(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
    out.uv = p;
    return out;
}

$PLUGIN_SOURCE

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let src = textureSample(src_tex, tex_sampler, in.uv);
    return apply_plugin(src, in.uv, params);
}
"""


@dataclass(frozen=True, slots=True)
class WgpuFilterResult:
    texture: Any
    owned_targets: list[WgpuRenderTargetHandle]


@dataclass(slots=True)
class _CachedFilter:
    texture: WgpuRenderTargetHandle
    cache_key: tuple[Any, ...]
    stale: bool = False


class WgpuFilterProcessor(FilterProcessorLegacyAliases):
    """Apply built-in filter layers without leaving the wgpu backend."""

    FILTER_IDS: ClassVar[dict[str, int]] = {
        "invert": 1,
        "brightness_contrast": 2,
        "levels": 3,
        "curves": 4,
        "hue_saturation": 5,
        "gradient_map": 6,
        "gaussian_blur": 7,
        "sharpen": 8,
        "noise_reduction": 9,
        # Compute-pass filters: never reach the fragment switch, but listed
        # here so supports()/can_apply() route them onto the GPU path.
        "mosaic": 10,
        # CT-02: fragment pass that rewrites alpha too (see ``fs_main``).
        "manga_screentone": 11,
    }

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self._pipeline: Any | None = None
        self._mask_mix_pipeline: Any | None = None
        self._plugin_pipelines: dict[tuple[str, str], Any] = {}
        self._layout: Any | None = None
        self._mask_mix_layout: Any | None = None
        self._unsharp_layout: Any | None = None
        self._unsharp_pipeline: Any | None = None
        self._plugin_layout: Any | None = None
        self._sampler: Any | None = None
        self._dummy_lut: WgpuTextureHandle | None = None
        self._lut_cache: dict[tuple[Any, ...], WgpuTextureHandle] = {}
        self._filter_cache: dict[str, _CachedFilter] = {}
        self._mask_resources = WgpuFilterMaskCache(bundle)
        self._mask_cache = self._mask_resources.entries
        self._cache_hits = 0
        self._cache_misses = 0
        self.compute_processor = WgpuComputeProcessor(bundle)

    @property
    def cache_hits(self) -> int:
        return self._cache_hits

    @property
    def cache_misses(self) -> int:
        return self._cache_misses

    @property
    def filter_cache_size(self) -> int:
        return len(self._filter_cache)

    # Keep mutable compatibility names synchronized with canonical storage.
    EFFECT_IDS: ClassVar[dict[str, int]] = FILTER_IDS

    @property
    def _effect_cache(self) -> dict[str, _CachedFilter]:
        return self._filter_cache

    @_effect_cache.setter
    def _effect_cache(self, value: dict[str, _CachedFilter]) -> None:
        self._filter_cache = value

    @property
    def effect_cache_size(self) -> int:
        return self.filter_cache_size

    def apply_layer_filter(
        self,
        encoder: Any,
        source: Any,
        layer: Any,
        *,
        create_target: Any,
        source_version: int | None = None,
        use_cache: bool = True,
        document_dpi: float | None = None,
        layer_origin: tuple[int, int] = (0, 0),
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        """Use the filter API while honoring legacy plugin method overrides."""
        apply = filter_method(
            self,
            "apply_layer_effect",
            "_apply_layer_filter",
            FilterProcessorLegacyAliases.apply_layer_effect,
        )
        return apply(
            encoder,
            source,
            layer,
            create_target=create_target,
            source_version=source_version,
            use_cache=use_cache,
            document_dpi=document_dpi,
            layer_origin=layer_origin,
            raster_context=raster_context,
        )

    def warnings_for_layer(self, layer: Any) -> tuple[str, ...]:
        filter_type = getattr(layer, "filter_type", getattr(layer, "effect_type", None))
        if not filter_type:
            return ()
        if self.supports(filter_type) or self._plugin_wgsl_source(str(filter_type)) is not None:
            return ()
        if FilterRegistry.get(str(filter_type)) is not None:
            return (
                f"layer {getattr(layer, 'id', '<unknown>')} has no WGSL filter "
                f"implementation for '{filter_type}'",
            )
        return (
            f"layer {getattr(layer, 'id', '<unknown>')} has unknown wgpu filter "
            f"'{filter_type}'",
        )

    @classmethod
    def supports(cls, filter_type: Any = None, *, effect_type: Any = None) -> bool:
        selected = filter_type if filter_type is not None else effect_type
        return str(selected or "") in cls.FILTER_IDS

    @classmethod
    def can_apply(cls, filter_type: Any = None, *, effect_type: Any = None) -> bool:
        """Return whether *filter_type* has a wgpu implementation.

        Filter layers whose type this rejects still need the CPU path, so the
        compositor keeps routing their subtree through the bitmap flatten.
        """

        selected = filter_type if filter_type is not None else effect_type
        name = str(selected or "")
        if not name:
            return False
        return cls.supports(name) or cls._plugin_wgsl_source(name) is not None

    @classmethod
    def chain_version(
        cls,
        previous_version: int,
        layer: Any,
        *,
        document_dpi: float | None = None,
    ) -> int:
        """Fold one filter layer into a running source-version for a chain.

        Intermediate filter outputs are plain render targets with no
        ``texture_version``, so a chained filter cannot derive cache identity
        from its input texture.  Folding each applied filter into a running
        version keeps later links in the chain invalidating when an earlier
        one changes.  ``document_dpi`` joins the hash for filters whose output
        depends on it (screentone), so a DPI change invalidates the chain.
        """

        filter_type = str(getattr(layer, "filter_type", getattr(layer, "effect_type", "")) or "")
        params = getattr(layer, "filter_params", getattr(layer, "effect_params", {})) or {}
        if filter_type == SCREENTONE_FILTER_TYPE:
            params = screentone_runtime_params(params, document_dpi=document_dpi)
        return (
            hash(
                (
                    int(previous_version),
                    str(getattr(layer, "id", "<unknown>")),
                    filter_type,
                    cls._freeze_params(params),
                    float(getattr(layer, "opacity", 1.0)),
                    cls._filter_mask_version(layer),
                )
            )
            & 0x7FFFFFFF
        )

    def _apply_layer_filter(
        self,
        encoder: Any,
        source: Any,
        layer: Any,
        *,
        create_target: Any,
        source_version: int | None = None,
        use_cache: bool = True,
        document_dpi: float | None = None,
        layer_origin: tuple[int, int] = (0, 0),
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        """Apply *layer*'s filter to *source*.

        ``source_version`` overrides the input identity used for caching, for
        inputs that carry no ``texture_version`` of their own (see
        :meth:`chain_version`).  ``use_cache=False`` skips the cache entirely
        and hands the final texture back in ``owned_targets`` so the caller
        retires it; sibling filter layers use this because their input is the
        running composite, whose identity is not knowable here.
        ``document_dpi`` / ``layer_origin`` are the apply-time context of
        filters that depend on the document scale and the carrier's canvas
        position (CT-02 screentone); they join the params (and so the cache
        key) instead of being persisted.
        """

        filter_type = str(getattr(layer, "filter_type", getattr(layer, "effect_type", "")) or "")
        plugin_source = (
            None if self.supports(filter_type) else self._plugin_wgsl_source(filter_type)
        )
        if not self.supports(filter_type) and plugin_source is None:
            return WgpuFilterResult(texture=source, owned_targets=[])
        cache_id = str(getattr(layer, "id", "<unknown>"))
        params_dict = getattr(layer, "filter_params", getattr(layer, "effect_params", {})) or {}
        if filter_type == SCREENTONE_FILTER_TYPE:
            params_dict = screentone_runtime_params(
                params_dict, document_dpi=document_dpi, layer_origin=layer_origin
            )
        cache_key = (
            *self._cache_key(
                filter_type, params_dict, source, layer, source_version=source_version
            ),
            raster_context,
        )
        cached = self._filter_cache.get(cache_id) if use_cache else None
        if (
            cached is not None
            and not cached.stale
            and cached.cache_key == cache_key
            and cached.texture.width == source.width
            and cached.texture.height == source.height
        ):
            self._cache_hits += 1
            return WgpuFilterResult(texture=cached.texture, owned_targets=[])

        self._cache_misses += 1
        dispatch = filter_method(
            self,
            "_dispatch_effect",
            "_dispatch_filter",
            FilterProcessorLegacyAliases._dispatch_effect,
        )
        filter_result = dispatch(
            encoder,
            source,
            filter_type,
            plugin_source,
            params_dict,
            create_target=create_target,
            raster_context=raster_context,
        )

        final_result = self._apply_filter_mask_if_needed(
            encoder,
            source,
            filter_result,
            layer,
            create_target=create_target,
            raster_context=raster_context,
        )
        final_result = self._apply_filter_opacity_if_needed(
            encoder,
            source,
            final_result,
            layer,
            create_target=create_target,
        )
        if final_result.texture is source:
            # A no-op filter (blur radius 0) hands back the input, which the
            # cache must never take ownership of — it would destroy a live
            # layer texture on the next eviction.
            return WgpuFilterResult(texture=source, owned_targets=[])
        if not use_cache:
            # Nothing owns the final texture, so hand it to the caller to retire.
            owned = list(final_result.owned_targets)
            if all(target is not final_result.texture for target in owned):
                owned.append(final_result.texture)
            return WgpuFilterResult(texture=final_result.texture, owned_targets=owned)
        old = self._filter_cache.pop(cache_id, None)
        if old is not None:
            self._destroy_target(old.texture)
        self._filter_cache[cache_id] = _CachedFilter(
            texture=final_result.texture,
            cache_key=cache_key,
        )
        return final_result

    def _dispatch_filter(
        self,
        encoder: Any,
        source: Any,
        filter_type: str,
        plugin_source: str | None,
        params_dict: dict[str, Any],
        *,
        create_target: Any,
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        """Route one filter to its compute / unsharp / plugin / fragment pass."""
        if filter_type == "gaussian_blur":
            return self.compute_processor.gaussian_blur(
                encoder,
                source,
                radius=float(params_dict.get("radius", 5.0)),
                raster_scale=raster_context.scale,
            )
        if filter_type == "sharpen":
            return self._apply_sharpen(
                encoder,
                source,
                params_dict,
                create_target=create_target,
                raster_scale=raster_context.scale,
            )
        if filter_type == "noise_reduction":
            return self.compute_processor.bilateral(
                encoder,
                source,
                strength=float(params_dict.get("strength", 10.0)),
                color_strength=float(params_dict.get("color_strength", 10.0)),
                raster_scale=raster_context.scale,
            )
        if filter_type == "mosaic":
            return self.compute_processor.mosaic(
                encoder,
                source,
                cell_size=float(params_dict.get("cell_size", 8.0)),
                raster_scale=raster_context.scale,
                texel_origin=raster_context.texel_origin,
            )
        if plugin_source is not None:
            return self._apply_plugin_wgsl_filter(
                encoder,
                source,
                filter_type,
                plugin_source,
                params_dict,
                create_target=create_target,
            )
        return self._apply_fragment_filter(
            encoder,
            source,
            filter_type,
            params_dict,
            create_target=create_target,
            raster_context=raster_context,
        )

    def _apply_fragment_filter(
        self,
        encoder: Any,
        source: Any,
        filter_type: str,
        params_dict: dict[str, Any],
        *,
        create_target: Any,
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        target = create_target(source.width, source.height)
        params = self._params_for_filter(filter_type, params_dict)
        if filter_type == SCREENTONE_FILTER_TYPE:
            # Page/local/custom anchors remain tied to the carrier. Only the
            # sampled texel's origin advances by the tight raster's offset.
            params[13] += raster_context.origin[0]
            params[14] += raster_context.origin[1]
            params[15] = raster_context.scale
        lut = self._lut_for_filter(filter_type, params_dict)
        bind_group = self._device.create_bind_group(
            layout=self._bind_group_layout,
            entries=[
                {"binding": 0, "resource": self._filter_sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": self._uniform_buffer(params)},
                {"binding": 3, "resource": lut.view},
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
        render_pass.set_pipeline(self._filter_pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        return WgpuFilterResult(texture=target, owned_targets=[target])

    def _apply_sharpen(
        self,
        encoder: Any,
        source: Any,
        params_dict: dict[str, Any],
        *,
        create_target: Any,
        raster_scale: int = 1,
    ) -> WgpuFilterResult:
        """Unsharp mask matching ``SharpenFilter``.

        The old pass was a fixed 3x3 neighbour average that ignored Radius and
        Threshold entirely, so the canvas showed a token sharpen while merge
        and export applied the real one.
        """

        amount = float(params_dict.get("amount", 50.0)) / 100.0
        radius = float(params_dict.get("radius", 1.0))
        threshold = float(params_dict.get("threshold", 0))
        if amount <= 0.0 or radius <= 0.0:
            return WgpuFilterResult(texture=source, owned_targets=[])

        # ``SharpenFilter`` blurs with ``ksize = max(int(radius) * 2 + 1, 3)``.
        blur = self.compute_processor.gaussian_blur(
            encoder, source, radius=max(1.0, radius), raster_scale=raster_scale
        )
        target = create_target(source.width, source.height)
        values = np.array([amount, threshold / 255.0, 0.0, 0.0], dtype=np.float32)
        uniform = self._device.create_buffer_with_data(
            data=values.tobytes(),
            usage=self._wgpu.BufferUsage.UNIFORM,
        )
        bind_group = self._device.create_bind_group(
            layout=self._unsharp_bind_group_layout,
            entries=[
                {"binding": 0, "resource": self._filter_sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": blur.texture.view},
                {"binding": 3, "resource": {"buffer": uniform, "offset": 0, "size": values.nbytes}},
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
        render_pass.set_pipeline(self._unsharp_render_pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        owned = [t for t in blur.owned_targets if t is not source]
        owned.append(target)
        return WgpuFilterResult(texture=target, owned_targets=owned)

    @property
    def _unsharp_bind_group_layout(self) -> Any:
        if self._unsharp_layout is None:
            self._unsharp_layout = self._device.create_bind_group_layout(
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
        return self._unsharp_layout

    @property
    def _unsharp_render_pipeline(self) -> Any:
        if self._unsharp_pipeline is not None:
            return self._unsharp_pipeline
        module = self._device.create_shader_module(code=_UNSHARP_WGSL)
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._unsharp_bind_group_layout]
        )
        try:
            self._unsharp_pipeline = self._device.create_render_pipeline(
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
            raise WgpuShaderError(f"failed to create sharpen pipeline: {exc}") from exc
        return self._unsharp_pipeline

    def _apply_filter_mask_if_needed(
        self,
        encoder: Any,
        source: Any,
        filter_result: WgpuFilterResult,
        layer: Any,
        *,
        create_target: Any,
        raster_context: FilterRasterContext = DEFAULT_FILTER_RASTER_CONTEXT,
    ) -> WgpuFilterResult:
        mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        if mask is None or mask.isNull() or not bool(getattr(layer, "mask_enabled", True)):
            # The final texture is owned by the cache, but any intermediate the
            # filter allocated on the way (a separable blur's first pass) still
            # has to be retired by the caller.
            return WgpuFilterResult(
                texture=filter_result.texture,
                owned_targets=[
                    target
                    for target in filter_result.owned_targets
                    if target is not filter_result.texture
                ],
            )
        mask_texture = self._filter_mask_texture(layer, mask)
        target = create_target(source.width, source.height)
        bind_group = self._device.create_bind_group(
            layout=self._mask_mix_bind_group_layout,
            entries=[
                {"binding": 0, "resource": self._filter_sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": filter_result.texture.view},
                {"binding": 3, "resource": mask_texture.view},
                {
                    "binding": 4,
                    "resource": self._uniform_buffer(
                        np.asarray(raster_context.mask_uv(source), dtype=np.float32)
                    ),
                },
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
        render_pass.set_pipeline(self._mask_mix_render_pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        return WgpuFilterResult(texture=target, owned_targets=list(filter_result.owned_targets))

    def _apply_filter_opacity_if_needed(
        self,
        encoder: Any,
        source: Any,
        filter_result: WgpuFilterResult,
        layer: Any,
        *,
        create_target: Any,
    ) -> WgpuFilterResult:
        """Fade the filter back towards the source, matching the CPU path.

        ``CanvasFilterLayerService._blend_filter_result`` draws the filtered
        image over the untouched source at the filter layer's opacity, so a
        half-opacity filter layer is a half-strength filter (not a
        half-transparent one).  A 1x1 constant-alpha mask reuses the existing
        mask-mix pass to do the same mix on the GPU.
        """

        opacity = float(getattr(layer, "opacity", 1.0))
        if opacity >= 1.0 or filter_result.texture is source:
            return filter_result
        mask_texture = self._constant_mask_texture(max(0.0, opacity))
        target = create_target(source.width, source.height)
        bind_group = self._device.create_bind_group(
            layout=self._mask_mix_bind_group_layout,
            entries=[
                {"binding": 0, "resource": self._filter_sampler},
                {"binding": 1, "resource": source.view},
                {"binding": 2, "resource": filter_result.texture.view},
                {"binding": 3, "resource": mask_texture.view},
                {
                    "binding": 4,
                    "resource": self._uniform_buffer(np.array([0, 0, 1, 1], dtype=np.float32)),
                },
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
        render_pass.set_pipeline(self._mask_mix_render_pipeline)
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        owned = list(filter_result.owned_targets)
        if all(existing is not filter_result.texture for existing in owned):
            owned.append(filter_result.texture)
        return WgpuFilterResult(texture=target, owned_targets=owned)

    def _constant_mask_texture(self, amount: float) -> WgpuTextureHandle:
        """Return a cached 1x1 mask whose alpha is *amount*."""

        quantized = max(0, min(255, round(amount * 255.0)))
        key = ("__constant__", quantized)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached
        rgba = np.zeros((1, 1, 4), dtype=np.uint8)
        rgba[0, 0, 3] = quantized
        return self._mask_resources.upload(
            key,
            np.ascontiguousarray(rgba),
            layer_id=f"__wgpu_effect_opacity__:{quantized}",
            version=quantized,
        )

    @property
    def _filter_sampler(self) -> Any:
        if self._sampler is None:
            self._sampler = self._device.create_sampler(mag_filter="nearest", min_filter="nearest")
        return self._sampler

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
                    {
                        "binding": 3,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "texture": {
                            "sample_type": self._wgpu.TextureSampleType.float,
                            "view_dimension": "2d",
                        },
                    },
                ]
            )
        return self._layout

    @property
    def _mask_mix_bind_group_layout(self) -> Any:
        if self._mask_mix_layout is None:
            self._mask_mix_layout = self._device.create_bind_group_layout(
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
                        for binding in (1, 2, 3)
                    ),
                    {
                        "binding": 4,
                        "visibility": self._wgpu.ShaderStage.FRAGMENT,
                        "buffer": {"type": self._wgpu.BufferBindingType.uniform},
                    },
                ]
            )
        return self._mask_mix_layout

    @property
    def _plugin_bind_group_layout(self) -> Any:
        if self._plugin_layout is None:
            self._plugin_layout = self._device.create_bind_group_layout(
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
        return self._plugin_layout

    @_filter_sampler.setter
    def _filter_sampler(self, value: Any) -> None:
        self._sampler = value

    @property
    def _filter_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        module = self._device.create_shader_module(code=_FILTER_WGSL)
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
            raise WgpuShaderError(f"failed to create filter pipeline: {exc}") from exc
        return self._pipeline

    @property
    def _mask_mix_render_pipeline(self) -> Any:
        if self._mask_mix_pipeline is not None:
            return self._mask_mix_pipeline
        module = self._device.create_shader_module(code=_MASK_MIX_WGSL)
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._mask_mix_bind_group_layout]
        )
        try:
            self._mask_mix_pipeline = self._device.create_render_pipeline(
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
            raise WgpuShaderError(f"failed to create filter mask pipeline: {exc}") from exc
        return self._mask_mix_pipeline

    @_filter_pipeline.setter
    def _filter_pipeline(self, value: Any) -> None:
        self._pipeline = value

    def _apply_plugin_wgsl_filter(
        self,
        encoder: Any,
        source: Any,
        filter_type: str,
        plugin_source: str,
        params_dict: dict[str, Any],
        *,
        create_target: Any,
    ) -> WgpuFilterResult:
        target = create_target(source.width, source.height)
        uniform_values = self._plugin_uniform_values(filter_type, params_dict)
        uniform = self._device.create_buffer_with_data(
            data=uniform_values.tobytes(),
            usage=self._wgpu.BufferUsage.UNIFORM,
        )
        bind_group = self._device.create_bind_group(
            layout=self._plugin_bind_group_layout,
            entries=[
                {"binding": 0, "resource": self._filter_sampler},
                {"binding": 1, "resource": source.view},
                {
                    "binding": 2,
                    "resource": {"buffer": uniform, "offset": 0, "size": uniform_values.nbytes},
                },
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
        render_pass.set_pipeline(self._plugin_render_pipeline(filter_type, plugin_source))
        render_pass.set_bind_group(0, bind_group, [], 0, 999999)
        render_pass.draw(6, 1, 0, 0)
        render_pass.end()
        return WgpuFilterResult(texture=target, owned_targets=[target])

    def _plugin_render_pipeline(self, filter_type: str, plugin_source: str) -> Any:
        key = (filter_type, plugin_source)
        cached = self._plugin_pipelines.get(key)
        if cached is not None:
            return cached
        module = self._device.create_shader_module(
            code=_PLUGIN_WGSL_TEMPLATE.replace("$PLUGIN_SOURCE", plugin_source)
        )
        layout = self._device.create_pipeline_layout(
            bind_group_layouts=[self._plugin_bind_group_layout]
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
            raise WgpuShaderError(
                f"failed to create plugin WGSL pipeline for {filter_type!r}: {exc}"
            ) from exc
        self._plugin_pipelines[key] = pipeline
        return pipeline

    def _uniform_buffer(self, params: np.ndarray) -> dict[str, Any]:
        uniform = self._device.create_buffer_with_data(
            data=params.astype(np.float32).tobytes(),
            usage=self._wgpu.BufferUsage.UNIFORM,
        )
        return {"buffer": uniform, "offset": 0, "size": params.nbytes}

    def _params_for_filter(self, filter_type: str, params: dict[str, Any]) -> np.ndarray:
        values = np.zeros(16, dtype=np.float32)
        values[0] = float(self.FILTER_IDS[filter_type])
        if filter_type == SCREENTONE_FILTER_TYPE:
            values[1:16] = np.asarray(screentone_gpu_uniforms(params), dtype=np.float32)
        elif filter_type == "invert":
            values[1] = float(params.get("strength", 100.0)) / 100.0
        elif filter_type == "brightness_contrast":
            values[1] = float(params.get("brightness", 0.0))
            values[2] = float(params.get("contrast", 0.0))
        elif filter_type == "levels":
            values[1] = float(params.get("in_black", 0))
            values[2] = float(params.get("in_white", 255))
            values[3] = float(params.get("gamma", 1.0))
            values[4] = float(params.get("out_black", 0))
            values[5] = float(params.get("out_white", 255))
        elif filter_type == "hue_saturation":
            values[1] = float(params.get("hue", 0.0))
            values[2] = float(params.get("saturation", 0.0))
            values[3] = float(params.get("lightness", 0.0))
        elif filter_type == "gaussian_blur":
            values[1] = float(params.get("radius", 5.0))
        elif filter_type == "sharpen":
            values[1] = float(params.get("amount", 50.0))
        elif filter_type == "noise_reduction":
            values[1] = float(params.get("strength", 50.0))
        return values

    def _lut_for_filter(self, filter_type: str, params: dict[str, Any]) -> WgpuTextureHandle:
        if filter_type == "curves":
            lut = CurvesFilter.build_lut_from_params(params)
            rgba = np.stack([lut, lut, lut, np.full(256, 255, dtype=np.uint8)], axis=1)
            return self._upload_lut(("curves", tuple(lut.tolist())), rgba)
        if filter_type == "gradient_map":
            stops = _parse_stops(params.get("gradient_stops", _DEFAULT_STOPS))
            rgba = _build_gradient_lut(stops)
            key = ("gradient_map", tuple(map(tuple, rgba.tolist())))
            return self._upload_lut(key, rgba)
        return self._dummy_lut_texture

    @property
    def _dummy_lut_texture(self) -> WgpuTextureHandle:
        if self._dummy_lut is None:
            rgba = np.zeros((256, 4), dtype=np.uint8)
            rgba[:, 0] = np.arange(256, dtype=np.uint8)
            rgba[:, 1] = rgba[:, 0]
            rgba[:, 2] = rgba[:, 0]
            rgba[:, 3] = 255
            self._dummy_lut = self._upload_lut(("identity",), rgba)
        return self._dummy_lut

    def _upload_lut(self, key: tuple[Any, ...], rgba: np.ndarray) -> WgpuTextureHandle:
        cached = self._lut_cache.get(key)
        if cached is not None:
            return cached
        data = np.ascontiguousarray(rgba.reshape(1, 256, 4))
        texture = self._device.create_texture(
            size=(256, 1, 1),
            usage=self._wgpu.TextureUsage.TEXTURE_BINDING | self._wgpu.TextureUsage.COPY_DST,
            format=self._wgpu.TextureFormat.rgba8unorm,
        )
        handle = WgpuTextureHandle(
            texture=texture,
            view=texture.create_view(),
            width=256,
            height=1,
            layer_id=f"__wgpu_effect_lut__:{key[0]}",
            texture_version=hash(key) & 0x7FFFFFFF,
        )
        self._queue.write_texture(
            {"texture": texture, "origin": (0, 0, 0)},
            data,
            {"bytes_per_row": 256 * 4, "rows_per_image": 1},
            (256, 1, 1),
        )
        self._lut_cache[key] = handle
        return handle

    def _filter_mask_texture(self, layer: Any, mask: Any) -> WgpuTextureHandle:
        version = self._filter_mask_version(layer)
        key = (
            str(getattr(layer, "id", "<unknown>")),
            version,
            int(mask.width()),
            int(mask.height()),
        )
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached
        # Canonicalize the grayscale mask into Alpha8 coverage (luminance x
        # alpha) so the mask-mix shader can read the alpha channel alone.
        # Persisted filter masks arrive luminance-encoded with opaque alpha,
        # which the previous raw upload rendered as full coverage everywhere.
        rgba = np.ascontiguousarray(qimage_to_straight_rgba(mask_coverage_alpha8(mask)))
        return self._mask_resources.upload(
            key,
            rgba,
            layer_id=f"__wgpu_effect_mask__:{key[0]}",
            version=version,
        )

    def _cache_key(
        self,
        filter_type: str,
        params: dict[str, Any],
        source: Any,
        layer: Any,
        *,
        source_version: int | None = None,
    ) -> tuple[Any, ...]:
        mask_version = self._filter_mask_version(layer)
        return (
            filter_type,
            self._freeze_params(params),
            (
                int(source_version)
                if source_version is not None
                else int(getattr(source, "texture_version", -1))
            ),
            int(source.width),
            int(source.height),
            mask_version,
            float(getattr(layer, "opacity", 1.0)),
        )

    @staticmethod
    def _filter_mask_version(layer: Any) -> int:
        mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        if mask is None or mask.isNull() or not bool(getattr(layer, "mask_enabled", True)):
            return 0
        version = int(
            getattr(
                layer,
                "filter_mask_texture_version",
                getattr(layer, "effect_mask_texture_version", 0),
            )
            or getattr(layer, "filter_mask_version", getattr(layer, "effect_mask_version", 0))
        )
        # The high half of QImage.cacheKey identifies the image. Two fresh
        # masks with equal edit counts collide if only the low 31 bits remain.
        return version if version > 0 else int(mask.cacheKey() & 0x7FFFFFFFFFFFFFFF)

    @staticmethod
    def _plugin_wgsl_source(filter_type: str) -> str | None:
        filter_cls = FilterRegistry.get(filter_type)
        if filter_cls is None:
            return None
        instance = filter_cls()
        shader_source = getattr(instance, "wgsl_shader_source", None)
        if not callable(shader_source):
            return None
        source = shader_source()
        return source if isinstance(source, str) and source.strip() else None

    @staticmethod
    def _plugin_uniform_values(filter_type: str, params: dict[str, Any]) -> np.ndarray:
        filter_cls = FilterRegistry.get(filter_type)
        uniforms: dict[str, Any] = {}
        if filter_cls is not None:
            instance = filter_cls()
            getter = getattr(instance, "wgpu_uniforms", None)
            if callable(getter):
                raw = getter(params)
                if isinstance(raw, dict):
                    uniforms = raw
        values = np.zeros(8, dtype=np.float32)
        for index in range(8):
            raw_value = uniforms.get(f"p{index}", 0.0)
            try:
                values[index] = float(raw_value)
            except (TypeError, ValueError):
                values[index] = 0.0
        return values

    def mark_cache_stale(self, layer_id: str) -> None:
        cached = self._filter_cache.get(layer_id)
        if cached is not None:
            cached.stale = True

    def finish_frame(self, active_layer_ids: set[str], *, submitted: bool) -> None:
        """Retire resources after submit, or after abandoning a failed encoder.

        IDs must include all scene layers, including hidden and child filters.
        A failed submission invalidates filter outputs whose queued drawing may
        not have executed. Uploaded mask textures remain valid and reusable.
        """
        for layer_id in tuple(self._filter_cache):
            if not submitted or layer_id not in active_layer_ids:
                self._destroy_target(self._filter_cache.pop(layer_id).texture)
        self._mask_resources.finish_frame(active_layer_ids)

    def invalidate_cache(self, layer_id: str | None = None) -> None:
        self._mask_resources.invalidate(layer_id)
        if layer_id is None:
            for cached in self._filter_cache.values():
                self._destroy_target(cached.texture)
            self._filter_cache.clear()
            return
        cached = self._filter_cache.pop(layer_id, None)
        if cached is not None:
            self._destroy_target(cached.texture)

    def evict_all_caches(self) -> None:
        self.invalidate_cache(None)

    def release(self) -> None:
        self.invalidate_cache(None)
        self._mask_resources.release()
        for handle in self._lut_cache.values():
            self._destroy_texture_handle(handle)
        self._lut_cache.clear()
        self._dummy_lut = None

    @staticmethod
    def _freeze_params(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(
                (str(key), WgpuFilterProcessor._freeze_params(val))
                for key, val in sorted(value.items())
            )
        if isinstance(value, list | tuple):
            return tuple(WgpuFilterProcessor._freeze_params(item) for item in value)
        return value

    @staticmethod
    def _destroy_target(target: WgpuRenderTargetHandle) -> None:
        destroy = getattr(target.texture, "destroy", None)
        if callable(destroy):
            destroy()

    @staticmethod
    def _destroy_texture_handle(handle: WgpuTextureHandle) -> None:
        destroy = getattr(handle.texture, "destroy", None)
        if callable(destroy):
            destroy()


# Established imports refer to the same implementation and shader constants.
WgpuEffectProcessor = WgpuFilterProcessor
WgpuEffectResult = WgpuFilterResult
_CachedEffect = _CachedFilter
EffectRegistry = FilterRegistry
CurvesEffect = CurvesFilter
SCREENTONE_EFFECT_TYPE = SCREENTONE_FILTER_TYPE
_EFFECT_WGSL = _FILTER_WGSL

EffectRasterContext = FilterRasterContext
DEFAULT_EFFECT_RASTER_CONTEXT = DEFAULT_FILTER_RASTER_CONTEXT
WgpuEffectMaskCache = WgpuFilterMaskCache
