"""Straight-alpha layer styles; circular dilation uses horizontal range maxima.

Building powers-of-two horizontal windows makes a radius-64 disk require at
most 258 lookups per pixel instead of a quadratic neighbourhood scan.
"""

from .extended_layer_style_shader import EXTENDED_RGB_WGSL, STYLE_MATH_WGSL
from .style_backdrop_shader import RGB_BACKDROP_WGSL
from .style_blend_shader import RGB_STYLE_BLEND

LAYER_STYLE_WGSL = (
    """
struct Params {
    operation: vec4f,
    geometry: vec4f,
    color: vec4f,
    style: vec4f, secondary: vec4f, lighting: vec4f, material: vec4f, coordinates: vec4f,
    blending: vec4f,
    profile_info: vec4f, profile_points: array<vec4f,32>,
    lights_info: vec4f, light_directions: array<vec4f,8>, light_colors: array<vec4f,8>,
    sample_x: vec4f, sample_y: vec4f, document_info: vec4f,
};
@group(0) @binding(0) var source: texture_2d<f32>;
@group(0) @binding(1) var previous: texture_2d<f32>;
@group(0) @binding(2) var mask: texture_2d<f32>;
@group(0) @binding(3) var h0: texture_2d<f32>;
@group(0) @binding(4) var h1: texture_2d<f32>;
@group(0) @binding(5) var h2: texture_2d<f32>;
@group(0) @binding(6) var h3: texture_2d<f32>;
@group(0) @binding(7) var h4: texture_2d<f32>;
@group(0) @binding(8) var h5: texture_2d<f32>;
@group(0) @binding(9) var h6: texture_2d<f32>;
@group(0) @binding(10) var h7: texture_2d<f32>;
@group(0) @binding(11) var<uniform> params: Params;

@vertex fn vs_main(@builtin(vertex_index) index: u32) -> @builtin(position) vec4f {
    var positions = array<vec2f, 3>(vec2f(-1., -1.), vec2f(3., -1.), vec2f(-1., 3.));
    return vec4f(positions[index], 0., 1.);
}
fn load(t: texture_2d<f32>, p: vec2i) -> vec4f {
    if any(p < vec2i(0)) || any(p >= vec2i(textureDimensions(t))) { return vec4f(0.); }
    return textureLoad(t, p, 0);
}
fn horizontal(level: i32, p: vec2i) -> f32 {
    switch level {
        case 0: { return load(h0, p).r; }
        case 1: { return load(h1, p).r; }
        case 2: { return load(h2, p).r; }
        case 3: { return load(h3, p).r; }
        case 4: { return load(h4, p).r; }
        case 5: { return load(h5, p).r; }
        case 6: { return load(h6, p).r; }
        default: { return load(h7, p).r; }
    }
}
fn over(below: vec4f, above: vec4f) -> vec4f {
    let alpha = above.a + below.a * (1. - above.a);
    if alpha <= 0.000001 { return vec4f(0.); }
    return vec4f((above.rgb * above.a + below.rgb * below.a * (1. - above.a)) / alpha, alpha);
}
@fragment fn fs_main(@builtin(position) position: vec4f) -> @location(0) vec4f {
    let p = vec2i(position.xy);
    let op = i32(params.operation.x);
    style_prepare(p);
    let radius = i32(params.operation.y);
    var base = load(source, p);
    if params.blending.y > .5 && params.blending.z < .5 { base=style_sample(source,p); }
    if op == 13 { return vec4f(0.); }
    if op == 12 { return base; }
    if op == 0 { // transparent padding, or a zero-filled accumulator
        if params.operation.z > 0. { return vec4f(0.); }
        var pixel = load(source, p - vec2i(params.geometry.xy));
        if params.operation.w > 0. && pixel.a > 0. { pixel = vec4f(pixel.rgb / pixel.a, pixel.a); }
        return pixel;
    }
    if op == 1 { // forward horizontal window, doubled at each level
        let other = load(source, p + vec2i(radius, 0)).r;
        return vec4f(select(max(base.r, other), min(base.r, other), params.operation.z > .5));
    }
    if op == 2 { // exact discrete circular dilation
        var alpha = select(0., 1., params.operation.z > .5);
        for (var dy = -radius; dy <= radius; dy++) {
            let dx = i32(floor(sqrt(f32(radius * radius - dy * dy))));
            let width = 2 * dx + 1;
            let level = i32(floor(log2(f32(width))));
            let window = 1 << u32(level);
            let q = p + vec2i(-dx, dy);
            let right = horizontal(level, q + vec2i(width - window, 0));
            let left = horizontal(level, q);
            alpha = select(max(alpha, max(left, right)), min(alpha, min(left, right)),
                           params.operation.z > .5);
        }
        return vec4f(alpha);
    }
    if op == 3 { // separable box blur (matches the existing style rasterizer)
        var alpha = 0.;
        let axis = vec2i(params.geometry.xy);
        let shift = vec2i(params.geometry.zw);
        for (var i = -radius; i <= radius; i++) {
            alpha += load(source, p - shift + axis * i).r;
        }
        return vec4f(alpha / f32(2 * radius + 1));
    }
    if op == 6 { return vec4f(base.a); }
    if op >= 7 { return extended_rgb(p, base); }
    let old = load(previous, p);
    let mode = i32(params.operation.z);
    if mode == 0 { return over(old, base); } // source over accumulated shadows/glows
    if mode == 1 { // color overlay, preserve source alpha
        return vec4f(mix(old.rgb, style_atop(params.color.rgb,old,p), params.color.a), old.a);
    }
    if mode == 4 {
        let amount = (1.-style_sample(mask,p).r)*params.color.a;
        return vec4f(mix(old.rgb, style_atop(params.color.rgb,old,p), amount), old.a);
    }
    var alpha = style_sample(mask, p).r;
    if mode == 3 { alpha = max(0., alpha - base.a); } // outside-only outline
    return backdrop_over(old, vec4f(params.color.rgb, alpha * params.color.a),p);
}
"""
    + STYLE_MATH_WGSL.replace("STYLE_RADIAL", "params.operation.z > .5")
    + EXTENDED_RGB_WGSL
    + RGB_STYLE_BLEND
    + RGB_BACKDROP_WGSL
)
