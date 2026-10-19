"""Additional RGB/native kernels with shared gradient and relief mathematics."""

from .relief_style_shader import RELIEF_WGSL

STYLE_MATH_WGSL = """
fn style_weight(radius: i32, offset: i32) -> f32 {
    let sigma = max(f32(radius) / 3., 1. / 3.);
    let x = f32(offset) / sigma;
    return exp(-0.5 * x * x);
}
fn style_gradient(p: vec2i) -> f32 {
    let uv = (style_local(p) + params.coordinates.xy + vec2f(.5)) / params.coordinates.zw
             - params.style.zw;
    if STYLE_RADIAL { return clamp(length(uv) * 2. / params.style.y, 0., 1.); }
    let axis = vec2f(cos(params.style.x), sin(params.style.x));
    return clamp(dot(uv, axis) / params.style.y + .5, 0., 1.);
}
""" + RELIEF_WGSL

EXTENDED_RGB_WGSL = """
@group(0) @binding(12) var coverage: texture_2d<f32>;
fn style_height_offset(p: vec2i,offset: vec2f) -> f32 {
    var q=vec2f(p);
    if params.blending.y>.5 {
        let pixel=vec3f(vec2f(p)+vec2f(.5),1.);
        q=vec2f(dot(params.sample_x.xyz,pixel),dot(params.sample_y.xyz,pixel));
    }
    let at=vec2i(floor(q+offset+vec2f(.5)));
    if any(at<vec2i(0)) || any(at>=vec2i(textureDimensions(mask))) {return 0.;}
    return textureLoad(mask,at,0).r;
}
fn style_reflectance(color: vec4f) -> vec4f { return color; }
fn style_light_color(base: vec4f, light: vec4f) -> vec4f { return base*light; }
fn extended_rgb(p: vec2i, base: vec4f) -> vec4f {
    let op = i32(params.operation.x);
    let radius = i32(params.operation.y);
    if op == 7 || op == 10 {
        var value = vec4f(0.);
        var weight = 0.;
        for (var i = -radius; i <= radius; i++) {
            let q = p + vec2i(params.geometry.xy) * i;
            let pixel = load(source, q);
            let w = style_weight(radius, i);
            if op == 10 { value.r += pixel.r * w; }
            else { value += vec4f(pixel.rgb*pixel.a, pixel.a) * w; }
            weight += w;
        }
        value /= weight;
        if op == 10 { return vec4f(value.r); }
        return vec4f(value.rgb / max(value.a, .000001), value.a);
    }
    var old = load(previous, p);
    if op == 8 || op == 14 {
        let a = mix(old.a, base.a, params.color.a);
        var color=mix(base.rgb,style_color(base.rgb,old.rgb),old.a);
        if op == 14 {
            let world=style_destination(old,p);
            color=mix(base.rgb,style_color(base.rgb,world.rgb),world.a);
        }
        let premul = mix(old.rgb*old.a, color*base.a, params.color.a);
        return vec4f(premul / max(a,.000001), a);
    }
    if op == 9 {
        let t = style_gradient(p);
        let a = mix(params.lighting.x, params.lighting.y, t);
        let color = mix(params.color.rgb*params.lighting.x,
                        params.secondary.rgb*params.lighting.y, t);
        let blended=style_atop(color/max(a,.000001),old,p);
        return vec4f(mix(old.rgb,blended,a*params.color.a), old.a);
    }
    let relief = style_relief(p, base.a);
    let weights = relief.weights;
    let opacity = params.style.w;
    if params.geometry.x > .5 && params.material.z < .5 {
        let ring = max(0., style_sample(coverage,p).r - base.a);
        let t = clamp(.5 + (weights.x-weights.y)*.5, 0., 1.);
        let lit_color = mix(params.color.rgb,relief.highlight.rgb,clamp(weights.x,0.,1.));
        let color = mix(params.secondary.rgb, lit_color, t);
        let a = ring * opacity * mix(params.lighting.y, params.lighting.x, t);
        let world=style_destination(old,p);
        old = over(vec4f(mix(color,style_color(color,world.rgb),world.a),a),old);
        if params.geometry.x < 1.5 { return old; }
    }
    let shadow = clamp(weights.y * opacity * params.lighting.y, 0., 1.);
    let highlight = clamp(weights.x * opacity * params.lighting.x, 0., 1.);
    let tinted = mix(old.rgb,style_atop(old.rgb*relief.tint.rgb,old,p),opacity);
    let shaded = mix(tinted, style_atop(params.secondary.rgb,vec4f(tinted,old.a),p), shadow);
    let lit=style_atop(relief.highlight.rgb,vec4f(shaded,old.a),p);
    return vec4f(mix(shaded,lit,highlight),old.a);
}
"""

EXTENDED_NATIVE_WGSL = """
@group(0) @binding(6) var<storage, read> coverage: array<f32>;
fn style_reflectance(color: vec4f) -> vec4f { return vec4f(1.)-color; }
fn style_light_color(base: vec4f, light: vec4f) -> vec4f {
    return vec4f(1.)-(vec4f(1.)-base)*(vec4f(1.)-light);
}
fn style_height_offset(p: vec2i,offset: vec2f) -> f32 {
    return style_sample(1,p,offset).alpha;
}
fn extended_native(p: vec2i, n: i32, op: i32) {
    let radius = i32(params.size.w);
    var color = vec4f(0.);
    var a = 0.;
    if op == 7 || op == 10 {
        var weight = 0.;
        for (var t = -radius; t <= radius; t++) {
            let q = p + vec2i(params.geometry.xy)*t;
            let w = style_weight(radius, t);
            let sa = alpha(q);
            a += sa*w;
            if op == 7 { color += ink(q)*sa*w; }
            weight += w;
        }
        a /= weight;
        color /= max(a*weight,.000001);
    } else {
        let old = vec4f(previous[n],previous[n+1],previous[n+2],previous[n+3]);
        a = previous[n+4];
        if op == 8 || op == 14 {
            let pixel=style_sample(0,p,vec2f(0.));
            let sa = pixel.alpha;
            let opacity = params.options.z;
            let world=style_destination(old,a,n);
            let blended=mix(pixel.color,style_color(pixel.color,world.color),world.alpha);
            color = mix(old*a, blended*sa, opacity);
            a = mix(a,sa,opacity);
            color /= max(a,.000001);
        } else if op == 9 {
            let t = style_gradient(p);
            let alpha = mix(params.lighting.x, params.lighting.y, t);
            let gradient = mix(params.color*params.lighting.x,
                               params.secondary*params.lighting.y, t);
            color = mix(old,style_atop(gradient/max(alpha,.000001),old,a,n),alpha*params.options.z);
        } else {
            let base=style_sample(0,p,vec2f(0.));
            let relief = style_relief(p,base.alpha);
            let weights = relief.weights;
            let opacity = params.style.w;
            color = old;
            if params.geometry.x > .5 && params.material.z < .5 {
                let ring = max(0., style_sample(2,p,vec2f(0.)).alpha - base.alpha);
                let t = clamp(.5+(weights.x-weights.y)*.5,0.,1.);
                let lit_color=mix(params.color,relief.highlight,clamp(weights.x,0.,1.));
                let raw = mix(params.secondary,lit_color,t);
                let ua = ring*opacity*mix(params.lighting.y,params.lighting.x,t);
                let world=style_destination(old,a,n);
                let under=mix(raw,style_color(raw,world.color),world.alpha);
                let total = a + ua*(1.-a);
                color = (old*a + under*ua*(1.-a))/max(total,.000001);
                a = total;
            }
            if !(params.geometry.x > .5 && params.geometry.x < 1.5) || params.material.z > .5 {
                let shadow = clamp(weights.y*opacity*params.lighting.y,0.,1.);
                let highlight = clamp(weights.x*opacity*params.lighting.x,0.,1.);
                let raw=vec4f(1.)-(vec4f(1.)-color)*relief.tint;
                let tinted=mix(color,style_atop(raw,color,a,n),opacity);
                let shaded=mix(tinted,style_atop(params.secondary,tinted,a,n),shadow);
                color = mix(shaded,style_atop(relief.highlight,shaded,a,n),highlight);
            }
        }
    }
    for (var c=0; c<4; c++) { output[n+c] = color[c]; }
    output[n+4] = a;
}
"""
