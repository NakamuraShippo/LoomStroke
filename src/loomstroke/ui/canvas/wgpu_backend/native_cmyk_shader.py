"""Five-component float compositing; the K plate never doubles as alpha."""

from typing import ClassVar

from .shader_library import BLEND_COMPOSITOR_WGSL, WgpuShaderLibrary


class NativeCmykShader:
    MODES: ClassVar[dict[str, int]] = {
        key: value
        for key, value in WgpuShaderLibrary.BLEND_MODE_IDS.items()
        if key not in {"hue", "saturation", "color", "luminosity"}
    }

    @staticmethod
    def code() -> str:
        start = BLEND_COMPOSITOR_WGSL.index("fn blend_color(")
        stop = BLEND_COMPOSITOR_WGSL.index("@fragment", start)
        blend = BLEND_COMPOSITOR_WGSL[start:stop]
        hsl_start = blend.index("    if (mode == 21)")
        hsl_stop = blend.index("    if (mode == 25)")
        blend = blend[:hsl_start] + blend[hsl_stop:]
        blend = blend.replace("vec3<f32>", "vec4<f32>")
        blend = blend.replace("vec4<f32>(0.299, 0.587, 0.114)", "vec4<f32>(0.25)")
        # Comparisons at exact integer-plane boundaries must not flip due
        # to float32 normalization/complement rounding. This tolerance is
        # below half the smallest 16-bit four-component average increment.
        blend = blend.replace("ls < ld", "ls < ld - 0.0000002")
        blend = blend.replace("ls > ld", "ls > ld + 0.0000002")
        blend = blend.replace("(s + d) >= vec4<f32>(1.0)", "(s + d) >= vec4<f32>(0.9999998)")
        return blend + """
struct Params {
    count: u32, mode: u32, opacity: f32, atop: u32, width: u32, x: u32, y: u32, pad: u32
};
@group(0) @binding(0) var<storage, read> source: array<f32>;
@group(0) @binding(1) var<storage, read> destination: array<f32>;
@group(0) @binding(2) var<storage, read_write> output: array<f32>;
@group(0) @binding(3) var<storage, read> mask: array<f32>;
@group(0) @binding(4) var<uniform> params: Params;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let i = gid.x;
    if (i >= params.count) { return; }
    let n = i * 5u;
    let s = vec4<f32>(source[n], source[n+1u], source[n+2u], source[n+3u]);
    let d = vec4<f32>(destination[n], destination[n+1u], destination[n+2u], destination[n+3u]);
    var sa = clamp(source[n+4u] * params.opacity * mask[i], 0.0, 1.0);
    let da = destination[n+4u];
    // Blend formulas operate on the complements of subtractive ink values.
    var b = vec4<f32>(1.0) - blend_color(vec4<f32>(1.0)-s, vec4<f32>(1.0)-d, i32(params.mode));
    if (params.mode == 26u) {
        let px = f32(i % params.width + params.x);
        let py = f32(i / params.width + params.y);
        var p = fract(vec2<f32>(px, py) * vec2<f32>(123.34, 456.21));
        p = p + dot(p, p + vec2<f32>(45.32));
        sa = select(0.0, 1.0, fract(p.x*p.y) < sa);
        b = s;
    }
    var a = sa + da*(1.0-sa);
    var premul = s*sa*(1.0-da) + d*da*(1.0-sa) + b*sa*da;
    if (params.atop != 0u) { a = da; premul = (b*sa+d*(1.0-sa))*da; }
    let c = select(vec4<f32>(0.0), premul/max(a, 0.0000001), a > 0.0);
    output[n] = clamp(c.x, 0.0, 1.0);
    output[n+1u] = clamp(c.y, 0.0, 1.0);
    output[n+2u] = clamp(c.z, 0.0, 1.0);
    output[n+3u] = clamp(c.w, 0.0, 1.0);
    output[n+4u] = a;
}
"""
