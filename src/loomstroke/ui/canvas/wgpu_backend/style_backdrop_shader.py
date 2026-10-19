"""Separate the styled foreground from the backdrop used by blend functions."""

STYLE_LOCAL_WGSL = """
fn style_local(p: vec2i) -> vec2f {
    if params.blending.y < .5 { return vec2f(p); }
    let pixel=vec3f(vec2f(p)+vec2f(.5),1.);
    return vec2f(dot(params.sample_x.xyz,pixel),dot(params.sample_y.xyz,pixel));
}
"""

RGB_BACKDROP_WGSL = STYLE_LOCAL_WGSL + """
@group(0) @binding(13) var backdrop: texture_2d<f32>;
@group(0) @binding(14) var underlay: texture_2d<f32>;
var<private> style_bg: vec4f;
fn style_sample(t: texture_2d<f32>, p: vec2i) -> vec4f {
    return load(t,vec2i(floor(style_local(p)+vec2f(.5))));
}
fn style_prepare(p: vec2i) {
    style_bg=vec4f(0.);
    if params.blending.y < .5 { return; }
    let raw=textureLoad(backdrop,p,0);
    let below=vec4f(raw.rgb/max(raw.a,.000001),raw.a);
    style_bg=below;
    if params.document_info.w < .5 { return; }
    let raw_under=textureLoad(underlay,p,0);
    let straight=vec4f(raw_under.rgb/max(raw_under.a,.000001),raw_under.a);
    style_bg=over(below,select(raw_under,straight,params.document_info.w>1.5));
}
fn style_destination(old: vec4f,p: vec2i) -> vec4f {
    let a=old.a+style_bg.a*(1.-old.a);
    return vec4f((old.rgb*old.a+style_bg.rgb*style_bg.a*(1.-old.a))/max(a,.000001),a);
}
fn style_atop(color: vec3f,old: vec4f,p: vec2i) -> vec3f {
    let a=old.a+style_bg.a*(1.-old.a);
    let d=(old.rgb*old.a+style_bg.rgb*style_bg.a*(1.-old.a))/max(a,.000001);
    return style_color(color,d);
}
fn backdrop_over(old: vec4f,above: vec4f,p: vec2i) -> vec4f {
    let world=style_destination(old,p);
    let a=above.a+old.a*(1.-above.a);
    let color=mix(above.rgb,style_color(above.rgb,world.rgb),world.a);
    return vec4f((color*above.a+old.rgb*old.a*(1.-above.a))/max(a,.000001),a);
}
"""

NATIVE_BACKDROP_WGSL = """
fn style_local(p: vec2i) -> vec2f {
    if params.blending.y < .5 { return vec2f(p); }
    let pixel=vec2f(p)+params.output_rect.xy+vec2f(.5);
    return vec2f(dot(params.mapping.xy,pixel),dot(params.mapping.zw,pixel))
           +params.placement.xy-params.placement.zw-vec2f(.5);
}
@group(0) @binding(7) var<storage,read> backdrop: array<f32>;
@group(0) @binding(8) var<storage,read> underlay: array<f32>;
struct StylePixel { color: vec4f, alpha: f32 };
fn style_raw(which: i32,p: vec2i) -> StylePixel {
    if !valid(p) { return StylePixel(vec4f(0.),0.); }
    let n=5*index(p);
    if which==1 { return StylePixel(vec4f(0.),mask[n+4]); }
    if which==2 { return StylePixel(vec4f(0.),coverage[n+4]); }
    return StylePixel(ink(p),alpha(p));
}
fn style_sample(which: i32,p: vec2i,offset: vec2f) -> StylePixel {
    let q=style_local(p)+offset;
    let corner=vec2i(floor(q)); let fraction=fract(q);
    var color=vec4f(0.); var a=0.;
    for (var y=0;y<2;y++) { for(var x=0;x<2;x++) {
        let w=select(1.-fraction.x,fraction.x,x==1)*select(1.-fraction.y,fraction.y,y==1);
        let pixel=style_raw(which,corner+vec2i(x,y));
        a+=pixel.alpha*w; color+=pixel.color*pixel.alpha*w;
    }}
    return StylePixel(color/max(a,.000001),a);
}
fn style_background(n: i32) -> StylePixel {
    if params.blending.y < .5 { return StylePixel(vec4f(0.),0.); }
    let color=vec4f(backdrop[n],backdrop[n+1],backdrop[n+2],backdrop[n+3]);
    let a=backdrop[n+4];
    if params.document_info.w < .5 { return StylePixel(color,a); }
    let ua=underlay[n+4]; let total=ua+a*(1.-ua);
    let uc=vec4f(underlay[n],underlay[n+1],underlay[n+2],underlay[n+3]);
    return StylePixel((uc*ua+color*a*(1.-ua))/max(total,.000001),total);
}
fn style_destination(old: vec4f,da: f32,n: i32) -> StylePixel {
    let bg=style_background(n); let a=da+bg.alpha*(1.-da);
    return StylePixel((old*da+bg.color*bg.alpha*(1.-da))/max(a,.000001),a);
}
fn style_atop(color: vec4f,old: vec4f,da: f32,n: i32) -> vec4f {
    return style_color(color,style_destination(old,da,n).color);
}
fn backdrop_over(old: vec4f,da: f32,color: vec4f,sa: f32,n: i32) -> StylePixel {
    let world=style_destination(old,da,n);
    let a=sa+da*(1.-sa);
    let blended=mix(color,style_color(color,world.color),world.alpha);
    return StylePixel((blended*sa+old*da*(1.-sa))/max(a,.000001),a);
}
"""
