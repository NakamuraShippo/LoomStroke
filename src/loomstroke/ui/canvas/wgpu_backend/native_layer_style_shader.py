"""Float32 CMYK layer styles: four independent inks plus independent alpha."""

from .extended_layer_style_shader import EXTENDED_NATIVE_WGSL, STYLE_MATH_WGSL
from .style_backdrop_shader import NATIVE_BACKDROP_WGSL
from .style_blend_shader import NATIVE_STYLE_BLEND

NATIVE_LAYER_STYLE_WGSL = (
    """
struct Params {
    size: vec4f, geometry: vec4f, color: vec4f, options: vec4f, crop: vec4f,
    mapping: vec4f, placement: vec4f, output_rect: vec4f,
    style: vec4f, secondary: vec4f, lighting: vec4f, material: vec4f, coordinates: vec4f,
    blending: vec4f,
    profile_info: vec4f, profile_points: array<vec4f,32>,
    lights_info: vec4f, light_directions: array<vec4f,8>, light_colors: array<vec4f,8>,
    sample_x: vec4f, sample_y: vec4f, document_info: vec4f,
};
@group(0) @binding(0) var<storage, read> source: array<f32>;
@group(0) @binding(1) var<storage, read> previous: array<f32>;
@group(0) @binding(2) var<storage, read> mask: array<f32>;
@group(0) @binding(3) var<storage, read_write> output: array<f32>;
@group(0) @binding(4) var<storage, read_write> ranges: array<f32>;
@group(0) @binding(5) var<uniform> params: Params;
fn index(p: vec2i) -> i32 { return p.y * i32(params.size.x) + p.x; }
fn valid(p: vec2i) -> bool { return all(p >= vec2i(0)) && all(p < vec2i(params.size.xy)); }
fn alpha(p: vec2i) -> f32 {
    if !valid(p) { return 0.; }
    return source[5 * index(p) + 4];
}
fn maximum(level: i32, p: vec2i) -> f32 {
    if !valid(p) { return 0.; }
    return ranges[level * i32(params.size.x * params.size.y) + index(p)];
}
fn ink(p: vec2i) -> vec4f {
    if !valid(p) { return vec4f(0.); }
    let n = 5 * index(p);
    return vec4f(source[n], source[n+1], source[n+2], source[n+3]);
}
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) invocation: vec3u) {
    let width = i32(select(params.size.x,params.document_info.x,params.blending.y>.5));
    let height = i32(select(params.size.y,params.document_info.y,params.blending.y>.5));
    let i = i32(invocation.x);
    let op = i32(params.size.z);
    if op == 6 {
        let size = vec2i(params.output_rect.zw);
        if i >= size.x * size.y { return; }
        let pixel = vec2f(vec2i(i % size.x, i / size.x)) + params.output_rect.xy + vec2f(.5);
        let local = vec2f(dot(params.mapping.xy, pixel), dot(params.mapping.zw, pixel))
                  + params.placement.xy - params.placement.zw - vec2f(.5);
        let corner = vec2i(floor(local));
        let fraction = fract(local);
        var a = 0.;
        var premul = vec4f(0.);
        for (var y = 0; y < 2; y++) {
            for (var x = 0; x < 2; x++) {
                let weight = select(1.-fraction.x, fraction.x, x == 1)
                           * select(1.-fraction.y, fraction.y, y == 1);
                let p = corner + vec2i(x, y);
                let covered = alpha(p) * weight;
                a += covered;
                premul += ink(p) * covered;
            }
        }
        let values = premul / max(a, 0.000001);
        for (var c = 0; c < 4; c++) { output[5*i+c] = values[c]; }
        output[5*i+4] = a;
        return;
    }
    if i >= width * height { return; }
    let p = vec2i(i % width, i / width);
    let n = 5 * i;
    let radius = i32(params.size.w);
    if op == 13 {
        for(var c=0;c<5;c++) { output[n+c]=0.; } return;
    }
    if op == 12 {
        let pixel=style_sample(0,p,vec2f(0.));
        for(var c=0;c<4;c++) { output[n+c]=pixel.color[c]; }
        output[n+4]=pixel.alpha; return;
    }
    if op >= 7 { extended_native(p,n,op); return; }
    if op == 1 {
        let level = i32(params.options.y);
        var value = alpha(p);
        if level > 0 {
            let right = maximum(level - 1, p + vec2i(1 << u32(level - 1), 0));
            let left = maximum(level - 1,p);
            value = select(max(left,right),min(left,right),params.options.x > .5);
        }
        ranges[level * width * height + i] = value;
        return;
    }
    if op == 5 {
        let crop = vec4i(params.crop);
        if p.x < crop.z && p.y < crop.w {
            let src = 5 * index(p + crop.xy);
            let dst = 5 * (p.y * crop.z + p.x);
            for (var c = 0; c < 5; c++) { output[dst+c] = source[src+c]; }
        }
        return;
    }
    if op == 2 || op == 3 {
        var value = select(0.,1.,op == 2 && params.options.x > .5);
        if op == 2 {
            for (var dy = -radius; dy <= radius; dy++) {
                let dx = i32(floor(sqrt(f32(radius*radius-dy*dy))));
                let length = 2 * dx + 1;
                let level = i32(floor(log2(f32(length))));
                let q = p + vec2i(-dx, dy);
                let right = maximum(level, q + vec2i(length - (1 << u32(level)), 0));
                let left = maximum(level,q);
                value = select(max(value,max(left,right)),min(value,min(left,right)),
                               params.options.x > .5);
            }
        } else {
            for (var t = -radius; t <= radius; t++) {
                value += alpha(p - vec2i(params.geometry.zw) + vec2i(params.geometry.xy) * t);
            }
            value /= f32(2 * radius + 1);
        }
        for (var c = 0; c < 4; c++) { output[n+c] = 0.; }
        output[n+4] = value;
        return;
    }
    let mode = i32(params.options.x);
    var pixel=style_sample(0,p,vec2f(0.));
    if params.blending.z>.5 {
        pixel=StylePixel(vec4f(source[n],source[n+1],source[n+2],source[n+3]),source[n+4]);
    }
    var ink = pixel.color;
    let old = vec4f(previous[n],previous[n+1],previous[n+2],previous[n+3]);
    var sa = pixel.alpha;
    let da = previous[n+4];
    if mode == 1 {
        ink = mix(old, style_atop(params.color,old,da,n), params.options.z);
        sa = da;
    } else if mode == 4 {
        let amount=(1.-style_sample(1,p,vec2f(0.)).alpha)*params.options.z;
        ink = mix(old, style_atop(params.color,old,da,n), amount);
        sa = da;
    } else {
        if mode >= 2 {
            ink = params.color;
            sa = style_sample(1,p,vec2f(0.)).alpha;
            if mode == 3 { sa = max(0., sa - pixel.alpha); }
            sa *= params.options.z;
        }
        if mode == 0 {
            let a=sa+da*(1.-sa);
            ink=(ink*sa+old*da*(1.-sa))/max(a,.000001); sa=a;
        } else {
            let result=backdrop_over(old,da,ink,sa,n);
            ink=result.color; sa=result.alpha;
        }
    }
    for (var c = 0; c < 4; c++) { output[n+c] = ink[c]; }
    output[n+4] = sa;
}
"""
    + STYLE_MATH_WGSL.replace("STYLE_RADIAL", "params.options.x > .5")
    + EXTENDED_NATIVE_WGSL
    + NATIVE_STYLE_BLEND
    + NATIVE_BACKDROP_WGSL
)
